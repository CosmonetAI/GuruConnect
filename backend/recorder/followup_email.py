"""Zoho Mail delivery for missed walk-in follow-ups.

The browser never receives the Zoho token.  Both the account lookup and the
message submission use the selected salesperson's server-side OAuth token.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .db import many, one
from .errors import fail
from .security import Workspace, workspace

router = APIRouter(prefix="/api/v1", tags=["follow-up-email"])
internal_router = APIRouter(tags=["internal-follow-up-email"])
log = logging.getLogger("recorder.followup_email")

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
SESSION_NOT_STARTED = "Session not started"
NOTHING_USEFUL_DETECTED = "Nothing useful detected"


class FollowUpEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    visitId: str = Field(min_length=1, max_length=80)
    ccAddress: str = Field(default="", max_length=2000)
    subject: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=50000)

    @field_validator("subject", "content")
    @classmethod
    def no_control_characters(cls, value: str) -> str:
        if any(character in value for character in "\x00\r"):
            raise ValueError("Email content contains an invalid control character")
        return value


def _email(value: Any) -> str:
    value = str(value or "").strip()
    if len(value) > 320 or not EMAIL_RE.fullmatch(value):
        return ""
    return value


def _addresses(value: str) -> list[str]:
    result = []
    for item in re.split(r"[,;\n]", value or ""):
        item = item.strip()
        if not item:
            continue
        if not _email(item):
            fail("invalid_cc_address", 422, "Enter valid CC email addresses")
        result.append(item)
    return result


def _provider_detail(response: httpx.Response) -> str:
    """Extract a short, non-sensitive reason from a Zoho error response."""
    try:
        payload = response.json()
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict):
        status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        for value in (status.get("description"), data.get("moreInfo"), data.get("message"), payload.get("message")):
            if value:
                return str(value)[:300]
    text = response.text.strip()
    return text[:300] if text else f"HTTP {response.status_code}"


def _render_email_html(content: str) -> str:
    """Render editable template text as safe HTML for Zoho Mail."""
    paragraphs = re.split(r"\n{2,}", str(content or "").strip())
    return "".join(
        f"<p>{html.escape(paragraph).replace(chr(10), '<br>')}</p>"
        for paragraph in paragraphs if paragraph.strip()
    )


def _account_addresses(account: dict) -> list[str]:
    addresses = []
    for value in (account.get("mailboxAddress"), account.get("incomingUserName"), account.get("primaryEmailAddress")):
        address = _email(value)
        if address and address.lower() not in {item.lower() for item in addresses}:
            addresses.append(address)
    for item in account.get("emailAddress") or []:
        if isinstance(item, dict):
            address = _email(item.get("mailId"))
            if address and address.lower() not in {value.lower() for value in addresses}:
                addresses.append(address)
    for item in account.get("sendMailDetails") or []:
        if isinstance(item, dict):
            address = _email(item.get("fromAddress"))
            if address and address.lower() not in {value.lower() for value in addresses}:
                addresses.append(address)
    return addresses


def _select_mail_account(accounts: list[dict], preferred_sender: str, selected_account_id: Any = None) -> tuple[dict, str]:
    usable = [account for account in accounts if isinstance(account, dict)]
    preferred = _email(preferred_sender).lower()
    selected = str(selected_account_id or '')
    account = next((item for item in usable if selected and str(item.get("accountId") or '') == selected), None)
    account = account or next((item for item in usable if preferred and preferred in {address.lower() for address in _account_addresses(item)}), None)
    if account is None:
        account = next((item for item in usable if item.get("type") == "ZOHO_ACCOUNT" and item.get("enabled") is not False), None)
    if account is None and usable:
        account = usable[0]
    if not account:
        fail("zoho_mail_account_missing", 502, "Zoho Mail did not return an account ID")
    addresses = _account_addresses(account)
    if not addresses:
        fail("zoho_mail_sender_missing", 502, "Zoho Mail did not return a valid sender address")
    mailbox = _email(account.get("mailboxAddress") or account.get("incomingUserName"))
    sender = mailbox or next((address for address in addresses if preferred and address.lower() == preferred), addresses[0])
    return account, sender


def _connection_row(row: dict) -> dict:
    if not row or not row.get("access_token"):
        fail("zoho_not_connected", 409, "Connect Zoho Mail for the assigned salesperson first")
    sender = _email(row.get("sender_email"))
    if not sender:
        fail("sender_email_missing", 409, "The signed-in Zoho user does not have a valid email address")
    return row


async def _refresh_token(database, row: dict, settings) -> str:
    refresh_token = str(row.get("refresh_token") or "")
    client_id = str(row.get("client_id") or "")
    client_secret = str(row.get("client_secret") or "")
    if not refresh_token or not client_id or not client_secret:
        fail("zoho_not_connected", 409, "Reconnect Zoho with ZohoMail.accounts.READ and ZohoMail.messages.CREATE")
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            response = await client.post(
                f"{settings.zoho_accounts_url.rstrip('/')}/oauth/v2/token",
                data={"grant_type": "refresh_token", "client_id": client_id,
                      "client_secret": client_secret, "refresh_token": refresh_token},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        fail("zoho_authentication_failed", 502, "Zoho authentication failed")
    token = str(payload.get("access_token") or "")
    if not token:
        fail("zoho_authentication_failed", 502, "Zoho did not return an access token")
    expires = datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in") or 3600))
    # Persist the refreshed token in a short transaction. The Zoho network
    # request must not share a database transaction because the API database
    # enforces a 20-second idle-in-transaction timeout.
    async with database.connection() as connection:
        await connection.execute(
            "update zoho_connections set access_token=%s, expires_at=%s, status='connected', last_error=null, updated_at=now() where organisation_id=%s and member_id=%s",
            (token, expires, row["organisation_id"], row["member_id"]),
        )
    row["access_token"] = token
    row["expires_at"] = expires
    return token


async def send_zoho_mail(database, settings, row: dict, *, to_address: str,
                         cc_addresses: list[str], subject: str, content: str) -> dict:
    """Look up the Mail account and submit one message using one OAuth token."""
    row = _connection_row(row)
    token = str(row["access_token"])
    expires_at = row.get("expires_at")
    if expires_at and expires_at <= datetime.now(timezone.utc) + timedelta(minutes=5):
        token = await _refresh_token(database, row, settings)
    headers = {"Authorization": f"Zoho-oauthtoken {token}", "Accept": "application/json"}
    mail_api_domain = str(getattr(settings, "zoho_mail_api_domain", "https://mail.zoho.com") or "https://mail.zoho.com").rstrip("/")
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        for attempt in range(2):
            try:
                account_response = await client.get(
                    f"{mail_api_domain}/api/accounts", headers=headers,
                )
                if account_response.status_code == 401 and attempt == 0:
                    token = await _refresh_token(database, row, settings)
                    headers["Authorization"] = f"Zoho-oauthtoken {token}"
                    continue
                if account_response.is_error:
                    fail("zoho_mail_account_lookup_failed", 502, f"Zoho Mail account lookup failed: {_provider_detail(account_response)}")
                account_payload = account_response.json()
                accounts = account_payload.get("data") if isinstance(account_payload, dict) else None
                account, sender_address = _select_mail_account(accounts if isinstance(accounts, list) else [], row.get("sender_email", ""), row.get("mail_account_id"))
                account_id = account.get("accountId") if isinstance(account, dict) else None
                if not account_id:
                    fail("zoho_mail_account_missing", 502, "Zoho Mail did not return an account ID")
                payload = {
                    "fromAddress": sender_address,
                    "toAddress": to_address,
                    "subject": subject.strip(),
                    "content": content,
                    "mailFormat": "html",
                    "askReceipt": "no",
                }
                if cc_addresses:
                    payload["ccAddress"] = ",".join(cc_addresses)
                send_response = await client.post(
                    f"{mail_api_domain}/api/accounts/{account_id}/messages",
                    headers={**headers, "Content-Type": "application/json"}, json=payload,
                )
                if send_response.status_code == 401 and attempt == 0:
                    token = await _refresh_token(database, row, settings)
                    headers["Authorization"] = f"Zoho-oauthtoken {token}"
                    continue
                if send_response.is_error:
                    fail("zoho_mail_delivery_failed", 502, f"Zoho Mail rejected the message: {_provider_detail(send_response)}")
                return {"accountId": str(account_id), "status": "accepted"}
            except (httpx.HTTPError, ValueError, TypeError):
                fail("zoho_mail_delivery_failed", 502, "Zoho Mail could not accept the message")
    fail("zoho_mail_delivery_failed", 502, "Zoho Mail could not accept the message")


def default_template(customer_name: str, reason: str, sender_name: str = "Sales team",
                     sender_email: str = "", sender_phone: str = "") -> tuple[str, str]:
    safe_name = (customer_name or "there").strip().split()[0]
    safe_sender = sender_name or "Sales team"
    safe_email = sender_email or ""
    safe_phone = sender_phone or ""
    if reason == SESSION_NOT_STARTED:
        subject = "Following up after your visit"
        body = "We noticed that your visit ended before a session was started. If you would like to continue the conversation, reschedule, or set up a connect-back, reply to this email and our sales team will help."
    else:
        subject = "Following up on your enquiry"
        body = "We wanted to check whether you still need help with a product, quote, invoice, or anything else. Reply with what you would like to discuss and our sales team will help."
    signature = "\n".join(value for value in (safe_sender, safe_email, safe_phone) if value)
    return subject, f"Hi {safe_name},\n\n{body}\n\nRegards,\n{signature}"


async def _missed_visitor(connection, context: Workspace, visit_id: str) -> dict:
    # Match the visibility rules used by /walkins/missed-visitors. Receptionists
    # are intentionally excluded from this sales action.
    if context.role not in {"owner", "salesperson"}:
        fail("sales_access_required", 403)
    row = await one(connection, """
        select m.*, coalesce(au.email, '') as sender_email,
               coalesce(p.display_name, '') as sender_name, coalesce(p.phone, '') as sender_phone,
               z.access_token, z.refresh_token, z.client_id, z.client_secret,
               z.expires_at, z.status as zoho_status, z.member_id, z.mail_account_id
        from public.guruconnect_missed_visitors m
        left join auth.users au on au.id=%s
        left join public.profiles p on p.id=%s
        left join public.zoho_connections z
          on z.organisation_id=m.organisation_id
         and z.member_id=%s
        where m.organisation_id=%s and m.visit_id=%s and m.expires_at > now()
          and not exists (
            select 1 from public.guruconnect_walkin_recordings r
            where r.organisation_id=m.organisation_id and r.visit_id=m.visit_id
          )
          and (%s or m.member_id=%s or m.registered_by=%s or m.assigned_agent_id=%s)
    """, (context.user.id, context.user.id, context.user.id, context.id, visit_id, context.admin,
           context.user.id, context.user.id, context.user.id))
    if not row:
        fail("follow_up_not_found", 404, "This follow-up is no longer available")
    recipient = _email(row.get("email"))
    if not recipient:
        fail("customer_email_missing", 422, "This customer does not have an email address")
    return row


@router.post("/follow-up-emails")
async def send_follow_up_email(body: FollowUpEmailRequest, request: Request,
                               context: Workspace = Depends(workspace)):
    database = request.app.state.db
    async with request.app.state.db.connection() as connection:
        row = await _missed_visitor(connection, context, body.visitId)
    cc = _addresses(body.ccAddress)
    result = await send_zoho_mail(
        database, request.app.state.settings, row,
        to_address=_email(row["email"]), cc_addresses=cc,
        subject=body.subject, content=_render_email_html(body.content),
    )
    # A manual follow-up satisfies the same one-message promise as the
    # weekday automation, so the 11:00 job will not send a duplicate. This is
    # deliberately a fresh, best-effort timestamp write after the external
    # Zoho request. The provider response is not persisted: delivery success
    # must never depend on JSON/database serialization.
    delivery_recorded = True
    try:
        async with database.connection() as connection:
            await connection.execute("""
                insert into public.guruconnect_followup_email_deliveries
                    (organisation_id, visit_id, scheduled_for, recipient, member_id, status, sent_at, source)
                values(%s,%s,(now() at time zone %s)::date,%s,%s,'sent',now(),'manual')
                on conflict (organisation_id, visit_id) do update
                  set status='sent', sent_at=excluded.sent_at,
                      recipient=excluded.recipient, member_id=excluded.member_id,
                      source='manual', error_code=null
            """, (context.id, row["visit_id"], request.app.state.settings.app_timezone,
                   _email(row["email"]), context.user.id))
    except Exception as error:
        # Zoho has already accepted the message. Do not turn a delivery-log
        # outage into a false send failure or make the user resend an email.
        delivery_recorded = False
        log.warning("follow_up_delivery_log_failed exception_type=%s", type(error).__name__)
    return {"sent": True, "toAddress": _email(row["email"]),
            "fromAddress": _email(row.get("sender_email")),
            "deliveryRecorded": delivery_recorded, **result}


def _business_source_date(today: date) -> date:
    # The Monday run picks up Friday's 9 PM sweep; weekday runs pick up the
    # immediately preceding day. There is no weekend send.
    return today - timedelta(days=3 if today.weekday() == 0 else 1)


async def _check_cron_secret(request: Request, supplied: str | None) -> None:
    import secrets
    configured = str(getattr(request.app.state.settings, "zoho_refresh_cron_secret", "") or "")
    if not configured or not supplied or not secrets.compare_digest(supplied, configured):
        fail("cron_authentication_failed", 401)


@internal_router.post("/api/internal/followup-emails/dispatch")
async def dispatch_follow_up_emails(request: Request,
                                    x_zoho_refresh_secret: str | None = Header(default=None)):
    await _check_cron_secret(request, x_zoho_refresh_secret)
    local_today = datetime.now(ZoneInfo(request.app.state.settings.app_timezone)).date()
    if local_today.weekday() > 4:
        return {"queued": 0, "sent": 0, "skipped": "weekend"}
    source_date = _business_source_date(local_today)
    async with request.app.state.db.connection() as connection:
        candidates = await many(connection, """
            select m.id, m.organisation_id, m.visit_id, m.email, m.customer_name,
                   m.assigned_agent_id, m.registered_by,
                   coalesce(m.assigned_agent_id,m.registered_by) as member_id,
                   z.access_token, z.refresh_token, z.client_id, z.client_secret,
                   z.expires_at, z.mail_account_id, coalesce(au.email, '') as sender_email,
                   coalesce(p.display_name, '') as sender_name, coalesce(p.phone, '') as sender_phone
            from public.guruconnect_missed_visitors m
            join public.zoho_connections z
              on z.organisation_id=m.organisation_id
             and z.member_id=coalesce(m.assigned_agent_id,m.registered_by)
            left join auth.users au on au.id=coalesce(m.assigned_agent_id,m.registered_by)
            left join public.profiles p on p.id=coalesce(m.assigned_agent_id,m.registered_by)
            where (m.missed_at at time zone %s)::date=%s
              and m.expires_at > now() and m.email <> ''
              and not exists (
                select 1 from public.guruconnect_walkin_recordings r
                where r.organisation_id=m.organisation_id and r.visit_id=m.visit_id
              )
        """, (request.app.state.settings.app_timezone, source_date))
    sent = 0
    for candidate in candidates:
        async with request.app.state.db.connection() as connection:
            claimed = await one(connection, """
                insert into public.guruconnect_followup_email_deliveries
                    (organisation_id, visit_id, scheduled_for, recipient, member_id, status, source)
                values(%s,%s,%s,%s,%s,'sending','automatic')
                on conflict (organisation_id, visit_id) do nothing
                returning id
            """, (candidate["organisation_id"], candidate["visit_id"], local_today,
                   _email(candidate["email"]), candidate["member_id"]))
        if not claimed:
            continue
        try:
            subject, content = default_template(
                candidate["customer_name"], SESSION_NOT_STARTED,
                candidate.get("sender_name"), candidate.get("sender_email"),
                candidate.get("sender_phone"),
            )
            await send_zoho_mail(
                request.app.state.db, request.app.state.settings, candidate,
                to_address=_email(candidate["email"]), cc_addresses=[],
                subject=subject,
                content=_render_email_html(content),
            )
        except Exception as error:
            try:
                async with request.app.state.db.connection() as connection:
                    await connection.execute(
                        "update public.guruconnect_followup_email_deliveries set status='failed', error_code=%s where id=%s",
                        (type(error).__name__, claimed["id"]),
                    )
            except Exception as log_error:
                log.warning("automatic_follow_up_failure_log_failed exception_type=%s", type(log_error).__name__)
            continue
        try:
            async with request.app.state.db.connection() as connection:
                await connection.execute(
                    "update public.guruconnect_followup_email_deliveries set status='sent', sent_at=now(), error_code=null where id=%s",
                    (claimed["id"],),
                )
        except Exception as error:
            # Zoho has already accepted the message. Keep the scheduler from
            # reporting a false delivery failure if only the timestamp write
            # is unavailable.
            log.warning("automatic_follow_up_delivery_log_failed exception_type=%s", type(error).__name__)
        sent += 1
    return {"sourceDate": source_date.isoformat(), "queued": len(candidates), "sent": sent}
