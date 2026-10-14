"""Workspace-authorized walk-ins; browser storage is never a hosted fallback."""

import hashlib
import json
import re
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .db import many, one
from .errors import fail
from .routes import audit
from .security import Workspace, administrator, owned_conversation, workspace

router = APIRouter(prefix="/api/v1/walkins", tags=["walkins"])
Purpose = Literal["Product enquiry", "New business enquiry", "Product demonstration", "Pricing or quotation request", "Existing order enquiry", "Service or support request", "Meeting with a specific employee", "Follow-up meeting", "Other"]
Status = Literal["Registered", "Waiting for Assignment", "Assigned", "Waiting for Sales Agent", "In Discussion", "Requirements Captured", "Follow-up Required", "Converted to Enquiry", "Completed", "Cancelled"]
Availability = Literal["Available", "Busy", "Unavailable"]
Text200 = Annotated[str, Field(max_length=200)]
TRANSITIONS = {
    "Registered": {"Waiting for Assignment", "Cancelled"},
    "Waiting for Assignment": {"Cancelled"},
    "Assigned": {"Waiting for Sales Agent", "In Discussion", "Cancelled"},
    "Waiting for Sales Agent": {"In Discussion", "Cancelled"},
    "In Discussion": {"Requirements Captured", "Follow-up Required", "Completed", "Cancelled"},
    "Requirements Captured": {"Follow-up Required", "Completed"},
    "Follow-up Required": {"In Discussion", "Requirements Captured", "Completed", "Cancelled"},
    "Converted to Enquiry": {"Completed"}, "Completed": set(), "Cancelled": set(),
}
CLOSED = {"Completed", "Cancelled", "Converted to Enquiry"}


def safe_text(value):
    if isinstance(value, str) and any(character == "\x00" or 0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError("Text must contain valid Unicode without null characters")
    return value


class WalkinCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    customerName: Annotated[str, Field(min_length=2, max_length=150)]
    mobileNumber: Annotated[str, Field(min_length=7, max_length=20)]
    email: Annotated[str, Field(max_length=320)] = ""
    companyName: Annotated[str, Field(max_length=150)] = ""
    purpose: Purpose = "Other"
    purposeDescription: Annotated[str, Field(max_length=2000)] = ""
    productOrService: Text200 = ""
    department: Text200 = ""
    requestedEmployee: Text200 = ""
    appointmentStatus: Literal["Walk-in", "Scheduled Appointment"] = "Walk-in"
    appointmentReference: Text200 = ""
    internalNotes: Annotated[str, Field(max_length=4000)] = ""

    _safe_text = field_validator("*", mode="before")(safe_text)

    @field_validator("mobileNumber")
    @classmethod
    def valid_phone(cls, value):
        if not re.fullmatch(r"\+?[0-9().\-\s]{7,20}", value) or not 7 <= len(re.sub(r"\D", "", value)) <= 15:
            raise ValueError("Enter a valid mobile number")
        return value

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address")
        return value


class Versioned(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expectedUpdatedAt: datetime

    @field_validator("expectedUpdatedAt")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("Use a timestamp with a timezone")
        return value


class Assignment(Versioned):
    agentId: UUID | None
    note: Annotated[str, Field(max_length=2000)] = ""

    _safe_note = field_validator("note", mode="before")(safe_text)


class StatusChange(Versioned):
    status: Status


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    detailedRequirement: Annotated[str, Field(max_length=10000)] = ""
    productOrService: Text200 = ""
    quantity: Text200 = ""
    budgetRange: Text200 = ""
    expectedPurchaseTimeline: Text200 = ""
    preferredSolution: Annotated[str, Field(max_length=2000)] = ""
    competitorOrAlternative: Text200 = ""
    specialRequests: Annotated[str, Field(max_length=4000)] = ""
    followUpAt: Annotated[str, Field(max_length=10)] = ""
    agentNotes: Annotated[str, Field(max_length=4000)] = ""
    nextAction: Annotated[str, Field(max_length=2000)] = ""

    _safe_text = field_validator("*", mode="before")(safe_text)

    @field_validator("followUpAt")
    @classmethod
    def valid_date(cls, value):
        if value:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("Use an ISO calendar date")
            date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def bounded_payload(self):
        if len(json.dumps(self.model_dump(), ensure_ascii=False).encode()) > 47_000:
            raise ValueError("Requirement details are too large; shorten notes")
        return self


class RequirementSave(Versioned):
    requirement: Requirement


class AvailabilityChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    availability: Availability


class RecordingLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversationId: UUID


SELECT_VISIT = """select v.*,p.display_name as assigned_agent_name
    from public.guruconnect_walkin_visits v left join public.profiles p on p.id=v.assigned_agent_id """
ACCESS = "(%s or v.assigned_agent_id=%s or (v.assigned_agent_id is null and v.registered_by=%s))"


def serialize_visit(row):
    result = dict(row["payload"])
    result.update(id=str(row["id"]), visitNumber=f"WI-{row['created_at'].year}-{row['visit_number']:06d}",
                  registeredBy=str(row["registered_by"]), assignedAgentId=str(row["assigned_agent_id"]) if row["assigned_agent_id"] else None,
                  assignedAgentName=row.get("assigned_agent_name") or ("Salesperson" if row["assigned_agent_id"] else None),
                  assignmentNote=row["assignment_note"], status=row["status"], checkInAt=row["check_in_at"].isoformat(),
                  assignedAt=row["assigned_at"].isoformat() if row["assigned_at"] else None,
                  convertedEnquiryReference=str(row["converted_enquiry_id"]) if row["converted_enquiry_id"] else None,
                  createdAt=row["created_at"].isoformat(), updatedAt=row["updated_at"].isoformat())
    return result


def serialize_requirement(row):
    result = Requirement.model_validate(row["requirements"]).model_dump()
    result["updatedAt"] = row["requirements_updated_at"].isoformat() if row["requirements_updated_at"] else None
    return result


async def active_context(connection, context):
    # Hold membership stable until this write commits; concurrent revocation or
    # demotion must serialize with the operation rather than reuse cached roles.
    member = await one(connection, "select role from public.members where organisation_id=%s and user_id=%s and status='active' for share", (context.id, context.user.id))
    if not member or member["role"] != context.role:
        fail("workspace_access_denied", 403)


async def visit(connection, context, visit_id, *, lock=False, editing=False):
    await active_context(connection, context)
    row = await one(connection, SELECT_VISIT + " where v.id=%s and v.organisation_id=%s and " + ACCESS + (" for update of v" if lock else ""),
                    (visit_id, context.id, context.admin, context.user.id, context.user.id))
    if not row:
        fail("walkin_not_found", 404)
    if editing and not context.admin and row["assigned_agent_id"] != context.user.id:
        fail("walkin_assigned_agent_required", 403)
    return row


def check_version(row, expected):
    if row["updated_at"] != expected:
        fail("stale_walkin", 409, "This visit changed while you were editing. Refresh the visit before saving again.")


async def history(connection, context, row, event_type, old_value=None, new_value=None, notes=""):
    await connection.execute("""insert into public.guruconnect_walkin_history
        (organisation_id,visit_id,actor_id,event_type,old_value,new_value,notes) values(%s,%s,%s,%s,%s,%s,%s)""",
                             (context.id, row["id"], context.user.id, event_type, old_value, new_value, notes))
    await audit(connection, context, "walkin." + event_type.lower().replace(" ", "_"), {"visit_id": str(row["id"])})


@router.get("/agents")
async def list_agents(request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        rows = await many(connection, """select m.user_id,p.display_name,coalesce(a.availability,'Available') as availability,
            case when %s or m.user_id=%s then count(v.id) else null end as open_visits
            from public.members m join public.profiles p on p.id=m.user_id
            left join public.guruconnect_walkin_agents a on a.organisation_id=m.organisation_id and a.user_id=m.user_id
            left join public.guruconnect_walkin_visits v on v.organisation_id=m.organisation_id and v.assigned_agent_id=m.user_id and v.status not in ('Completed','Cancelled','Converted to Enquiry')
            where m.organisation_id=%s and m.status='active'
            group by m.user_id,p.display_name,a.availability order by p.display_name,m.user_id limit 1000""", (context.admin, context.user.id, context.id))
    return {"items": [{"id": str(row["user_id"]), "name": row["display_name"] or "Salesperson", "department": "Sales", "specialization": "", "availability": row["availability"], "openVisits": row["open_visits"]} for row in rows],
            "currentAgentId": str(context.user.id), "canManage": context.admin}


@router.patch("/agents/{agent_id}")
async def update_availability(agent_id: UUID, body: AvailabilityChange, request: Request, context: Workspace = Depends(administrator)):
    async with request.app.state.db.connection() as connection:
        await active_context(connection, context)
        member = await one(connection, "select user_id from public.members where organisation_id=%s and user_id=%s and status='active' for share", (context.id, agent_id))
        if not member:
            fail("walkin_agent_not_found", 404)
        await connection.execute("insert into public.guruconnect_walkin_agents(organisation_id,user_id,availability) values(%s,%s,%s) on conflict(organisation_id,user_id) do update set availability=excluded.availability,updated_at=clock_timestamp()", (context.id, agent_id, body.availability))
        await audit(connection, context, "walkin.agent_availability_changed", {"agent_id": str(agent_id), "availability": body.availability})
    return {"ok": True}


@router.get("/history")
async def list_history(request: Request, context: Workspace = Depends(workspace),
                       visitId: UUID | None = None, page: int = Query(default=1, ge=1, le=100000), pageSize: int = Query(default=100, ge=1, le=100)):
    where = " where h.organisation_id=%s and " + ACCESS + " and (%s::uuid is null or h.visit_id=%s)"
    params = (context.id, context.admin, context.user.id, context.user.id, visitId, visitId)
    async with request.app.state.db.connection() as connection:
        if visitId:
            await visit(connection, context, visitId)
        rows = await many(connection, """with matches as materialized (
            select h.*,p.display_name as actor_name from public.guruconnect_walkin_history h
            join public.guruconnect_walkin_visits v on v.id=h.visit_id and v.organisation_id=h.organisation_id
            join public.profiles p on p.id=h.actor_id""" + where + """), totals as (select count(*) as total from matches),
            page_rows as (select * from matches order by created_at desc,id desc limit %s offset %s)
            select page_rows.*,totals.total from totals left join page_rows on true order by page_rows.created_at desc,page_rows.id desc""", params + (pageSize, (page - 1) * pageSize))
    return {"items": [{"id": str(row["id"]), "visitId": str(row["visit_id"]), "eventType": row["event_type"], "oldValue": row["old_value"], "newValue": row["new_value"], "notes": row["notes"], "performedBy": row["actor_name"] or "Salesperson", "createdAt": row["created_at"].isoformat()} for row in rows if row["id"]],
            "total": rows[0]["total"], "page": page, "pageSize": pageSize}


@router.get("")
async def list_visits(request: Request, context: Workspace = Depends(workspace),
                      page: int = Query(default=1, ge=1, le=100000), pageSize: int = Query(default=100, ge=1, le=100),
                      query: str = Query(default="", max_length=120), purpose: Purpose | None = None, status: Status | None = None,
                      assignedAgentId: UUID | None = None, fromDate: date | None = None, toDate: date | None = None):
    if fromDate and toDate and fromDate > toDate:
        fail("invalid_date_range", 422)
    search = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    where = " where v.organisation_id=%s and " + ACCESS + """
        and (v.customer_name ilike %s or v.mobile_number ilike %s or ('WI-'||extract(year from v.created_at)::text||'-'||lpad(v.visit_number::text,greatest(6,length(v.visit_number::text)),'0')) ilike %s)
        and (%s::text is null or v.purpose=%s) and (%s::text is null or v.status=%s)
        and (%s::uuid is null or v.assigned_agent_id=%s)
        and (%s::date is null or v.check_in_at>=%s::date)
        and (%s::date is null or v.check_in_at<%s::date+interval '1 day')"""
    params = (context.id, context.admin, context.user.id, context.user.id, search, search, search, purpose, purpose, status, status, assignedAgentId, assignedAgentId, fromDate, fromDate, toDate, toDate)
    async with request.app.state.db.connection() as connection:
        rows = await many(connection, "with matches as materialized (" + SELECT_VISIT + where + """), totals as (
            select count(*) as total,
            count(*) filter(where status in ('Registered','Waiting for Assignment')) as waiting_count,
            count(*) filter(where status in ('Assigned','Waiting for Sales Agent')) as assigned_count,
            count(*) filter(where status='In Discussion') as discussion_count,
            count(*) filter(where status='Requirements Captured') as requirements_count,
            count(*) filter(where converted_enquiry_id is not null) as converted_count from matches),
            page_rows as (select * from matches order by check_in_at desc,id desc limit %s offset %s)
            select page_rows.*,totals.* from totals left join page_rows on true order by page_rows.check_in_at desc,page_rows.id desc""", params + (pageSize, (page - 1) * pageSize))
    return {"items": [serialize_visit(row) for row in rows if row["id"]], "total": rows[0]["total"], "page": page, "pageSize": pageSize,
            "summary": {"waiting": rows[0]["waiting_count"], "assigned": rows[0]["assigned_count"], "discussion": rows[0]["discussion_count"], "requirements": rows[0]["requirements_count"], "converted": rows[0]["converted_count"]}}


@router.post("", status_code=201)
async def register_visit(body: WalkinCreate, request: Request, response: Response, context: Workspace = Depends(workspace),
                         idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128)):
    if not idempotency_key or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", idempotency_key):
        fail("idempotency_key_required", 400)
    payload = body.model_dump()
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    async with request.app.state.db.connection() as connection:
        await active_context(connection, context)
        await connection.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", (f"walkin:{context.id}:{context.user.id}:{idempotency_key}",))
        prior = await one(connection, SELECT_VISIT + " where v.organisation_id=%s and v.registered_by=%s and v.idempotency_key=%s", (context.id, context.user.id, idempotency_key))
        if prior:
            if prior["payload_hash"] != payload_hash:
                fail("idempotency_conflict", 409)
            # Reassignment must not make a replay reveal another agent's visit.
            visible = await visit(connection, context, prior["id"])
            response.status_code = 200
            return serialize_visit(visible)
        assigned = None if context.admin else context.user.id
        status = "Assigned" if assigned else ("Waiting for Assignment" if body.appointmentStatus == "Walk-in" else "Registered")
        row = await one(connection, """insert into public.guruconnect_walkin_visits
            (organisation_id,registered_by,assigned_agent_id,assigned_at,idempotency_key,payload_hash,payload,customer_name,mobile_number,purpose,status)
            values(%s,%s,%s,case when %s::uuid is null then null else now() end,%s,%s,%s,%s,%s,%s,%s) returning id""",
                        (context.id, context.user.id, assigned, assigned, idempotency_key, payload_hash, Jsonb(payload), body.customerName, body.mobileNumber, body.purpose, status))
        await history(connection, context, row, "Registered", new_value=status)
        saved = await visit(connection, context, row["id"])
    return serialize_visit(saved)


@router.get("/{visit_id}")
async def get_visit(visit_id: UUID, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        row = await visit(connection, context, visit_id)
    return serialize_visit(row)


@router.patch("/{visit_id}/assignment")
async def assign_visit(visit_id: UUID, body: Assignment, request: Request, context: Workspace = Depends(administrator)):
    async with request.app.state.db.connection() as connection:
        row = await visit(connection, context, visit_id, lock=True)
        check_version(row, body.expectedUpdatedAt)
        if row["status"] in CLOSED:
            fail("walkin_closed", 409)
        if body.agentId:
            member = await one(connection, "select user_id from public.members where organisation_id=%s and user_id=%s and status='active' for share", (context.id, body.agentId))
            if not member:
                fail("walkin_agent_not_found", 404)
        if row["assigned_agent_id"] == body.agentId and row["assignment_note"] == body.note:
            return serialize_visit(row)
        status = row["status"] if row["assigned_agent_id"] == body.agentId else ("Assigned" if body.agentId else "Waiting for Assignment")
        await connection.execute("update public.guruconnect_walkin_visits set assigned_agent_id=%s,assigned_at=case when %s::uuid is null then null when assigned_agent_id=%s then assigned_at else clock_timestamp() end,assignment_note=%s,status=%s,updated_at=clock_timestamp() where id=%s and organisation_id=%s",
                                 (body.agentId, body.agentId, body.agentId, body.note, status, visit_id, context.id))
        await history(connection, context, row, "Assignment changed", str(row["assigned_agent_id"]) if row["assigned_agent_id"] else None, str(body.agentId) if body.agentId else None, body.note)
        saved = await visit(connection, context, visit_id)
    return serialize_visit(saved)


@router.patch("/{visit_id}/status")
async def change_status(visit_id: UUID, body: StatusChange, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        row = await visit(connection, context, visit_id, lock=True, editing=True)
        check_version(row, body.expectedUpdatedAt)
        if body.status not in TRANSITIONS[row["status"]]:
            fail("invalid_walkin_transition", 409)
        if body.status == "Requirements Captured" and not row["requirements_updated_at"]:
            fail("walkin_requirements_required", 409)
        await connection.execute("update public.guruconnect_walkin_visits set status=%s,updated_at=clock_timestamp() where id=%s and organisation_id=%s", (body.status, visit_id, context.id))
        await history(connection, context, row, "Status changed", row["status"], body.status)
        saved = await visit(connection, context, visit_id)
    return serialize_visit(saved)


@router.get("/{visit_id}/requirements")
async def get_requirements(visit_id: UUID, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        row = await visit(connection, context, visit_id)
    return serialize_requirement(row)


@router.get("/{visit_id}/recordings")
async def list_recordings(visit_id: UUID, request: Request, context: Workspace = Depends(workspace),
                          page: int = Query(default=1, ge=1, le=100000), pageSize: int = Query(default=100, ge=1, le=100)):
    async with request.app.state.db.connection() as connection:
        await visit(connection, context, visit_id)
        rows = await many(connection, """with matches as materialized (
            select c.* from public.guruconnect_walkin_recordings r
            join public.conversations c on c.id=r.conversation_id and c.organisation_id=r.organisation_id and c.owner_id=r.owner_id
            where r.organisation_id=%s and r.visit_id=%s and (%s or r.owner_id=%s) and c.deleted_at is null),
            totals as (select count(*) as total from matches), page_rows as (
            select * from matches order by created_at desc,id desc limit %s offset %s)
            select page_rows.*,totals.total from totals left join page_rows on true order by page_rows.created_at desc,page_rows.id desc""",
                          (context.id, visit_id, context.admin, context.user.id, pageSize, (page - 1) * pageSize))
    # Return only public metadata needed by the review UI, without provider
    # snapshots, hashes, lease values or internal processing configuration.
    fields = {"id", "customer_name_snapshot", "salesperson_name_snapshot", "title", "created_at", "duration_seconds", "total_samples", "capture_state", "upload_state", "transcription_state", "english_state", "speaker_state", "final_state", "source_language", "owner_id"}
    return {"items": [{key: value for key, value in row.items() if key in fields} for row in rows if row["id"]], "total": rows[0]["total"], "page": page, "pageSize": pageSize}


@router.post("/{visit_id}/recordings")
async def link_recording(visit_id: UUID, body: RecordingLink, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        row = await visit(connection, context, visit_id, lock=True, editing=True)
        await owned_conversation(connection, context, body.conversationId)
        prior = await one(connection, "select visit_id,organisation_id from public.guruconnect_walkin_recordings where conversation_id=%s", (body.conversationId,))
        if prior:
            if prior["visit_id"] != visit_id or prior["organisation_id"] != context.id:
                fail("recording_already_linked", 409)
            return {"ok": True}
        if row["status"] in CLOSED:
            fail("walkin_closed", 409)
        if row["assigned_agent_id"] != context.user.id:
            fail("walkin_assigned_agent_required", 403)
        already_enquiry = await one(connection, "select id from public.guruconnect_enquiries where conversation_id=%s", (body.conversationId,))
        if already_enquiry:
            fail("recording_already_linked", 409)
        await connection.execute("insert into public.guruconnect_walkin_recordings(organisation_id,visit_id,conversation_id,owner_id) values(%s,%s,%s,%s)", (context.id, visit_id, body.conversationId, context.user.id))
        await history(connection, context, row, "Recording attached", new_value=str(body.conversationId))
    return {"ok": True}


@router.put("/{visit_id}/requirements")
async def save_requirements(visit_id: UUID, body: RequirementSave, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        row = await visit(connection, context, visit_id, lock=True, editing=True)
        check_version(row, body.expectedUpdatedAt)
        if row["status"] in CLOSED:
            fail("walkin_closed", 409)
        if not row["assigned_agent_id"]:
            fail("walkin_assigned_agent_required", 409)
        if not (body.requirement.detailedRequirement or body.requirement.productOrService):
            fail("walkin_requirements_required", 422, "Add a customer requirement or product before saving.")
        status = "Requirements Captured" if "Requirements Captured" in TRANSITIONS[row["status"]] else row["status"]
        await connection.execute("update public.guruconnect_walkin_visits set requirements=%s,requirements_updated_at=clock_timestamp(),status=%s,updated_at=clock_timestamp() where id=%s and organisation_id=%s", (Jsonb(body.requirement.model_dump()), status, visit_id, context.id))
        await history(connection, context, row, "Requirements updated")
        if status != row["status"]:
            await history(connection, context, row, "Status changed", row["status"], status)
        saved = await visit(connection, context, visit_id)
    return serialize_requirement(saved)


async def conversion_visit(connection, context: Workspace, source_event_id: str):
    """Called by enquiry creation before insert, using the same transaction."""
    if not source_event_id.startswith("walkin:"):
        return None
    try:
        visit_id = UUID(source_event_id.removeprefix("walkin:"))
    except ValueError:
        fail("invalid_walkin_source", 422)
    # UUID strings must use one canonical spelling for the unique source index.
    if source_event_id != f"walkin:{visit_id}":
        fail("invalid_walkin_source", 422)
    row = await visit(connection, context, visit_id, lock=True, editing=True)
    if row["assigned_agent_id"] != context.user.id:
        fail("walkin_assigned_agent_required", 403)
    if row["converted_enquiry_id"]:
        fail("walkin_already_converted", 409)
    if row["status"] not in {"Requirements Captured", "Follow-up Required"} or not row["requirements_updated_at"]:
        fail("walkin_requirements_required", 409)
    return row


async def complete_conversion(connection, context: Workspace, row, enquiry_id):
    if row is None:
        return
    await connection.execute("update public.guruconnect_walkin_visits set converted_enquiry_id=%s,status='Converted to Enquiry',updated_at=clock_timestamp() where id=%s and organisation_id=%s", (enquiry_id, row["id"], context.id))
    await history(connection, context, row, "Converted to enquiry", new_value=str(enquiry_id))


async def validate_conversion_recording(connection, context: Workspace, row, conversation_id):
    if conversation_id is None:
        return
    if row is None:
        existing = await one(connection, "select visit_id from public.guruconnect_walkin_recordings where organisation_id=%s and conversation_id=%s and owner_id=%s", (context.id, conversation_id, context.user.id))
        if existing:
            fail("walkin_conversion_required", 409, "Create this recording's enquiry from its assigned walk-in visit.")
        return
    link = await one(connection, "select conversation_id from public.guruconnect_walkin_recordings where organisation_id=%s and visit_id=%s and conversation_id=%s and owner_id=%s", (context.id, row["id"], conversation_id, context.user.id))
    if not link:
        fail("walkin_recording_mismatch", 409, "Choose a saved recording attached to this visit.")
