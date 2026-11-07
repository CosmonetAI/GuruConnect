"""Workspace-authorized walk-ins; browser storage is never a hosted fallback."""

import base64
import hashlib
import json
import logging
import re
import secrets
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .db import many, one
from .errors import fail
from .routes import audit
from .security import Workspace, administrator, owned_conversation, workspace
from .assignment import current_ticket_day, format_ticket_number, least_loaded_salesperson, next_daily_ticket
from app.zoho import set_member_id, set_organisation_id, zoho
from app.crm_provisioning import provision_enquiry_lead

router = APIRouter(prefix="/api/v1/walkins", tags=["walkins"])
log = logging.getLogger(__name__)
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

VOICE_FIELD_PROMPTS = {
    "name": "the person's full name",
    "phone": "the person's phone number",
    "email": "the person's email address",
    "organisation": "the person's organisation or company name",
    "requirement": "the person's project or purchase requirement",
    "customerType": "whether the person is here to buy/customer or sell/vendor",
}

PHONE_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def _phone_digits(value: object) -> str:
    """Normalize model/transcript phone output, including spoken digit sequences."""
    text = str(value or "").strip().lower()
    direct = re.sub(r"\D", "", text)
    if len(direct) >= 7:
        return direct[-15:]
    tokens = re.findall(r"[a-z]+", text)
    spoken = "".join(PHONE_WORDS[token] for token in tokens if token in PHONE_WORDS)
    return spoken[-15:] if len(spoken) >= 7 else ""

class VoiceFieldCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    field: str
    audioBase64: str = Field(min_length=1, max_length=8_000_000)
    contentType: str = Field(default="audio/webm", max_length=100)


def safe_text(value):
    if isinstance(value, str) and any(character == "\x00" or 0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError("Text must contain valid Unicode without null characters")
    return value


@router.post("/voice-field")
async def extract_voice_field(
    body: VoiceFieldCreate,
    request: Request,
    context: Workspace = Depends(workspace),
):
    """Extract one registration value from a transient microphone clip.

    The clip is held in memory only for the provider request. It is never
    attached to a conversation, uploaded to storage, or written to Supabase.
    """
    field = body.field
    if field not in VOICE_FIELD_PROMPTS:
        fail("voice_field_invalid", 422, "This registration field cannot be extracted from voice")
    settings = request.app.state.settings
    if not settings.openai_api_key:
        fail("voice_extraction_unconfigured", 503, "OpenAI voice extraction is not configured")
    try:
        content = base64.b64decode(body.audioBase64, validate=True)
    except ValueError:
        fail("voice_field_audio_invalid", 422, "Record a short voice answer and try again")
    if not content or len(content) > 6_000_000:
        fail("voice_field_audio_invalid", 422, "Record a short voice answer and try again")
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value(), max_retries=1)
        transcript = await client.audio.transcriptions.create(
            model="whisper-1",
            file=(f"walkin-{field}.webm", content, body.contentType or "audio/webm"),
            response_format="text",
        )
        spoken = str(getattr(transcript, "text", transcript) or "").strip()
        if not spoken:
            fail("voice_field_empty", 422, "No speech was detected. Try again")
        extraction = await client.chat.completions.create(
            model=settings.openai_translation_model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "Extract exactly one registration answer. Return only JSON with a string key value. For customerType, value must be exactly customer or vendor. Never invent missing details."},
                {"role": "user", "content": f"Field: {VOICE_FIELD_PROMPTS[field]}\nSpoken answer: {spoken}"},
            ],
        )
        raw = extraction.choices[0].message.content or "{}"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        value = parsed.get("value")
        if value in (None, ""):
            value = next((parsed.get(key) for key in ("phone", "phone_number", "mobile", "number", "answer") if parsed.get(key) not in (None, "")), "")
        if field == "phone":
            value = _phone_digits(value) or _phone_digits(spoken)
        if field == "customerType":
            value = "vendor" if str(value).strip().lower() == "vendor" else "customer"
        else:
            value = str(value).strip()
        if not value:
            fail("voice_field_unresolved", 422, "The answer could not be understood. Try again")
        return {"field": field, "value": value, "transcript": spoken}
    except Exception as error:
        if getattr(error, "status_code", None):
            raise
        log.warning("walkin_voice_field_failed field=%s exception_type=%s", field, type(error).__name__)
        fail("voice_field_failed", 502, "Voice extraction failed. Try again")


def _walkin_extracted_fields(extractions, context):
    """Flatten only grounded registration fields for the live form preview."""
    fields = {}
    aliases = {
        "name": "customerName", "contact_name": "customerName", "full_name": "customerName",
        "phone": "mobileNumber", "contact_phone": "mobileNumber", "mobile": "mobileNumber",
        "email": "email", "contact_email": "email", "company": "companyName",
        "contact_company": "companyName", "organisation": "companyName", "organization": "companyName",
        "project": "projectName", "requirement": "projectName", "active_topic": "projectName",
        "customer_type": "customerType", "contact_type": "customerType",
    }

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                target = aliases.get(str(key).lower())
                if target and isinstance(item, (str, int, float)) and str(item).strip():
                    text = str(item).strip()
                    fields[target] = ("vendor" if text.lower() in {"vendor", "sell", "selling"} else "customer") if target == "customerType" else text
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for row in extractions:
        if row.get("status") == "complete":
            visit(row.get("data") or {})
    visit(context or {})
    return fields


@router.get("/{conversation_id}/walkin-preview")
async def walkin_preview(conversation_id: UUID, request: Request, context: Workspace = Depends(workspace)):
    """Return the safe, progressively available transcript/extraction preview for registration."""
    async with request.app.state.db.connection() as connection:
        recording = await owned_conversation(connection, context, conversation_id)
        version = None
        turns = []
        if recording.get("active_version_id"):
            version = await one(connection, "select id,english_state,state,coverage_samples from public.transcript_versions where id=%s and conversation_id=%s", (recording["active_version_id"], conversation_id))
            if version:
                turns = await many(connection, "select ordinal,start_seconds,end_seconds,source_text,english_text,source_language,speaker_ref from public.transcript_turns where version_id=%s order by ordinal", (version["id"],))
        extractions = await many(connection, "select status,data,created_at from public.conversation_extractions where conversation_id=%s order by created_at desc limit 20", (conversation_id,))
        context_row = await one(connection, "select context from public.voice_ai_contexts where conversation_id=%s", (conversation_id,))
    public_extractions = [{"status": row.get("status"), "data": row.get("data") or {}, "created_at": row.get("created_at")} for row in extractions]
    fields = _walkin_extracted_fields(extractions, (context_row or {}).get("context") if context_row else {})
    return {
        "conversation": {key: recording.get(key) for key in ("id", "capture_state", "upload_state", "transcription_state", "english_state", "final_state", "duration_seconds", "active_version_id")},
        "version": version,
        "transcript": turns,
        "extractions": public_extractions,
        "fields": fields,
    }


class WalkinCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    customerName: Annotated[str, Field(min_length=2, max_length=150)]
    mobileNumber: Annotated[str, Field(min_length=7, max_length=20)]
    email: Annotated[str, Field(max_length=320)] = ""
    companyName: Annotated[str, Field(max_length=150)] = ""
    projectName: Annotated[str, Field(max_length=150)] = ""
    customerType: Literal["customer", "vendor"] = "customer"
    assignedAgentId: UUID | None = None
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


async def ensure_walkin_books_contact(body: WalkinCreate, context: Workspace):
    """Register a walk-in in the selected Books organization when available."""
    set_organisation_id(str(context.id))
    set_member_id(str(context.user.id))
    status = zoho.status()
    books_org = status.get("organization_id")
    if not status.get("connected") or not books_org:
        return None
    contact = await zoho.ensure_books_contact(
        books_org,
        name=body.customerName,
        email=body.email,
        phone=body.mobileNumber,
        contact_type=body.customerType,
    )
    if not isinstance(contact, dict):
        return None
    return contact.get("contact_id") or contact.get("id")


async def provision_walkin_crm(connection, context, enquiry_id, body):
    """Create/link the visitor in Bigin after the local registration is saved."""
    try:
        result = await provision_enquiry_lead(
            zoho, name=body.customerName, phone=body.mobileNumber, email=body.email or "", contact_type=body.customerType,
            details={"description": body.purposeDescription or body.internalNotes or "Created from ConnectCosmos visitor registration"},
        )
        contact, record = result["contact"], result["record"]
        books_contact = result.get("books_contact") or {}
        books_contact_id = books_contact.get("contact_id") or books_contact.get("id")
        await connection.execute("""update public.guruconnect_enquiries
            set bigin_contact_id=%s,bigin_pipeline_id=%s,bigin_deal_id=%s,books_contact_id=coalesce(%s,books_contact_id),crm_sync_error=null
            where id=%s and organisation_id=%s""",
            (str(contact.get("id")), result["pipeline_id"], str(record.get("id")), books_contact_id, enquiry_id, context.id))
        return result
    except Exception as error:
        log.warning("walkin_bigin_provision_failed enquiry_id=%s exception_type=%s", enquiry_id, type(error).__name__)
        await connection.execute("update public.guruconnect_enquiries set crm_sync_error=%s where id=%s and organisation_id=%s", (type(error).__name__, enquiry_id, context.id))
        return None


SELECT_VISIT = """select v.*,p.display_name as assigned_agent_name,
                         e.ticket_day,e.daily_sequence
    from public.guruconnect_walkin_visits v
    left join public.profiles p on p.id=v.assigned_agent_id
    left join public.guruconnect_enquiries e
      on e.id=v.converted_enquiry_id and e.organisation_id=v.organisation_id """
ACCESS = "(%s or v.assigned_agent_id=%s or v.registered_by=%s)"


def serialize_visit(row):
    result = dict(row["payload"])
    ticket_day = row.get("ticket_day")
    daily_sequence = row.get("daily_sequence")
    visit_number = (
        format_ticket_number(ticket_day, daily_sequence)
        if ticket_day is not None and daily_sequence is not None
        else f"WI-{row['created_at']:%d%m%Y}-{row['visit_number']:06d}"
    )
    result.update(id=str(row["id"]), visitNumber=visit_number,
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


def missed_cutoff(now_local: datetime | None = None) -> datetime:
    """Return the most recent 9 PM India cutoff for the daily rollup."""
    current = now_local or datetime.now(ZoneInfo("Asia/Kolkata"))
    cutoff = current.replace(hour=21, minute=0, second=0, microsecond=0)
    return cutoff if current >= cutoff else cutoff - timedelta(days=1)


async def rollup_missed_visitors(connection, cutoff: datetime | None = None) -> int:
    """Snapshot visits with no linked recording and purge expired snapshots.

    The unique visit key makes this safe when the nightly task and a page load
    happen at the same time.
    """
    await connection.execute("delete from public.guruconnect_missed_visitors where expires_at <= now()")
    rows = await many(connection, """
        select v.*,
               coalesce(v.assigned_agent_id, v.registered_by) as member_id
        from public.guruconnect_walkin_visits v
        where v.check_in_at < %s
          and v.status <> 'Cancelled'
          and not exists (
            select 1 from public.guruconnect_walkin_recordings r
            where r.organisation_id=v.organisation_id and r.visit_id=v.id
          )
          and not exists (
            select 1 from public.guruconnect_missed_visitors m
            where m.organisation_id=v.organisation_id and m.visit_id=v.id
          )
        order by v.check_in_at asc
        limit 5000
    """, (cutoff or missed_cutoff(),))
    for row in rows:
        payload = row["payload"] if isinstance(row["payload"], dict) else {}
        await connection.execute("""
            insert into public.guruconnect_missed_visitors
              (organisation_id,member_id,visit_id,registered_by,assigned_agent_id,
               visit_number,customer_name,mobile_number,email,purpose,payload,check_in_at)
            values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (organisation_id,visit_id) do nothing
        """, (row["organisation_id"], row["member_id"], row["id"], row["registered_by"],
              row["assigned_agent_id"], row["visit_number"], row["customer_name"],
              row["mobile_number"], payload.get("email", ""), row["purpose"], Jsonb(payload),
              row["check_in_at"]))
    return len(rows)


async def active_context(connection, context):
    if context.admin:
        return
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
        await connection.execute("""
            update public.profiles p
            set availability_status='inactive', final_status='inactive', final_status_until=null, updated_at=now()
            where coalesce(p.final_status, p.availability_status)='active'
              and (p.last_seen_at is null or p.last_seen_at < now()-interval '90 seconds')
              and not exists (select 1 from public.profile_presence_sessions s where s.user_id=p.id and s.last_seen_at >= now()-interval '90 seconds')
              and exists (select 1 from public.members m where m.organisation_id=%s and m.user_id=p.id and m.status='active')
        """, (context.id,))
        rows = await many(connection, """with candidates as (
            select m.user_id,m.role,p.display_name,coalesce(a.availability,'Available') as availability,
              case
                when coalesce(p.final_status, p.availability_status) in ('inactive','on_leave') then coalesce(p.final_status, p.availability_status)
                when coalesce(p.final_status, p.availability_status)='be_right_back' and p.updated_at > now()-interval '15 minutes' then 'be_right_back'
                when exists (select 1 from public.profile_presence_sessions ps where ps.user_id=p.id and ps.last_seen_at >= now()-interval '90 seconds') then 'active'
                else 'inactive'
              end as presence_status,
              count(distinct e.id) filter (where e.stage in ('new','in_process')) as open_tickets,
              case when %s or m.user_id=%s then count(distinct v.id) else null end as open_visits
            from public.members m join public.profiles p on p.id=m.user_id
            left join public.guruconnect_walkin_agents a on a.organisation_id=m.organisation_id and a.user_id=m.user_id
            left join public.guruconnect_walkin_visits v on v.organisation_id=m.organisation_id and v.assigned_agent_id=m.user_id and v.status not in ('Completed','Cancelled','Converted to Enquiry')
            left join public.guruconnect_enquiries e on e.organisation_id=m.organisation_id and e.assigned_to=m.user_id
            where m.organisation_id=%s and m.status='active' and m.role in ('owner','salesperson')
            group by m.user_id,m.role,p.id,p.display_name,a.availability,p.availability_status,p.final_status,p.final_status_until
          )
          select * from candidates c
          where c.role in ('salesperson','owner')
          order by case when c.role='salesperson' then 0 else 1 end,
            case c.presence_status when 'active' then 0 when 'on_leave' then 1 when 'be_right_back' then 2 else 3 end,
            case c.availability when 'Available' then 0 when 'Busy' then 1 else 2 end,
            c.open_tickets asc,c.display_name,c.user_id limit 1000""", (context.admin, context.user.id, context.id))
    return {"items": [{"id": str(row["user_id"]), "name": row["display_name"] or "Salesperson", "department": "Sales", "specialization": "", "role": row["role"], "availability": row["availability"], "presenceStatus": row["presence_status"], "openVisits": row["open_visits"], "openTickets": row["open_tickets"]} for row in rows],
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
        and (v.customer_name ilike %s or v.mobile_number ilike %s
             or ('WI-'||to_char(coalesce(e.ticket_day,(v.check_in_at at time zone 'Asia/Kolkata')::date),'DDMMYYYY')||'-'||lpad(coalesce(e.daily_sequence,v.visit_number)::text,6,'0')) ilike %s)
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
    payload = body.model_dump(mode="json")
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
        ticket_day = current_ticket_day(request.app.state.settings.app_timezone)
        salesperson = None
        if body.assignedAgentId:
            if context.role != "receptionist":
                fail("walkin_assignment_not_allowed", 403)
            salesperson = await one(connection, """
                select m.user_id,coalesce(p.display_name,'') as display_name,coalesce(u.email,'') as email,m.role
                from public.members m
                join public.profiles p on p.id=m.user_id
                join auth.users u on u.id=m.user_id
                where m.organisation_id=%s and m.user_id=%s and m.role='owner' and m.status='active'
                for share of m
            """, (context.id, body.assignedAgentId))
            if not salesperson:
                fail("walkin_assignment_not_allowed", 422, "Reception can assign a visitor to Automatic assignment or an owner.")
        else:
            salesperson = await least_loaded_salesperson(connection, context.id, ticket_day)
        assigned = salesperson["user_id"] if salesperson else None
        status = "Assigned" if assigned else "Waiting for Assignment"
        row = await one(connection, """insert into public.guruconnect_walkin_visits
            (organisation_id,registered_by,assigned_agent_id,assigned_at,idempotency_key,payload_hash,payload,customer_name,mobile_number,purpose,status)
            values(%s,%s,%s,case when %s::uuid is null then null else now() end,%s,%s,%s,%s,%s,%s,%s) returning id""",
                        (context.id, context.user.id, assigned, assigned, idempotency_key, payload_hash, Jsonb(payload), body.customerName, body.mobileNumber, body.purpose, status))
        daily_sequence = await next_daily_ticket(connection, context.id, ticket_day)
        tracking_token = secrets.token_urlsafe(12)
        tracking_hash = hashlib.sha256(tracking_token.encode()).hexdigest()
        ticket_payload = {
            "customerName": body.customerName, "phone": body.mobileNumber, "email": body.email,
            "channel": "Retail walk-in", "branch": body.department or "Walk-in",
            "assignedExecutive": salesperson["display_name"] if salesperson else "Unassigned",
            "product": body.productOrService, "quantity": 1, "unit": "nos", "notes": body.internalNotes or body.purposeDescription,
            "sourceEventId": f"walkin:{row['id']}", "sourceWalkinId": str(row["id"]), "customerType": "Vendor" if body.customerType == "vendor" else "Retail customer",
            "company": body.companyName, "projectName": body.projectName,
            "priority": "Normal"
        }
        ticket_payload["ticketNumber"] = daily_sequence
        ticket_hash = hashlib.sha256(json.dumps(ticket_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        books_contact_id = None
        try:
            books_contact_id = await ensure_walkin_books_contact(body, context)
        except Exception as error:
            # Visitor registration remains available when Books is disconnected or temporarily unavailable.
            log.warning("walkin_books_contact_unavailable visit_id=%s exception_type=%s", row["id"], type(error).__name__)
        enquiry = await one(connection, """insert into public.guruconnect_enquiries
            (organisation_id,owner_id,idempotency_key,payload_hash,payload,customer_name,phone,channel,branch,assigned_to,stage,unread,ticket_day,daily_sequence,public_token_hash,books_contact_id)
            values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'new',true,%s,%s,%s,%s) returning id""",
            (context.id, context.user.id, f"walkin-enquiry:{row['id']}", ticket_hash, Jsonb(ticket_payload), body.customerName, body.mobileNumber, "Retail walk-in", body.department or "Walk-in", salesperson["user_id"] if salesperson else None, ticket_day, daily_sequence, tracking_hash, books_contact_id))
        await connection.execute("update public.guruconnect_walkin_visits set converted_enquiry_id=%s where id=%s and organisation_id=%s", (enquiry["id"], row["id"], context.id))
        await history(connection, context, row, "Registered", new_value=status)
        saved = await visit(connection, context, row["id"])
        crm_result = await provision_walkin_crm(connection, context, enquiry["id"], body)
    result = serialize_visit(saved)
    result["ticket"] = {"number": format_ticket_number(ticket_day, daily_sequence), "token": tracking_token, "stage": "new"}
    if crm_result:
        result["crm"] = {"contactId": str(crm_result["contact"].get("id")), "leadId": str(crm_result["record"].get("id"))}
    return result


@router.get("/missed-visitors")
async def list_missed_visitors(request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        await rollup_missed_visitors(connection)
        rows = await many(connection, """
            select m.*, p.display_name as assigned_agent_name,
                   e.daily_sequence, e.ticket_day
            from public.guruconnect_missed_visitors m
            left join public.profiles p on p.id=m.assigned_agent_id
            left join public.guruconnect_walkin_visits v
              on v.id=m.visit_id and v.organisation_id=m.organisation_id
            left join public.guruconnect_enquiries e
              on e.id=v.converted_enquiry_id and e.organisation_id=v.organisation_id
            where m.organisation_id=%s
              and m.expires_at > now()
              and (%s or m.member_id=%s or m.registered_by=%s or m.assigned_agent_id=%s)
              and not exists (
                select 1 from public.guruconnect_walkin_recordings r
                where r.organisation_id=m.organisation_id and r.visit_id=m.visit_id
              )
            order by m.missed_at desc, m.id desc
            limit 200
        """, (context.id, context.admin, context.user.id, context.user.id, context.user.id))
        followups = await many(connection, """select e.id,e.customer_name,e.phone,e.payload,e.created_at,
                    e.assigned_to,e.follow_up_required
             from public.guruconnect_enquiries e
             where e.organisation_id=%s and e.follow_up_required
               and e.stage <> 'closed'
               and (%s or e.assigned_to=%s or e.owner_id=%s)
             order by e.created_at desc limit 200""", (context.id, context.admin, context.user.id, context.user.id))
    timezone_name = request.app.state.settings.app_timezone
    items = [{
        "id": str(row["id"]), "visitId": str(row["visit_id"]),
        "visitNumber": (
            format_ticket_number(row["ticket_day"], row["daily_sequence"])
            if row["ticket_day"] is not None and row["daily_sequence"] is not None
            else f"WI-{row['check_in_at']:%d%m%Y}-{row['visit_number']:06d}"
        ),
        "ticketNumber": format_ticket_number(row["ticket_day"], row["daily_sequence"]) if row["ticket_day"] is not None and row["daily_sequence"] is not None else None,
        "ticketDate": str(row["ticket_day"]) if row["ticket_day"] else row["check_in_at"].astimezone(ZoneInfo(timezone_name)).date().isoformat(),
        "ticketTime": row["check_in_at"].astimezone(ZoneInfo(timezone_name)).strftime("%H:%M:%S"),
        "customerName": row["customer_name"], "mobileNumber": row["mobile_number"],
        "email": row["email"], "purpose": row["purpose"], "payload": row["payload"],
        "assignedAgentName": row["assigned_agent_name"] or "Salesperson",
        "checkInAt": row["check_in_at"].isoformat(), "missedAt": row["missed_at"].isoformat(),
        "expiresAt": row["expires_at"].isoformat(),
        "followUp": False, "reason": "Walked in · no session started",
    } for row in rows]
    items.extend({
        "id": f"followup:{row['id']}", "visitId": str(row["id"]), "visitNumber": "Follow-up enquiry",
        "ticketNumber": None, "ticketDate": row["created_at"].date().isoformat(),
        "ticketTime": row["created_at"].strftime("%H:%M:%S"), "customerName": row["customer_name"],
        "mobileNumber": row["phone"], "email": (row["payload"] or {}).get("email", ""),
        "purpose": (row["payload"] or {}).get("notes") or "No business intent detected",
        "assignedAgentName": (row["payload"] or {}).get("assignedExecutive") or "Salesperson",
        "checkInAt": row["created_at"].isoformat(), "missedAt": row["created_at"].isoformat(),
        "expiresAt": (row["created_at"] + timedelta(days=14)).isoformat(), "payload": row["payload"] or {}, "followUp": True,
        "reason": "Nothing fruitful captured",
    } for row in followups)
    return {"items": items}


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
        if body.agentId and not row["converted_enquiry_id"]:
            from .enquiry_routes import create_walkin_enquiry
            agent = await one(connection, "select display_name from public.profiles where id=%s", (body.agentId,))
            enquiry_id = await create_walkin_enquiry(connection, context, row, body.agentId, agent["display_name"] if agent else "Salesperson", request.app.state.settings.app_timezone)
            await connection.execute("update public.guruconnect_walkin_visits set converted_enquiry_id=%s where id=%s and organisation_id=%s", (enquiry_id, visit_id, context.id))
        elif body.agentId and row["converted_enquiry_id"]:
            agent = await one(connection, "select display_name from public.profiles where id=%s", (body.agentId,))
            await connection.execute("""update public.guruconnect_enquiries
                set assigned_to=%s,
                    payload=jsonb_set(payload,'{assignedExecutive}',to_jsonb(%s::text),true)
                where id=%s and organisation_id=%s""",
                (body.agentId, agent["display_name"] if agent else "Salesperson", row["converted_enquiry_id"], context.id))
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
            select c.*, c.active_version_id as transcript_version_id,
                   coalesce(active_transcript.turns, '[]'::jsonb) as transcript_turns
            from public.guruconnect_walkin_recordings r
            join public.conversations c on c.id=r.conversation_id and c.organisation_id=r.organisation_id and c.owner_id=r.owner_id
            left join lateral (
              select v.id as version_id,
                     jsonb_agg(jsonb_build_object(
                       'id', t.id,
                       'ordinal', t.ordinal,
                       'start_seconds', t.start_seconds,
                       'end_seconds', t.end_seconds,
                       'source_text', t.source_text,
                       'english_text', t.english_text,
                       'source_language', t.source_language,
                       'speaker_ref', t.speaker_ref,
                       'speaker_confidence', t.speaker_confidence
                     ) filter (where t.id is not null) order by t.ordinal) as turns
              from public.transcript_versions v
              left join public.transcript_turns t on t.version_id=v.id
              where v.id=c.active_version_id
              group by v.id
            ) active_transcript on true
            where r.organisation_id=%s and r.visit_id=%s and (%s or r.owner_id=%s) and c.deleted_at is null),
            totals as (select count(*) as total from matches), page_rows as (
            select * from matches order by created_at desc,id desc limit %s offset %s)
            select page_rows.*,totals.total from totals left join page_rows on true order by page_rows.created_at desc,page_rows.id desc""",
                          (context.id, visit_id, context.admin, context.user.id, pageSize, (page - 1) * pageSize))
    # Return only public metadata needed by the review UI, without provider
    # snapshots, hashes, lease values or internal processing configuration.
    fields = {"id", "customer_name_snapshot", "salesperson_name_snapshot", "title", "created_at", "duration_seconds", "total_samples", "capture_state", "upload_state", "transcription_state", "english_state", "speaker_state", "final_state", "language_hint", "owner_id", "active_version_id", "transcript_version_id"}
    items = []
    for row in rows:
        if not row["id"]:
            continue
        item = {key: value for key, value in row.items() if key in fields}
        item["transcript"] = row.get("transcript_turns") or []
        items.append(item)
    return {"items": items, "total": rows[0]["total"], "page": page, "pageSize": pageSize}


@router.post("/{visit_id}/recordings")
async def link_recording(visit_id: UUID, body: RecordingLink, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        row = await visit(connection, context, visit_id, lock=True)
        recording = await owned_conversation(connection, context, body.conversationId)
        reception_capture = context.role == "receptionist" and row["registered_by"] == context.user.id and recording["audio_bucket"] == "walkin-audio"
        if row["assigned_agent_id"] != context.user.id and not reception_capture:
            fail("walkin_assigned_agent_required", 403)
        if recording["upload_state"] != "saved" or recording["total_samples"] <= 0:
            fail("recording_not_saved", 409)
        prior = await one(connection, "select visit_id,organisation_id from public.guruconnect_walkin_recordings where conversation_id=%s", (body.conversationId,))
        if prior:
            if prior["visit_id"] != visit_id or prior["organisation_id"] != context.id:
                fail("recording_already_linked", 409)
            return {"ok": True}
        if row["status"] in CLOSED:
            fail("walkin_closed", 409)
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
    if row["status"] in CLOSED:
        fail("walkin_already_converted", 409)
    if row["status"] not in {"Requirements Captured", "Follow-up Required"} or not row["requirements_updated_at"]:
        fail("walkin_requirements_required", 409)
    if row["converted_enquiry_id"]:
        # Registration already issues the visitor's ticket. A linked queue item
        # is not an explicit requirements conversion until the visit says so.
        enquiry = await one(connection, "select * from public.guruconnect_enquiries where id=%s and organisation_id=%s for update", (row["converted_enquiry_id"], context.id))
        if not enquiry or not (
            enquiry["payload"].get("sourceEventId") == source_event_id
            or (enquiry["payload"].get("sourceWalkinId") == str(visit_id)
                and enquiry["idempotency_key"] == f"walkin-assignment:{visit_id}")
        ):
            fail("walkin_enquiry_mismatch", 409)
        row["conversion_enquiry"] = enquiry
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
