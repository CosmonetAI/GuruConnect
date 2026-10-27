import hashlib
import json
import logging
import re
import secrets
from datetime import datetime
from uuid import UUID
from urllib.parse import quote

import httpx

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from psycopg.types.json import Jsonb

from . import schemas as s
from .db import many, one
from .errors import fail
from .security import User, Workspace, administrator, conversation, current_user, token_hash, workspace

router = APIRouter(prefix="/api/v1")
log = logging.getLogger("recorder.api")


async def audit(connection, context: Workspace, action: str, metadata: dict | None = None, conversation_id=None):
    await connection.execute("insert into audit_events(organisation_id,conversation_id,actor_id,action,metadata) values(%s,%s,%s,%s,%s)", (context.id, conversation_id, context.user.id, action, Jsonb(metadata or {})))


@router.get("/profile")
async def get_profile(request: Request, user: User = Depends(current_user)):
    async with request.app.state.db.connection() as connection:
        await connection.execute("""
            update profiles
            set availability_status='active', leave_end_date=null, updated_at=now()
            where id=%s and availability_status='on_leave' and leave_end_date < current_date
        """, (user.id,))
        row = await one(connection, "select * from profiles where id=%s", (user.id,))
    return row or {"id": user.id, "display_name": user.name, "preferences": {}}


@router.patch("/profile")
async def update_profile(body: s.ProfilePatch, request: Request, user: User = Depends(current_user)):
    if len(str(body.preferences)) > 5000 or any(key not in {"theme", "microphone_id", "locale"} for key in body.preferences):
        fail("invalid_preferences")
    async with request.app.state.db.connection() as connection:
        if body.phone is not None and body.phone and not re.fullmatch(r"\+?[0-9][0-9 ()-]{7,19}", body.phone):
            fail("invalid_phone", 422)
        if body.avatar_path is not None and body.avatar_path and not body.avatar_path.startswith(f"{user.id}/"):
            fail("invalid_avatar", 422)
        return await one(connection, "insert into profiles(id,display_name,preferences,phone,avatar_path,availability_status,leave_end_date,last_seen_at) values(%s,%s,%s,%s,%s,%s,%s,now()) on conflict(id) do update set display_name=excluded.display_name,preferences=excluded.preferences,phone=excluded.phone,avatar_path=excluded.avatar_path,availability_status=excluded.availability_status,leave_end_date=excluded.leave_end_date,updated_at=now() returning *", (user.id, body.display_name, Jsonb(body.preferences), body.phone, body.avatar_path, body.availability_status, body.leave_end_date))

@router.post("/presence")
async def presence(request: Request, user: User = Depends(current_user)):
    async with request.app.state.db.connection() as connection:
        row = await one(connection, """
            update profiles
            set availability_status='active', leave_end_date=null, last_seen_at=now(), updated_at=now()
            where id=%s and availability_status='on_leave' and leave_end_date < current_date
            returning id,last_seen_at,availability_status
        """, (user.id,))
        if not row:
            row = await one(connection, "update profiles set last_seen_at=now(), updated_at=now() where id=%s returning id,last_seen_at,availability_status", (user.id,))
    return row or {"id": user.id, "availability_status": "active"}


@router.get("/analytics")
async def analytics(request: Request, context: Workspace = Depends(workspace)):
    """Return workspace totals for owners and member totals for salespeople.

    Authorization is always checked against the authenticated actor, never a
    client-supplied member identity.
    """
    scoped_all = context.admin
    scope_user = context.member_id
    async with request.app.state.db.connection() as connection:
        total = await one(connection, """
            select count(*) as total
            from guruconnect_enquiries
            where organisation_id=%s and (%s or owner_id=%s or assigned_to=%s)
        """, (context.id, scoped_all, scope_user, scope_user))
        channels = await many(connection, """
            select channel, count(*) as count
            from guruconnect_enquiries
            where organisation_id=%s and (%s or owner_id=%s or assigned_to=%s)
            group by channel order by count desc, channel
        """, (context.id, scoped_all, scope_user, scope_user))
        stages = await many(connection, """
            select stage, count(*) as count
            from guruconnect_enquiries
            where organisation_id=%s and (%s or owner_id=%s or assigned_to=%s)
            group by stage
            order by case stage when 'new' then 0 when 'in_process' then 1 when 'closed' then 2 else 3 end
        """, (context.id, scoped_all, scope_user, scope_user))
        latest = await many(connection, """
            select e.id, e.customer_name as "customerName", e.created_at as "createdAt",
                   e.stage, e.channel, e.branch, e.assigned_to as "assignedTo",
                   coalesce(p.display_name, e.payload->>'assignedExecutive', 'Awaiting assignment') as "assignedName"
            from guruconnect_enquiries e
            left join public.profiles p on p.id=e.assigned_to
            where e.organisation_id=%s and (%s or e.owner_id=%s or e.assigned_to=%s)
            order by e.created_at desc, e.id desc
            limit 8
        """, (context.id, scoped_all, scope_user, scope_user))
    return {
        "total": total["total"],
        "channels": channels,
        "stages": stages,
        "latestVisitors": [
            {**item, "id": str(item["id"]), "createdAt": item["createdAt"].isoformat()}
            for item in latest
        ],
        "organizationWide": scoped_all,
    }


@router.get("/workspaces")
async def list_workspaces(request: Request, user: User = Depends(current_user)):
    async with request.app.state.db.connection() as connection:
        rows = await many(connection, """
            select o.id, o.name, m.user_id as member_id, m.role, p.display_name
            from organisations o
            join members actor on actor.organisation_id=o.id and actor.user_id=%s and actor.status='active'
            join members m on m.organisation_id=o.id and m.status='active'
            left join profiles p on p.id=m.user_id
            where actor.role='owner' or m.user_id=%s
            order by o.created_at, case when m.user_id=%s then 0 else 1 end, p.display_name
        """, (user.id, user.id, user.id))
    return {"items": rows, "next_cursor": None}


@router.post("/workspaces", status_code=201)
async def create_workspace(body: s.WorkspaceCreate, request: Request, user: User = Depends(current_user)):
    async with request.app.state.db.connection() as connection:
        await connection.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", (str(user.id),))
        count = await one(connection, "select count(*) as count from members where user_id=%s and role='owner' and status='active'", (user.id,))
        if count["count"] >= 10:
            fail("workspace_limit_reached", 409)
        created = await one(connection, "select create_workspace(%s,%s) as id", (user.id, body.name))
        from .provider_bootstrap import ensure_managed_providers
        await ensure_managed_providers(connection, created["id"], request.app.state.settings)
        row = await one(connection, "select * from organisations where id=%s", (created["id"],))
        return {**row, "role": "owner"}


@router.get("/team")
async def team(request: Request, context: Workspace = Depends(administrator)):
    async with request.app.state.db.connection() as connection:
        await connection.execute("""
            update profiles p
            set availability_status='active', leave_end_date=null, updated_at=now()
            where p.availability_status='on_leave' and p.leave_end_date < current_date
              and exists (select 1 from members m where m.organisation_id=%s and m.user_id=p.id and m.status='active')
        """, (context.id,))
        rows = await many(connection, "select m.*,p.display_name,p.availability_status,p.last_seen_at,u.email,case when m.status <> 'active' then m.status when u.confirmed_at is null then 'invited' else 'active' end as status from members m left join profiles p on p.id=m.user_id join auth.users u on u.id=m.user_id where m.organisation_id=%s order by m.created_at", (context.id,))
        invitations = await many(connection, "select id,email,role,expires_at,accepted_at,revoked_at from invites where organisation_id=%s order by created_at desc limit 100", (context.id,))
    return {"items": rows, "invites": invitations, "next_cursor": None}


@router.post("/invites", status_code=201)
async def invite(body: s.Invitation, request: Request, context: Workspace = Depends(administrator)):
    settings = request.app.state.settings
    if not settings.supabase_url or not settings.supabase_service_role_key:
        fail("auth_not_configured", 503, "Supabase admin authentication is not configured")

    # Supabase creates the account and sends the recipient a one-time invite
    # link. The link lands on /reset, where the recipient sets their password.
    # Membership is provisioned here, so no invitation token or second accept
    # step is needed after the recipient follows the email.
    origin = request.headers.get("origin")
    redirect_origin = origin if origin in settings.cors_origins else settings.cors_origins[0]
    invite_payload = {"email": body.email.lower(), "data": {"display_name": body.name.strip()}}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{settings.supabase_url}/auth/v1/invite?redirect_to={quote(f'{redirect_origin}/reset', safe='')}" ,
                headers={
                    "Authorization": f"Bearer {settings.supabase_service_role_key.get_secret_value()}",
                    "apikey": settings.supabase_service_role_key.get_secret_value(),
                    "Content-Type": "application/json",
                },
                json=invite_payload,
            )
        if response.status_code >= 400:
            # Supabase's error body can contain provider details, but it must
            # never be returned to the browser because it may include sensitive
            # Auth internals. Keep only stable diagnostic fields in Render logs.
            provider_error = {}
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    provider_error = {key: payload.get(key) for key in ("code", "error_code", "msg", "message") if payload.get(key)}
            except ValueError:
                pass
            log.warning(
                "supabase_invite_failed request_id=%s status=%s provider_error=%s",
                getattr(request.state, "request_id", "unavailable"), response.status_code, provider_error or "unstructured_response",
            )
            if response.status_code in {400, 422}:
                fail("member_invitation_failed", 409, "This email may already have an account or could not be invited")
            fail("member_invitation_unavailable", 503, "The invitation email could not be sent")
        auth_user = response.json()
        invited_user_id = auth_user.get("id")
        if not invited_user_id:
            fail("member_invitation_unavailable", 503, "Supabase did not return the invited member")
    except httpx.HTTPError as exc:
        log.warning(
            "supabase_invite_transport_failed request_id=%s exception_type=%s",
            getattr(request.state, "request_id", "unavailable"), type(exc).__name__,
        )
        fail("member_invitation_unavailable", 503, "The invitation email could not be sent")

    token = secrets.token_urlsafe(32)  # retained only as a non-user-visible audit value
    async with request.app.state.db.connection() as connection:
        if await one(connection, "select 1 from members where organisation_id=%s and user_id=%s", (context.id, invited_user_id)):
            fail("membership_exists", 409, "This person is already a member of the workspace")
        row = await one(connection, "insert into invites(organisation_id,email,role,token_hash,invited_by,accepted_by,accepted_at,expires_at) values(%s,%s,%s,%s,%s,%s,now(),now()+interval '7 days') returning id,email,role,expires_at", (context.id, body.email.lower(), body.role, token_hash(token), context.user.id, invited_user_id))
        await connection.execute("insert into members(organisation_id,user_id,role) values(%s,%s,%s)", (context.id, invited_user_id, body.role))
        await audit(connection, context, "invitation.created", {"invite_id": str(row["id"]), "role": body.role, "user_id": str(invited_user_id)})
    return {**row, "status": "sent"}


@router.post("/invites/accept")
async def accept_invite(body: s.AcceptInvitation, request: Request, user: User = Depends(current_user)):
    async with request.app.state.db.connection() as connection:
        accepted = await one(connection, "select accept_invitation(%s,%s) as organisation_id", (user.id, token_hash(body.token)))
        member = await one(connection, "select role from members where organisation_id=%s and user_id=%s and status='active'", (accepted["organisation_id"], user.id))
    return {"organisation_id": accepted["organisation_id"], "role": member["role"]}


@router.delete("/team/{user_id}")
async def revoke_member(user_id: UUID, request: Request, context: Workspace = Depends(administrator)):
    async with request.app.state.db.connection() as connection:
        member = await one(connection, "select * from members where organisation_id=%s and user_id=%s for update", (context.id, user_id))
        if not member:
            fail("member_not_found", 404)
        if member["role"] == "owner" or member["user_id"] == context.user.id or (member["role"] == "admin" and context.role != "owner"):
            fail("membership_change_denied", 403)
        await connection.execute("update members set status='revoked',revoked_at=now() where organisation_id=%s and user_id=%s", (context.id, user_id))
        await connection.execute("delete from recording_leases where organisation_id=%s and user_id=%s", (context.id, user_id))
        await audit(connection, context, "membership.revoked", {"user_id": str(user_id)})
    return {"revoked": True}


@router.delete("/team/{user_id}/delete")
async def delete_member(user_id: UUID, request: Request, context: Workspace = Depends(administrator)):
    """Permanently remove a non-owner member and member-scoped data."""
    if context.role != "owner":
        fail("membership_change_denied", 403)
    settings = request.app.state.settings
    if not settings.supabase_url or not settings.supabase_service_role_key:
        fail("auth_not_configured", 503, "Supabase admin authentication is not configured")
    if user_id == context.user.id:
        fail("membership_change_denied", 403, "The workspace owner cannot delete their own account here")

    owner_id = context.user.id
    async with request.app.state.db.connection() as connection:
        member = await one(connection, "select role from members where organisation_id=%s and user_id=%s for update", (context.id, user_id))
        if not member:
            fail("member_not_found", 404)
        if member["role"] == "owner":
            fail("membership_change_denied", 403)
        if await one(connection, "select 1 from members where user_id=%s and organisation_id<>%s limit 1", (user_id, context.id)):
            fail("member_in_other_workspace", 409, "Remove this member from their other workspaces before deleting their Auth account")
        if await one(connection, "select 1 from conversations where organisation_id=%s and owner_id=%s limit 1", (context.id, user_id)):
            fail("member_has_workspace_data", 409, "Move or remove this member's conversations before deleting the account")

        # Delete the member's Zoho credentials and all Zoho pipeline cache data.
        await connection.execute("delete from zoho_pipeline_records where organisation_id=%s and member_id=%s", (context.id, user_id))
        await connection.execute("delete from zoho_pipeline_sync where organisation_id=%s and member_id=%s", (context.id, user_id))
        await connection.execute("delete from zoho_connections where organisation_id=%s and member_id=%s", (context.id, user_id))

        # Preserve ordinary workspace history under the owner while removing all
        # foreign-key references that would block Auth account deletion.
        await connection.execute("delete from recording_leases where organisation_id=%s and user_id=%s", (context.id, user_id))
        await connection.execute("delete from guruconnect_walkin_agents where organisation_id=%s and user_id=%s", (context.id, user_id))
        await connection.execute("update guruconnect_walkin_visits set registered_by=%s where organisation_id=%s and registered_by=%s", (owner_id, context.id, user_id))
        await connection.execute("update guruconnect_walkin_visits set assigned_agent_id=null, assigned_at=null, status='Waiting for Assignment' where organisation_id=%s and assigned_agent_id=%s", (context.id, user_id))
        await connection.execute("update guruconnect_walkin_history set actor_id=%s where organisation_id=%s and actor_id=%s", (owner_id, context.id, user_id))
        await connection.execute("update guruconnect_walkin_recordings set owner_id=%s where organisation_id=%s and owner_id=%s", (owner_id, context.id, user_id))
        await connection.execute("update guruconnect_enquiries set owner_id=%s, assigned_to=case when assigned_to=%s then %s else assigned_to end where organisation_id=%s and owner_id=%s", (owner_id, user_id, owner_id, context.id, user_id))
        await connection.execute("update customers set owner_id=%s, member_id=case when member_id=%s then %s else member_id end where organisation_id=%s and owner_id=%s", (owner_id, user_id, owner_id, context.id, user_id))
        await connection.execute("update chat_sessions set member_id=%s where organisation_id=%s and member_id=%s", (owner_id, context.id, user_id))
        await connection.execute("update conversations set owner_id=%s where organisation_id=%s and owner_id=%s", (owner_id, context.id, user_id))
        await connection.execute("update recorder_private.conversation_requests set owner_id=%s where organisation_id=%s and owner_id=%s", (owner_id, context.id, user_id))
        await connection.execute("update speaker_assignments set assigned_by=%s where assigned_by=%s", (owner_id, user_id))
        await connection.execute("update transcript_revisions set actor_id=%s where actor_id=%s", (owner_id, user_id))
        await connection.execute("update comparisons set requested_by=%s where requested_by=%s", (owner_id, user_id))
        await connection.execute("update audit_events set actor_id=%s where organisation_id=%s and actor_id=%s", (owner_id, context.id, user_id))
        await connection.execute("update invites set invited_by=%s where organisation_id=%s and invited_by=%s", (owner_id, context.id, user_id))
        await connection.execute("update invites set accepted_by=null where organisation_id=%s and accepted_by=%s", (context.id, user_id))
        await connection.execute("delete from members where organisation_id=%s and user_id=%s", (context.id, user_id))
        await audit(connection, context, "membership.deleted", {"user_id": str(user_id)})

    key = settings.supabase_service_role_key.get_secret_value()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.delete(f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users/{user_id}", headers={"Authorization": f"Bearer {key}", "apikey": key})
        if response.status_code not in {200, 204, 404}:
            log.error("supabase_user_delete_failed request_id=%s status=%s user_id=%s", getattr(request.state, "request_id", "unavailable"), response.status_code, user_id)
            fail("member_deletion_incomplete", 503, "Workspace data was removed, but the Auth account could not be deleted")
    except httpx.HTTPError:
        log.error("supabase_user_delete_transport_failed request_id=%s user_id=%s", getattr(request.state, "request_id", "unavailable"), user_id)
        fail("member_deletion_incomplete", 503, "Workspace data was removed, but the Auth account could not be deleted")
    return {"deleted": True, "user_id": str(user_id)}


@router.get("/customers")
async def customers(request: Request, context: Workspace = Depends(workspace), q: str = Query(default="", max_length=120), limit: int = Query(default=50, ge=1, le=100), cursor: UUID | None = None):
    async with request.app.state.db.connection() as connection:
        rows = await many(connection, "select * from customers where organisation_id=%s and deleted_at is null and owner_id=%s and name ilike %s and (%s::uuid is null or id>%s) order by id limit %s", (context.id, context.member_id, "%" + q.replace("%", "\\%").replace("_", "\\_") + "%", cursor, cursor, limit + 1))
    return {"items": rows[:limit], "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None}

@router.get("/enquiries/unread-count")
async def unread_enquiries(request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        row = await one(connection, "select count(*) as count from guruconnect_enquiries where organisation_id=%s and unread and (%s or assigned_to=%s)", (context.id, context.admin, context.user.id))
    return {"count": row["count"]}

@router.patch("/enquiries/{enquiry_id}/state")
async def update_enquiry_state(enquiry_id: UUID, body: dict, request: Request, context: Workspace = Depends(workspace)):
    stage = body.get("stage")
    if stage not in {"new", "in_process", "closed", "customer_did_not_show"}: fail("invalid_enquiry_stage", 422)
    async with request.app.state.db.connection() as connection:
        row = await one(connection, "update guruconnect_enquiries set stage=%s, unread=false where id=%s and organisation_id=%s and (%s or assigned_to=%s) returning *", (stage, enquiry_id, context.id, context.admin, context.user.id))
        if not row: fail("enquiry_not_found", 404)
    return row


@router.post("/customers", status_code=201)
async def create_customer(body: s.CustomerCreate, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        return await one(connection, "insert into customers(organisation_id,owner_id,name,is_unnamed) values(%s,%s,%s,%s) returning *", (context.id, context.user.id, "Name not provided" if body.is_unnamed else body.name, body.is_unnamed))


@router.patch("/customers/{customer_id}")
async def update_customer(customer_id: UUID, body: s.CustomerCreate, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        row = await one(connection, "update customers set name=%s,is_unnamed=%s,updated_at=now() where id=%s and organisation_id=%s and deleted_at is null and owner_id=%s returning *", ("Name not provided" if body.is_unnamed else body.name, body.is_unnamed, customer_id, context.id, context.member_id))
        if not row:
            fail("customer_not_found", 404)
        await audit(connection, context, "customer.updated", {"customer_id": str(customer_id)})
        return row


@router.get("/customers/{customer_id}/conversations")
async def customer_history(customer_id: UUID, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        customer = await one(connection, "select id from customers where id=%s and organisation_id=%s and deleted_at is null and owner_id=%s", (customer_id, context.id, context.member_id))
        if not customer:
            fail("customer_not_found", 404)
        rows = await many(connection, "select * from conversations where customer_id=%s and organisation_id=%s and deleted_at is null and owner_id=%s order by created_at desc limit 100", (customer_id, context.id, context.member_id))
    return {"items": rows, "next_cursor": None}


@router.get("/conversations")
async def conversations(request: Request, context: Workspace = Depends(workspace), q: str = Query(default="", max_length=120), state: str | None = Query(default=None, max_length=30), date_from: datetime | None = None, date_to: datetime | None = None, limit: int = Query(default=30, ge=1, le=100), cursor: str | None = Query(default=None, max_length=100)):
    cursor_time, cursor_id = None, None
    if cursor:
        try:
            timestamp, separator, identifier = cursor.partition("|")
            cursor_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            cursor_id = UUID(identifier) if separator else None
        except (ValueError, TypeError):
            fail("invalid_cursor", 400)
    async with request.app.state.db.connection() as connection:
        audio_bucket = "walkin-audio" if context.role == "receptionist" else "session-audio"
        rows = await many(connection, "select c.*, cust.phone as customer_phone, cust.email as customer_email from conversations c left join customers cust on cust.id=c.customer_id and cust.organisation_id=c.organisation_id where c.organisation_id=%s and c.deleted_at is null and c.audio_bucket=%s and c.owner_id=%s and (c.customer_name_snapshot ilike %s or c.title ilike %s or coalesce(cust.phone,'') ilike %s or coalesce(cust.email,'') ilike %s) and (%s::text is not null or (c.upload_state='saved' and c.total_samples>0)) and (%s::text is null or c.capture_state=%s or c.final_state=%s) and (%s::timestamptz is null or c.created_at>=%s) and (%s::timestamptz is null or c.created_at<=%s) and (%s::timestamptz is null or c.created_at<%s or (%s::uuid is not null and c.created_at=%s and c.id<%s)) order by c.created_at desc,c.id desc limit %s", (context.id, audio_bucket, context.member_id, "%"+q+"%", "%"+q+"%", "%"+q+"%", "%"+q+"%", state, state, state, state, date_from, date_from, date_to, date_to, cursor_time, cursor_time, cursor_id, cursor_time, cursor_id, limit+1))
    return {"items": rows[:limit], "next_cursor": f"{rows[limit-1]['created_at'].isoformat()}|{rows[limit-1]['id']}" if len(rows)>limit else None}


async def create_run(connection, context: Workspace, conv: dict, engine_id: str, kind: str, idempotency_key: str, *, allow_blocked: bool = False, settings=None):
    if settings is not None:
        from .provider_bootstrap import ensure_managed_providers
        await ensure_managed_providers(connection, context.id, settings)
    previous = await one(connection, "select * from processing_runs where conversation_id=%s and idempotency_key=%s", (conv["id"], idempotency_key))
    if previous:
        if previous["engine_id"] != engine_id or previous["kind"] != kind:
            fail("idempotency_conflict", 409)
        return previous
    conn = await one(connection, "select * from engine_connections where organisation_id=%s and engine_id=%s for share", (context.id, engine_id))
    ready = conn and conn["state"] == "connected" and conn["tested_at"] is not None
    if not ready and not allow_blocked:
        fail("engine_unavailable", 409, "Connect and test this engine, or deliberately save audio for later processing")
    translator = None
    strategy = "native-english"
    snapshot = dict(conn["config"] if conn else {})
    if engine_id == "elevenlabs":
        translator = await one(connection, "select c.* from workspace_engine_settings s join engine_connections c on c.id=s.translator_connection_id and c.organisation_id=s.organisation_id where s.organisation_id=%s and c.engine_id in ('azure-translator','openai-translator') for share of s,c", (context.id,))
        strategy = "openai" if translator and translator["engine_id"] == "openai-translator" else "azure"
        if translator:
            snapshot["translation"] = {"provider": translator["engine_id"], "config": translator["config"]}
    return await one(connection, "insert into processing_runs(conversation_id,organisation_id,connection_id,connection_generation,engine_id,model_version,config_snapshot,translation_strategy,translator_connection_id,translator_generation,kind,state,idempotency_key,audio_manifest_hash) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *", (conv["id"], context.id, conn["id"] if conn else None, conn["generation"] if conn else None, engine_id, "scribe_v2" if engine_id == "elevenlabs" else "large-v3", Jsonb(snapshot), strategy, translator["id"] if translator else None, translator["generation"] if translator else None, kind, "pending" if ready else "blocked", idempotency_key, conv.get("manifest_hash")))


@router.post("/conversations", status_code=201)
async def new_conversation(body: s.ConversationCreate, request: Request, response: Response,
                           context: Workspace = Depends(workspace),
                           idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if context.member_id != context.user.id:
        fail("personal_workspace_required", 403, "Switch to your own workspace before creating a recording")
    if body.audio_bucket == "walkin-audio" and context.role != "receptionist":
        fail("walkin_recording_role_required", 403, "Only receptionists can create walk-in recordings")
    if body.audio_bucket == "session-audio" and context.role == "receptionist":
        fail("session_recording_role_required", 403, "Receptionists must use walk-in recording")
    if idempotency_key is not None and not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", idempotency_key):
        fail("invalid_idempotency_key", 400, "Use a stable save key of 8–128 letters, digits, dots, underscores, colons or hyphens")
    request_hash = hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    async with request.app.state.db.connection() as connection:
        # Freeze active membership while creating or replaying this request.
        # Revocation obtains an exclusive member lock and cannot race this save.
        member = await one(connection, "select role from members where organisation_id=%s and user_id=%s and status='active' for share", (context.id, context.user.id))
        if not member:
            fail("workspace_access_denied", 403)
        if idempotency_key is not None:
            await connection.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", (f"conversation-create:{context.id}:{context.user.id}:{idempotency_key}",))
            prior = await one(connection, "select r.request_hash,c.* from recorder_private.conversation_requests r join conversations c on c.id=r.conversation_id and c.organisation_id=r.organisation_id and c.owner_id=r.owner_id where r.organisation_id=%s and r.owner_id=%s and r.idempotency_key=%s", (context.id, context.user.id, idempotency_key))
            if prior:
                if prior.pop("request_hash") != request_hash:
                    fail("idempotency_conflict", 409)
                if prior["deleted_at"] is not None:
                    fail("conversation_deleted", 409, "This save already created a conversation that was later deleted; start a new recording explicitly")
                runs = await many(connection, "select * from processing_runs where conversation_id=%s order by created_at", (prior["id"],))
                response.status_code = 200
                return {**prior, "runs": runs}
        from .provider_bootstrap import ensure_managed_providers
        await ensure_managed_providers(connection, context.id, request.app.state.settings)
        settings = await one(connection, "select * from workspace_engine_settings where organisation_id=%s", (context.id,))
        engine_id = body.engine_id or settings["default_engine_id"]
        if not engine_id and not body.audio_only:
            fail("no_active_engine", 409, "Select a connected engine or deliberately record audio for later processing")
        if body.engine_id and not context.admin and (not settings["salesperson_engine_selection"] or body.engine_id not in settings["allowed_engine_ids"]):
            fail("engine_selection_not_allowed", 403)
        if body.customer_id:
            customer = await one(connection, "select * from customers where id=%s and organisation_id=%s and owner_id=%s and deleted_at is null", (body.customer_id, context.id, context.user.id))
            if not customer:
                fail("customer_not_found", 404)
        else:
            phone = (body.phone or '').strip() or None
            email = (body.email or '').strip().lower() or None
            normalized_phone = re.sub(r'\D', '', phone or '')[-15:] or None
            # Explicitly type nullable parameters. PostgreSQL cannot infer the
            # type of a bare NULL used in ``%s is not null`` and raises 42P18
            # when both phone and email are omitted (the audio-only flow).
            customer = await one(connection, "select * from customers where organisation_id=%s and owner_id=%s and deleted_at is null and ((%s::text is not null and right(regexp_replace(coalesce(normalized_phone,''),'\\D','','g'),10)=right(regexp_replace(%s::text,'\\D','','g'),10)) or (%s::text is not null and normalized_email=%s::text)) limit 1", (context.id, context.user.id, normalized_phone, normalized_phone, email, email))
            if not customer:
                customer = await one(connection, "insert into customers(organisation_id,owner_id,name,email,phone,normalized_email,normalized_phone,is_unnamed) values(%s,%s,%s,%s,%s,%s,%s,%s) returning *", (context.id, context.user.id, "Name not provided" if body.is_unnamed else body.customer_name, email, phone, email, normalized_phone, body.is_unnamed))
        profile = await one(connection, "select display_name from profiles where id=%s", (context.user.id,))
        row = await one(connection, "insert into conversations(organisation_id,owner_id,customer_id,customer_name_snapshot,salesperson_name_snapshot,title,location,language_hint,consent_confirmed,audio_bucket) values(%s,%s,%s,%s,%s,%s,%s,%s,true,%s) returning *", (context.id, context.user.id, customer["id"], customer["name"], profile["display_name"] if profile and profile["display_name"] else context.user.name, body.title or "", body.location or "", body.language_hint, body.audio_bucket))
        run = None if body.audio_only else await create_run(connection, context, row, engine_id, "production", "initial")
        if run is None:
            await connection.execute("update conversations set transcription_state='blocked' where id=%s", (row["id"],))
            row["transcription_state"] = "blocked"
        if idempotency_key is not None:
            await connection.execute("insert into recorder_private.conversation_requests(organisation_id,owner_id,idempotency_key,request_hash,conversation_id) values(%s,%s,%s,%s,%s)", (context.id, context.user.id, idempotency_key, request_hash, row["id"]))
        await audit(connection, context, "conversation.created", {"engine_id": engine_id, "audio_only": body.audio_only}, row["id"])
        return {**row, "runs": [run] if run else []}


@router.get("/conversations/{conversation_id}")
async def detail(conversation_id: UUID, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        row = await conversation(connection, context, conversation_id)
        runs = await many(connection, "select * from processing_runs where conversation_id=%s order by created_at", (conversation_id,))
        parts = await many(connection, "select * from recording_parts where conversation_id=%s order by ordinal", (conversation_id,))
        versions = await many(connection, "select * from transcript_versions where conversation_id=%s order by created_at desc limit 100", (conversation_id,))
    return {**row, "runs": runs, "parts": parts, "versions": versions}


@router.patch("/conversations/{conversation_id}")
async def update_conversation(conversation_id: UUID, body: s.ConversationPatch, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        await conversation(connection, context, conversation_id, lock=True)
        fields = body.model_dump(exclude_unset=True)
        if not fields:
            fail("empty_update")
        values = [value or "" for value in fields.values()]
        query = "update conversations set " + ",".join(f"{key}=%s" for key in fields) + ",updated_at=now() where id=%s returning *"
        row = await one(connection, query, tuple(values) + (conversation_id,))
        await audit(connection, context, "conversation.metadata_corrected", {"fields": list(fields)}, conversation_id)
        return row


@router.get("/conversations/{conversation_id}/progress")
async def progress(conversation_id: UUID, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        row = await conversation(connection, context, conversation_id)
        jobs = await many(connection, "select id,run_id,window_id,kind,state,attempt,error_code,created_at,updated_at from processing_jobs where conversation_id=%s order by created_at desc limit 100", (conversation_id,))
        chunks = await one(connection, "select count(*) as captured,count(*) filter(where state='validated') as saved,coalesce(sum(sample_count) filter(where state='validated'),0) as saved_samples from audio_chunks where conversation_id=%s", (conversation_id,))
        runs = await many(connection, "select id,engine_id,state,coverage_samples,kind from processing_runs where conversation_id=%s", (conversation_id,))
    return {"conversation": row, "jobs": jobs, "chunks": chunks, "runs": runs}
