"""TeleCRM webhook ingestion.

TeleCRM is currently an inbound source only. These routes authenticate the
shared key, validate the small set of fields needed to identify a delivery,
and persist both normalized columns and the complete source payload. Sending
messages back to TeleCRM is intentionally left for a later integration.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any

from fastapi import APIRouter, Header, Request
from psycopg.types.json import Jsonb

from .db import one
from .errors import fail

router = APIRouter(prefix="/api/webhooks/telecrm", tags=["telecrm"])

EVENTS = {
    "call_recording_available",
    "whatsapp_message_received",
    "whatsapp_message_status",
}


def _value(payload: dict[str, Any], *names: str) -> str | None:
    for name in names:
        value = payload.get(name)
        if value is not None:
            value = str(value).strip()
            if value:
                return value
    return None


def _required(payload: dict[str, Any], *names: str) -> str:
    value = _value(payload, *names)
    if not value:
        fail("telecrm_required_field", 422, field=names[0])
    return value


def _phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return digits[-15:] or None


def _dedupe_key(event: str, payload: dict[str, Any]) -> tuple[str, str | None]:
    external_id = _value(payload, "call_id") if event == "call_recording_available" else _value(payload, "message_id")
    # Status updates for the same message are distinct events when their
    # status changes, while retries of an identical status remain idempotent.
    status = _value(payload, "status") if event == "whatsapp_message_status" else None
    identity = ":".join(item for item in (external_id, status) if item)
    if identity:
        return f"{event}:{identity}", external_id
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return f"{event}:payload:{hashlib.sha256(canonical).hexdigest()}", None


def _auth_value(authorization: str | None, x_telecrm_key: str | None) -> str:
    if x_telecrm_key:
        return x_telecrm_key.strip()
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    return ""


def _authenticate(request: Request, authorization: str | None, x_telecrm_key: str | None) -> None:
    configured = getattr(request.app.state.settings, "telecrm_key", None)
    expected = configured.get_secret_value() if configured else ""
    if not expected:
        fail("telecrm_webhook_not_configured", 503)
    supplied = _auth_value(authorization, x_telecrm_key)
    if not supplied or not hmac.compare_digest(supplied, expected):
        fail("telecrm_authentication_failed", 401)


def _validate(event: str, payload: dict[str, Any]) -> None:
    if event not in EVENTS:
        fail("telecrm_unsupported_event", 422, event=event or None)
    if event == "call_recording_available":
        _required(payload, "recording_url")
        _required(payload, "customer_phone")
        _required(payload, "call_started_at")
        _required(payload, "call_ended_at")
        _required(payload, "duration_seconds")
    elif event == "whatsapp_message_received":
        _required(payload, "customer_phone")
        _required(payload, "message_text")
        _required(payload, "received_at")
    else:
        _required(payload, "customer_phone")
        _required(payload, "status")


async def _store(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    event = _value(payload, "event") or ""
    _validate(event, payload)
    dedupe_key, external_id = _dedupe_key(event, payload)
    customer_phone = _required(payload, "customer_phone")
    message_text = _value(payload, "message_text", "message")
    values = (
        event,
        external_id,
        dedupe_key,
        _value(payload, "call_id"),
        _value(payload, "message_id"),
        _value(payload, "conversation_id"),
        _value(payload, "lead_id"),
        customer_phone,
        _phone(customer_phone),
        _value(payload, "customer_name"),
        _value(payload, "agent_id"),
        _value(payload, "agent_name"),
        _value(payload, "agent_email"),
        _value(payload, "direction"),
        _value(payload, "recording_url"),
        _value(payload, "call_started_at"),
        _value(payload, "call_ended_at"),
        _value(payload, "duration_seconds"),
        _value(payload, "call_status"),
        _value(payload, "call_summary"),
        _value(payload, "call_transcript"),
        _value(payload, "call_outcome"),
        _value(payload, "customer_requirements"),
        _value(payload, "customer_sentiment"),
        _value(payload, "next_action"),
        _value(payload, "follow_up_required"),
        _value(payload, "message_type"),
        message_text,
        _value(payload, "media_url"),
        _value(payload, "received_at"),
        _value(payload, "sent_at"),
        _value(payload, "delivered_at"),
        _value(payload, "read_at"),
        _value(payload, "failed_at"),
        _value(payload, "status"),
        _value(payload, "failure_reason"),
        Jsonb(payload),
    )
    async with request.app.state.db.connection() as connection:
        row = await one(connection, """
            insert into public.telecrm_events(
              event_type,external_id,dedupe_key,call_id,message_id,conversation_id,lead_id,
              customer_phone,normalized_customer_phone,customer_name,agent_id,agent_name,agent_email,
              direction,recording_url,call_started_at,call_ended_at,duration_seconds,call_status,
              call_summary,call_transcript,call_outcome,customer_requirements,customer_sentiment,
              next_action,follow_up_required,message_type,message_text,media_url,received_at,sent_at,
              delivered_at,read_at,failed_at,status,failure_reason,payload
            ) values(
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            ) on conflict (dedupe_key) do nothing returning id
        """, values)
        duplicate = row is None
        if duplicate:
            row = await one(connection, "select id from public.telecrm_events where dedupe_key=%s", (dedupe_key,))
    if not row:
        fail("telecrm_event_not_persisted", 503)
    return {"accepted": True, "duplicate": duplicate, "event_id": str(row["id"]), "event": event}


async def ingest(
    payload: dict[str, Any],
    request: Request,
    authorization: str | None,
    x_telecrm_key: str | None,
) -> dict[str, Any]:
    _authenticate(request, authorization, x_telecrm_key)
    if not isinstance(payload, dict):
        fail("telecrm_payload_must_be_object", 422)
    return await _store(request, payload)


@router.post("")
async def telecrm_webhook(
    payload: dict[str, Any],
    request: Request,
    authorization: str | None = Header(default=None),
    x_telecrm_key: str | None = Header(default=None),
):
    """Receive all current TeleCRM events; the payload's event selects the type."""
    return await ingest(payload, request, authorization, x_telecrm_key)


@router.post("/{event_name}")
async def telecrm_webhook_alias(
    event_name: str,
    payload: dict[str, Any],
    request: Request,
    authorization: str | None = Header(default=None),
    x_telecrm_key: str | None = Header(default=None),
):
    """Convenience aliases for TeleCRM configurations that use one URL per event."""
    aliases = {
        "call-recording": "call_recording_available",
        "whatsapp-incoming": "whatsapp_message_received",
        "whatsapp-status": "whatsapp_message_status",
    }
    expected = aliases.get(event_name)
    _authenticate(request, authorization, x_telecrm_key)
    if expected and _value(payload, "event") != expected:
        fail("telecrm_event_path_mismatch", 422, event=expected)
    if not isinstance(payload, dict):
        fail("telecrm_payload_must_be_object", 422)
    return await _store(request, payload)
