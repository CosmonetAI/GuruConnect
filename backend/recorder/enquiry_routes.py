"""Durable sales enquiries linked to the existing private recording pipeline.

Enquiry creation never schedules inference. Capture/finalization remains owned by
the recording API, so retrying this form cannot create another paid job.
"""

import hashlib
import json
import re
import secrets
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .db import many, one
from .errors import fail
from .routes import audit
from .security import Workspace, conversation, workspace
from .walkin_routes import complete_conversion, conversion_visit, validate_conversion_recording
from .assignment import current_ticket_day, format_enquiry_number, format_ticket_number, least_loaded_salesperson, next_daily_ticket
from app.zoho import zoho
from app.session_tags import derive_business_tags

router = APIRouter(prefix="/api/v1/enquiries", tags=["enquiries"])

Text200 = Annotated[str, Field(max_length=200)]
Text160 = Annotated[str, Field(max_length=160)]
Text80 = Annotated[str, Field(max_length=80)]
Quantity = Annotated[int, Field(strict=True, ge=1, le=1_000_000)]


class ProductRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product: Text200 = ""
    sku: Text80 = ""
    quantity: Quantity = 1
    unit: Annotated[str, Field(min_length=1, max_length=32)] = "nos"
    customerSegment: Text80 = ""
    budget: Text80 = ""
    paymentPreference: Text80 = ""
    timeline: Annotated[str, Field(max_length=120)] = ""
    stockCheck: Annotated[bool, Field(strict=True)] = False
    notes: Annotated[str, Field(max_length=2000)] = ""

    @model_validator(mode="after")
    def require_identifier(self):
        if not self.product and not self.sku:
            raise ValueError("Each product requirement needs a product name or SKU")
        return self


class EnquiryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    customerName: Annotated[str, Field(min_length=2, max_length=200)]
    phone: Annotated[str, Field(min_length=7, max_length=20)]
    email: Annotated[str, Field(max_length=320)] = ""
    channel: Annotated[str, Field(min_length=1, max_length=80)]
    branch: Annotated[str, Field(min_length=1, max_length=160)]
    assignedExecutive: Annotated[str, Field(min_length=1, max_length=160)]
    product: Text200 = ""
    quantity: Quantity = 1
    unit: Annotated[str, Field(min_length=1, max_length=32)] = "nos"
    notes: Annotated[str, Field(max_length=10000)] = ""
    sourceEventId: Annotated[str, Field(max_length=160)] = ""
    customerType: Literal["Retail customer", "Vendor", "Project", "Retail", "Dealer"] = "Retail customer"
    company: Text200 = ""
    projectName: Text200 = ""
    preferredContactMethod: Text80 = ""
    productRequirements: Annotated[list[ProductRequirement], Field(max_length=25)] = Field(default_factory=list)
    customerNotes: Annotated[str, Field(max_length=4000)] = ""
    salespersonNotes: Annotated[str, Field(max_length=4000)] = ""
    requirementsSummary: Annotated[str, Field(max_length=4000)] = ""
    followUpDate: Annotated[str, Field(max_length=10)] = ""
    followUpOwner: Text160 = ""
    priority: Literal["Normal", "High", "Urgent"] = "Normal"
    conversationId: UUID | None = None

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, value: str):
        digits = re.sub(r"\D", "", value)
        if not re.fullmatch(r"\+?[0-9().\-\s]{7,20}", value) or not 7 <= len(digits) <= 15:
            raise ValueError("Enter a valid phone number")
        return value

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str):
        if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address")
        return value

    @field_validator("followUpDate")
    @classmethod
    def valid_date(cls, value: str):
        if value:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("Use an ISO calendar date (YYYY-MM-DD)")
            date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def bounded_payload(self):
        if len(json.dumps(self.model_dump(mode="json"), ensure_ascii=False).encode()) > 95_000:
            raise ValueError("Enquiry details are too large; shorten notes or product requirements")
        return self

EnquiryUpdate = EnquiryCreate


class EnquiryAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    assigneeId: UUID


# A deleted recording is not exposed as still available. Its enquiry metadata
# remains durable and readable under the same workspace/owner authorization.
SELECT_ENQUIRY = """
select e.*, c.capture_state, c.upload_state, c.transcription_state,
       c.english_state, c.speaker_state, c.final_state, c.duration_seconds,
       c.deleted_at as recording_deleted_at,
       assigned_profile.display_name as assigned_salesperson_name,
       assigned_user.email as assigned_salesperson_email,
       forwarded_profile.display_name as forwarded_by_name
from public.guruconnect_enquiries e
left join public.conversations c on c.id=e.conversation_id
  and c.organisation_id=e.organisation_id and c.owner_id=e.owner_id
left join public.profiles assigned_profile on assigned_profile.id=e.assigned_to
left join auth.users assigned_user on assigned_user.id=e.assigned_to
left join public.profiles forwarded_profile on forwarded_profile.id=e.owner_id
left join public.voice_ai_contexts ai_context on ai_context.conversation_id=e.conversation_id
"""


def serialize_enquiry(row: dict) -> dict:
    payload = dict(row["payload"])
    # Stored server ownership and dates always win over submitted metadata.
    payload.update(id=str(row["id"]),
                   enquiryNumber=format_enquiry_number(
                       row["ticket_day"] or row["created_at"].date(),
                       row["daily_sequence"] or row["enquiry_number"],
                   ),
                   createdAt=row["created_at"].isoformat())
    payload["conversationId"] = str(row["conversation_id"]) if row["conversation_id"] else None
    payload["stage"] = row.get("stage", "new")
    payload["unread"] = bool(row.get("unread", False))
    payload["assignedTo"] = str(row["assigned_to"]) if row.get("assigned_to") else None
    payload["assignedSalesperson"] = row.get("assigned_salesperson_name") or row.get("assigned_salesperson_email") or payload.get("assignedExecutive", "")
    payload["assignedSalespersonEmail"] = row.get("assigned_salesperson_email", "")
    payload["forwardedBy"] = payload.get("forwardedBy") or row.get("forwarded_by_name") or "Team"
    payload["tags"] = derive_business_tags(row.get("ai_context") or {}, payload=payload)
    if row["conversation_id"] and row.get("capture_state") and not row.get("recording_deleted_at"):
        payload["recording"] = {
            "captureState": row["capture_state"], "uploadState": row["upload_state"],
            "transcriptionState": row["transcription_state"], "englishState": row["english_state"],
            "speakerState": row["speaker_state"], "finalState": row["final_state"],
            "durationSeconds": row["duration_seconds"],
        }
    else:
        payload["recording"] = None
    return payload


async def assign_missing_enquiries(connection, organisation_id, timezone_name):
    """Persist an assignee for legacy enquiries created without one."""
    missing = await many(connection, """
        select id, ticket_day, payload
        from public.guruconnect_enquiries
        where organisation_id=%s and assigned_to is null
        order by created_at asc, id asc
        for update skip locked
    """, (organisation_id,))
    for item in missing:
        ticket_day = item["ticket_day"] or current_ticket_day(timezone_name)
        assignee = await least_loaded_salesperson(connection, organisation_id, ticket_day)
        if not assignee:
            continue
        name = assignee["display_name"] or assignee["email"]
        payload = dict(item.get("payload") or {})
        payload.update({
            "assignedExecutive": name,
            "assignedSalespersonEmail": assignee["email"],
            "followUpOwner": name,
        })
        await connection.execute("""
            update public.guruconnect_enquiries
            set assigned_to=%s,payload=%s
            where id=%s and organisation_id=%s and assigned_to is null
        """, (assignee["user_id"], Jsonb(payload), item["id"], organisation_id))


@router.get("")
async def list_enquiries(request: Request, context: Workspace = Depends(workspace),
                         page: int = Query(default=1, ge=1, le=100_000),
                         pageSize: int = Query(default=10, ge=1, le=100),
                         query: str = Query(default="", max_length=120),
                         channel: str = Query(default="", max_length=80),
                         branch: str = Query(default="", max_length=160)):
    # Treat '%' and '_' as text; search values are never concatenated into SQL.
    search = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    # The owner's personal view is organization-wide; a switched member view
    # is restricted to that member while the owner remains the authenticated actor.
    scoped_all = context.admin and context.personal
    scope_user = context.member_id if not scoped_all else context.user.id
    where = """where e.organisation_id=%s and (%s or e.assigned_to=%s or (e.owner_id=%s and e.assigned_to is null))
      and (e.customer_name ilike %s or e.phone ilike %s or e.id::text ilike %s
           or ('ENQ-'||to_char(coalesce(e.ticket_day,(e.created_at at time zone 'Asia/Kolkata')::date),'DDMMYYYY')||'-'||
               lpad(coalesce(e.daily_sequence,e.enquiry_number)::text,6,'0')) ilike %s)
      and (%s='' or e.channel=%s) and (%s='' or e.branch=%s)"""
    params = (context.id, scoped_all, scope_user, scope_user, search, search, search, search, channel, channel, branch, branch)
    async with request.app.state.db.connection() as connection:
        await assign_missing_enquiries(connection, context.id, request.app.state.settings.app_timezone)
        # One SQL statement gives count and rows the same snapshot, including an
        # empty page, without extending the transaction across another roundtrip.
        rows = await many(connection, "with matches as materialized (" + SELECT_ENQUIRY + where + "), "
                          "totals as (select count(*) as total from matches), "
                          "page_rows as (select * from matches order by created_at desc,id desc limit %s offset %s) "
                          "select page_rows.*,totals.total from totals left join page_rows on true order by page_rows.created_at desc,page_rows.id desc",
                          params + (pageSize, (page - 1) * pageSize))
    total = rows[0]["total"]
    return {"items": [serialize_enquiry(row) for row in rows if row["id"] is not None], "total": total,
            "page": page, "pageSize": pageSize, "totalPages": (total + pageSize - 1) // pageSize}


@router.post("", status_code=201)
async def create_enquiry(body: EnquiryCreate, request: Request, response: Response,
                         context: Workspace = Depends(workspace),
                         idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128)):
    if not idempotency_key or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", idempotency_key):
        fail("idempotency_key_required", 400, "Send a stable Idempotency-Key (8–128 letters, digits, dots, underscores, colons or hyphens) when saving")
    payload = body.model_dump(mode="json")
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    async with request.app.state.db.connection() as connection:
        # Keep revocation serialized with this save, including idempotent replay.
        member = await one(connection, "select role from members where organisation_id=%s and user_id=%s and status='active' for share",
                           (context.id, context.user.id))
        if not member:
            fail("workspace_access_denied", 403)
        # Same-key concurrent submissions are serialized before reading or writing.
        await connection.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))",
                                 (f"guruconnect-enquiry:{context.id}:{context.user.id}:{idempotency_key}",))
        prior = await one(connection, SELECT_ENQUIRY + " where e.organisation_id=%s and e.owner_id=%s and e.idempotency_key=%s",
                          (context.id, context.user.id, idempotency_key))
        if prior:
            if prior["payload_hash"] != payload_hash:
                fail("idempotency_conflict", 409, "This save key was already used for different enquiry details; refresh before saving again")
            response.status_code = 200
            return serialize_enquiry(prior)
        visit = await conversion_visit(connection, context, body.sourceEventId)
        existing_ticket = visit.get("conversion_enquiry") if visit else None
        if visit:
            # The locked visit owns assignment. Requirements conversion must
            # retain its current agent rather than load-balance the ticket again.
            assignee = await one(connection, "select m.user_id,p.display_name,u.email from public.members m join public.profiles p on p.id=m.user_id join auth.users u on u.id=m.user_id where m.organisation_id=%s and m.user_id=%s and m.status='active' for share of m", (context.id, visit["assigned_agent_id"]))
            if not assignee:
                fail("walkin_agent_not_found", 409)
        else:
            ticket_day = current_ticket_day(request.app.state.settings.app_timezone)
            assignee = await least_loaded_salesperson(connection, context.id, ticket_day)
        assigned_to = assignee["user_id"] if assignee else None
        if existing_ticket:
            if existing_ticket["conversation_id"] and existing_ticket["conversation_id"] != body.conversationId:
                fail("walkin_recording_already_linked", 409)
            # Keep ticket metadata and reception notes that the conversion form
            # did not submit. Explicitly submitted fields remain editable.
            payload = {**payload, **existing_ticket["payload"], **body.model_dump(mode="json", exclude_unset=True)}
            payload["sourceEventId"] = f"walkin:{visit['id']}"
            payload["sourceWalkinId"] = str(visit["id"])
        if assignee:
            payload["assignedExecutive"] = assignee["display_name"] or assignee["email"]
            payload["assignedSalespersonEmail"] = assignee["email"]
        if body.conversationId:
            recording = await conversation(connection, context, body.conversationId, lock=True)
            if recording["owner_id"] != context.user.id:
                fail("recording_owner_required", 403)
            if recording["upload_state"] != "saved" or recording["capture_state"] not in {"stopped", "interrupted"} or recording["total_samples"] <= 0:
                fail("recording_not_saved", 409, "Finish the recording or audio upload and wait until its audio is saved before saving the enquiry")
            await validate_conversion_recording(connection, context, visit, body.conversationId)
            linked = await one(connection, "select id from public.guruconnect_enquiries where conversation_id=%s", (body.conversationId,))
            if linked and (not existing_ticket or linked["id"] != existing_ticket["id"]):
                fail("recording_already_linked", 409, "This recording is already attached to an enquiry")
        books_contact_id = existing_ticket.get("books_contact_id") if existing_ticket else None
        follow_up_required = False
        if body.conversationId:
            intent_row = await one(connection, "select last_intent from public.voice_ai_contexts where conversation_id=%s", (body.conversationId,))
            last_intent = (intent_row or {}).get("last_intent")
            follow_up_required = last_intent in (None, "", "unknown", "general_conversation", "follow_up")
            payload["followUpRequired"] = follow_up_required
        try:
            zoho_status = zoho.status() if not books_contact_id else {}
            books_org = zoho_status.get("organization_id")
            email = payload.get("email") or payload.get("customerEmail")
            if books_org and (body.phone or email):
                books_contact = await zoho.ensure_books_contact(books_org, name=body.customerName, email=email, phone=body.phone)
                books_contact_id = books_contact.get("contact_id") or books_contact.get("id")
        except Exception:
            pass
        if existing_ticket:
            # Preserve the issued ticket number/day, public token hash and row
            # identity. Owning the locked assigned visit authorizes the handoff
            # from reception to this agent; the recording owner FK stays exact.
            await connection.execute("""update public.guruconnect_enquiries set
                owner_id=%s,conversation_id=%s,idempotency_key=%s,payload_hash=%s,payload=%s,
                customer_name=%s,phone=%s,channel=%s,branch=%s,assigned_to=%s,unread=true,books_contact_id=%s,follow_up_required=%s
                where id=%s and organisation_id=%s""",
                (context.user.id, body.conversationId, idempotency_key, payload_hash, Jsonb(payload),
                 body.customerName, body.phone, body.channel, body.branch, assigned_to, books_contact_id, follow_up_required,
                 existing_ticket["id"], context.id))
            row = {"id": existing_ticket["id"]}
        else:
            daily_sequence = await next_daily_ticket(connection, context.id, ticket_day)
            payload["ticketNumber"] = daily_sequence
            payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
            tracking_token = secrets.token_urlsafe(12)
            tracking_hash = hashlib.sha256(tracking_token.encode()).hexdigest()
            row = await one(connection, """insert into public.guruconnect_enquiries
            (organisation_id,owner_id,conversation_id,idempotency_key,payload_hash,payload,
             customer_name,phone,channel,branch,assigned_to,stage,unread,ticket_day,daily_sequence,public_token_hash,books_contact_id,follow_up_required)
            values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'new',true,%s,%s,%s,%s,%s) returning id""",
                            (context.id, context.user.id, body.conversationId, idempotency_key, payload_hash, Jsonb(payload),
                             body.customerName, body.phone, body.channel, body.branch, assigned_to, ticket_day, daily_sequence, tracking_hash, books_contact_id, follow_up_required))
        await audit(connection, context, "enquiry.converted_from_walkin" if existing_ticket else "enquiry.created", {"enquiry_id": str(row["id"]), "has_recording": body.conversationId is not None}, body.conversationId)
        if visit:
            await complete_conversion(connection, context, visit, row["id"])
        saved = await one(connection, SELECT_ENQUIRY + " where e.id=%s and e.organisation_id=%s", (row["id"], context.id))
    return serialize_enquiry(saved)


async def create_walkin_enquiry(connection, context, row, assigned_to, assigned_name, timezone_name):
    """Create the editable owner queue item as soon as a walk-in is assigned."""
    product = row["payload"].get("productOrService", "")
    payload = {
        "customerName": row["payload"]["customerName"],
        "phone": row["payload"]["mobileNumber"],
        "email": row["payload"].get("email", ""),
        "channel": "Retail Walk-in",
        "branch": "Walk-in",
        "assignedExecutive": assigned_name or "Salesperson",
        "product": product,
        "quantity": 1,
        "unit": "nos",
        "notes": row["payload"].get("internalNotes", ""),
        "sourceWalkinId": str(row["id"]),
        "customerType": "Retail customer",
        "company": row["payload"].get("companyName", ""),
        "projectName": row["payload"].get("projectName", ""),
        "preferredContactMethod": row["payload"].get("preferredContactMethod", ""),
        "productRequirements": ([{"product": product, "sku": "", "quantity": 1, "unit": "nos", "customerSegment": "", "budget": "", "paymentPreference": "", "timeline": "", "stockCheck": False, "notes": ""}] if product else []),
        "customerNotes": row["payload"].get("purposeDescription", ""),
        "salespersonNotes": "",
        "requirementsSummary": "",
        "followUpDate": "",
        "followUpOwner": assigned_name or "",
        "priority": "Normal",
    }
    key = f"walkin-assignment:{row['id']}"
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    existing = await one(connection, "select id from public.guruconnect_enquiries where organisation_id=%s and idempotency_key=%s", (context.id, key))
    if existing:
        return existing["id"]
    ticket_day = current_ticket_day(timezone_name)
    daily_sequence = await next_daily_ticket(connection, context.id, ticket_day)
    payload["ticketNumber"] = daily_sequence
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    tracking_token = secrets.token_urlsafe(12)
    tracking_hash = hashlib.sha256(tracking_token.encode()).hexdigest()
    created = await one(connection, """insert into public.guruconnect_enquiries
        (organisation_id,owner_id,idempotency_key,payload_hash,payload,customer_name,phone,channel,branch,assigned_to,stage,unread,ticket_day,daily_sequence,public_token_hash)
        values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'new',true,%s,%s,%s) returning id""",
        (context.id, context.user.id, key, digest, Jsonb(payload), payload["customerName"], payload["phone"], payload["channel"], payload["branch"], assigned_to, ticket_day, daily_sequence, tracking_hash))
    await audit(connection, context, "enquiry.created_from_walkin", {"enquiry_id": str(created["id"]), "walkin_id": str(row["id"])})
    return created["id"]


@router.patch("/{enquiry_id}")
async def update_enquiry(enquiry_id: UUID, body: EnquiryUpdate, request: Request, context: Workspace = Depends(workspace)):
    payload = body.model_dump(mode="json")
    async with request.app.state.db.connection() as connection:
        row = await one(connection, SELECT_ENQUIRY + " where e.id=%s and e.organisation_id=%s and (%s or e.assigned_to=%s or (e.owner_id=%s and e.assigned_to is null)) for update",
                        (enquiry_id, context.id, context.admin, context.user.id, context.user.id))
        if not row:
            fail("enquiry_not_found", 404)
        if body.conversationId:
            recording = await conversation(connection, context, body.conversationId, lock=True)
            if recording["owner_id"] != context.user.id:
                fail("recording_owner_required", 403)
            if recording["upload_state"] != "saved" or recording["capture_state"] not in {"stopped", "interrupted"} or recording["total_samples"] <= 0:
                fail("recording_not_saved", 409, "Finish the recording or audio upload before saving this enquiry")
            linked = await one(connection, "select id from public.guruconnect_enquiries where conversation_id=%s and id<>%s", (body.conversationId, enquiry_id))
            if linked:
                fail("recording_already_linked", 409)
        payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        await connection.execute("""update public.guruconnect_enquiries
            set payload=%s,payload_hash=%s,conversation_id=%s,customer_name=%s,phone=%s,channel=%s,branch=%s
            where id=%s and organisation_id=%s""",
            (Jsonb(payload), payload_hash, body.conversationId, body.customerName, body.phone, body.channel, body.branch, enquiry_id, context.id))
        await audit(connection, context, "enquiry.updated", {"enquiry_id": str(enquiry_id)}, body.conversationId)
        saved = await one(connection, SELECT_ENQUIRY + " where e.id=%s and e.organisation_id=%s", (enquiry_id, context.id))
    return serialize_enquiry(saved)


@router.get("/assignees")
async def list_assignees(request: Request, context: Workspace = Depends(workspace)):
    if context.role not in {"owner", "salesperson"}:
        fail("enquiry_assignment_not_allowed", 403)
    role_filter = "('owner','salesperson')" if context.role == "owner" else "('owner')"
    async with request.app.state.db.connection() as connection:
        await connection.execute("""
            update public.profiles p
            set availability_status='inactive', final_status='inactive', final_status_until=null, updated_at=now()
            where coalesce(p.final_status, p.availability_status)='active'
              and (p.last_seen_at is null or p.last_seen_at < now()-interval '90 seconds')
              and not exists (select 1 from public.profile_presence_sessions s where s.user_id=p.id and s.last_seen_at >= now()-interval '90 seconds')
              and exists (select 1 from public.members m where m.organisation_id=%s and m.user_id=p.id and m.status='active')
        """, (context.id,))
        rows = await many(connection, f"""select m.user_id,m.role,p.display_name,u.email,
                case when coalesce(p.final_status, p.availability_status) in ('inactive','on_leave') then coalesce(p.final_status, p.availability_status)
                     when coalesce(p.final_status, p.availability_status)='be_right_back' and p.updated_at > now()-interval '15 minutes' then 'be_right_back'
                     when exists (select 1 from public.profile_presence_sessions ps where ps.user_id=p.id and ps.last_seen_at >= now()-interval '90 seconds') then 'active'
                     else 'inactive' end as presence_status,
                count(e.id) filter (where e.stage in ('new','in_process')) as open_tickets
            from public.members m
            join public.profiles p on p.id=m.user_id
            join auth.users u on u.id=m.user_id
            left join public.guruconnect_enquiries e on e.organisation_id=m.organisation_id and e.assigned_to=m.user_id
            where m.organisation_id=%s and m.status='active' and m.role in {role_filter}
            group by m.user_id,m.role,p.display_name,u.email,p.id,p.availability_status,p.final_status,p.final_status_until,p.last_seen_at
            order by case when m.role='salesperson' then 0 else 1 end, open_tickets asc, p.display_name""", (context.id,))
    return {"items": [{"id": str(row["user_id"]), "name": row["display_name"] or row["email"], "email": row["email"], "role": row["role"], "openTickets": row["open_tickets"], "presenceStatus": row["presence_status"]} for row in rows]}


@router.patch("/{enquiry_id}/assignment")
async def assign_enquiry(enquiry_id: UUID, body: EnquiryAssignment, request: Request, context: Workspace = Depends(workspace)):
    if context.role not in {"owner", "salesperson"}:
        fail("enquiry_assignment_not_allowed", 403)
    async with request.app.state.db.connection() as connection:
        row = await one(connection, SELECT_ENQUIRY + " where e.id=%s and e.organisation_id=%s and (%s or e.assigned_to=%s or (e.owner_id=%s and e.assigned_to is null)) for update",
                        (enquiry_id, context.id, context.admin, context.user.id, context.user.id))
        if not row:
            fail("enquiry_not_found", 404)
        target = await one(connection, """select m.user_id,m.role,p.display_name,u.email
            from public.members m join public.profiles p on p.id=m.user_id join auth.users u on u.id=m.user_id
            where m.organisation_id=%s and m.user_id=%s and m.status='active' and m.role in ('owner','salesperson')""",
                           (context.id, body.assigneeId))
        if not target:
            fail("enquiry_assignment_not_allowed", 422, "Choose an active owner or salesperson.")
        if context.role == "salesperson" and target["role"] != "owner":
            fail("enquiry_assignment_not_allowed", 403, "Salespeople can transfer enquiries to an owner only.")
        payload = dict(row["payload"] or {})
        actor = await one(connection, "select display_name from public.profiles where id=%s", (context.user.id,))
        payload.update({
            "assignedExecutive": target["display_name"] or target["email"],
            "assignedSalespersonEmail": target["email"],
            "followUpOwner": target["display_name"] or target["email"],
            "forwardedBy": (actor or {}).get("display_name") or "Team",
        })
        await connection.execute("""update public.guruconnect_enquiries
            set assigned_to=%s,payload=%s,unread=true
            where id=%s and organisation_id=%s""", (target["user_id"], Jsonb(payload), enquiry_id, context.id))
        source_walkin = payload.get("sourceWalkinId")
        if source_walkin:
            try:
                UUID(str(source_walkin))
            except ValueError:
                source_walkin = None
        if source_walkin:
            await connection.execute("""update public.guruconnect_walkin_visits
                set assigned_agent_id=%s,assigned_at=coalesce(assigned_at,clock_timestamp()),
                    status=case when status in ('Registered','Waiting for Assignment') then 'Assigned' else status end,
                    assignment_note=%s,updated_at=clock_timestamp()
                where id=%s and organisation_id=%s""", (target["user_id"], f"Enquiry assigned by {(actor or {}).get('display_name') or 'team'}", source_walkin, context.id))
        await audit(connection, context, "enquiry.assigned", {"enquiry_id": str(enquiry_id), "assigned_to": str(target["user_id"])})
        saved = await one(connection, SELECT_ENQUIRY + " where e.id=%s and e.organisation_id=%s", (enquiry_id, context.id))
    return serialize_enquiry(saved)


@router.get("/public/track")
async def track_public_ticket(request: Request, token: str = Query(min_length=16, max_length=128)):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    async with request.app.state.db.connection() as connection:
        row = await one(connection, """select e.ticket_day,e.daily_sequence,e.stage,e.created_at,
                                      p.display_name as salesperson_name,u.email as salesperson_email
                               from public.guruconnect_enquiries e
                               left join public.profiles p on p.id=e.assigned_to
                               left join auth.users u on u.id=e.assigned_to
                               where e.public_token_hash=%s limit 1""", (token_hash,))
    if not row:
        raise HTTPException(404, "Ticket not found or token expired")
    return {"ticketNumber": format_ticket_number(row["ticket_day"], row["daily_sequence"]), "date": str(row["ticket_day"]), "stage": row["stage"],
            "createdAt": row["created_at"].isoformat(), "salesperson": row["salesperson_name"] or "Awaiting assignment",
            "salespersonEmail": row["salesperson_email"] or ""}

@router.get("/{enquiry_id}")
async def get_enquiry(enquiry_id: UUID, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        row = await one(connection, SELECT_ENQUIRY + " where e.id=%s and e.organisation_id=%s and (%s or e.assigned_to=%s or (e.owner_id=%s and e.assigned_to is null))",
                        (enquiry_id, context.id, context.admin, context.user.id, context.user.id))
        if not row:
            fail("enquiry_not_found", 404)
        await connection.execute("update public.guruconnect_enquiries set unread=false where id=%s and organisation_id=%s", (enquiry_id, context.id))
    return serialize_enquiry(row)
