"""Idempotent Bigin/Books contact and lead provisioning for ConnectCosmos enquiries."""

from __future__ import annotations

import re
import logging
from typing import Any

from .crm_lead import find_contacts, normalize_email, normalize_phone, split_name

log = logging.getLogger(__name__)


def _rows(payload: Any) -> list[dict]:
    data = payload.get("data") if isinstance(payload, dict) else None
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _first(payload: Any) -> dict | None:
    rows = _rows(payload)
    if rows:
        row = rows[0]
        # Bigin write responses return {data: [{details: {id: ...}}]} while
        # read responses return {data: [{id: ...}]}. Normalize both forms.
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        return {**details, **row} if details else row
    return payload if isinstance(payload, dict) and payload.get("id") else None


async def ensure_bigin_contact(zoho, *, name: str, phone: str = "", email: str = "", details: dict | None = None) -> dict:
    """Find an exact phone/email match, otherwise create a Bigin contact."""
    matched_by, contacts = await find_contacts(zoho, phone, email)
    if contacts:
        contact = contacts[0]
        # Reuse the matched contact. Complete only missing identity fields;
        # never replace a value already stored in Bigin with a new guess.
        updates = {}
        if email and not any(normalize_email(contact.get(field)) for field in ("Email", "Secondary_Email")):
            updates["Email"] = normalize_email(email)
        if phone and not any(normalize_phone(contact.get(field)) for field in ("Phone", "Mobile", "Home_Phone")):
            updates["Phone"] = phone.strip()
        if updates:
            updated = _first(await zoho.update_bigin_contact(str(contact["id"]), updates))
            if updated:
                contact = {**contact, **updated, **updates}
        return contact
    first, last = split_name(name)
    details = details if isinstance(details, dict) else {}
    payload = {
        "First_Name": first or "Customer",
        "Last_Name": last or first or "Enquiry",
        "Email": normalize_email(email) or None,
        "Phone": phone.strip() or None,
        "Description": details.get("description") or "Created from ConnectCosmos visitor enquiry",
    }
    for key in ("Title", "Mailing_Street", "Mailing_City", "Mailing_State", "Mailing_Country", "Mailing_Zip"):
        if details.get(key):
            payload[key] = str(details[key])[:200]
    payload = {key: value for key, value in payload.items() if value not in (None, "")}
    created = _first(await zoho.create_bigin_contact(payload))
    if not created or not created.get("id"):
        raise RuntimeError("Bigin contact creation returned no contact id")
    return created


async def provision_enquiry_lead(zoho, *, name: str, phone: str = "", email: str = "", details: dict | None = None,
                                 contact_type: str = "customer", pipeline_id: str | None = None, stage: str | None = None) -> dict:
    """Create/link Bigin and Books contacts, then create the Bigin pipeline record."""
    details = details if isinstance(details, dict) else {}
    books_contact = None
    books_error = None
    try:
        status = zoho.status()
        books_org = status.get("organization_id") if status.get("connected") else None
        if books_org and (phone or email):
            books_contact = await zoho.ensure_books_contact(books_org, name=name, phone=phone, email=email, contact_type=contact_type)
    except Exception as error:
        books_error = type(error).__name__
        log.warning("books_contact_provision_failed exception_type=%s", books_error)
    contact = await ensure_bigin_contact(zoho, name=name, phone=phone, email=email, details=details)
    if not pipeline_id:
        layouts = zoho_payload = await zoho.pipeline_layout()
        data = zoho_payload.get("layouts", zoho_payload.get("data", [])) if isinstance(zoho_payload, dict) else []
        layout = next((item for item in data if item.get("id")), None)
        pipeline_id = str(layout.get("id")) if layout else None
        if not stage and layout:
            for section in layout.get("sections", []):
                for field in section.get("fields", []):
                    if field.get("api_name") == "Stage":
                        values = field.get("pick_list_values") or []
                        stage = next((item.get("actual_value") or item.get("display_value") for item in values if item.get("actual_value") or item.get("display_value")), None)
    if not pipeline_id:
        raise RuntimeError("Bigin has no pipeline layout available")
    payload = {
        "Deal_Name": details.get("deal_name") or f"{name.strip() or 'Customer'} - New enquiry",
        "Stage": stage or "New Enquiry",
        "Pipeline": {"id": str(pipeline_id)},
        "Contact_Name": {"id": str(contact["id"])},
        "Description": details.get("description") or "Created from ConnectCosmos visitor enquiry",
    }
    if details.get("amount") not in (None, ""):
        payload["Amount"] = float(details["amount"])
    created = _first(await zoho.create_pipeline(payload))
    if not created or not created.get("id"):
        raise RuntimeError("Bigin pipeline creation returned no lead id")
    return {"contact": contact, "books_contact": books_contact, "books_error": books_error,
            "record": created, "pipeline_id": str(pipeline_id)}
