"""SMTP2GO PDF delivery with explicit acceptance and ambiguous-outcome semantics.

The document workflow owns its durable delivery claim. This adapter performs one
submission only: SMTP2GO does not document an email-send idempotency parameter,
so retrying a lost acknowledgement could send the same document twice.
"""
from __future__ import annotations

import base64
from email.utils import formataddr
import hashlib
import json
import re
from typing import Any, Mapping

import httpx

from .settings import settings

SMTP2GO_SEND_URL = 'https://api.smtp2go.com/v3/email/send'
MAX_PDF_BYTES = 20 * 1024 * 1024
_EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+")
_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}')
_MESSAGES = {
    'email_not_configured': 'Email delivery is not configured yet. The document can still be downloaded as a PDF.',
    'email_invalid_recipient': 'Enter a valid recipient email address, or download the PDF instead.',
    'email_invalid_message': 'Check the email subject and message before sending.',
    'email_invalid_attachment': 'The document could not be attached as a valid PDF.',
    'email_attachment_too_large': 'The PDF is too large to send by email. Download it instead.',
    'email_invalid_delivery_key': 'The email delivery request is invalid. Reopen the saved document.',
    'email_idempotency_conflict': 'This delivery key already belongs to a different email request.',
    'email_delivery_in_progress': 'This document email is already being submitted. Refresh its delivery status.',
    'email_provider_authentication_failed': 'The email service rejected its configuration. The document is saved and can be downloaded.',
    'email_provider_rate_limited': 'The email service is busy. The document is saved; retry email delivery later.',
    'email_provider_rejected': 'The email service rejected this message. The document is saved and can be downloaded.',
    'email_provider_unreachable': 'The email service could not be reached. The document is saved; retry email delivery later.',
    'email_delivery_uncertain': 'The email service may have accepted this document. Check its delivery activity before sending it again.',
}


class EmailDeliveryError(Exception):
    def __init__(self, code: str, status_code: int = 502, *, retryable: bool = False,
                 uncertain: bool = False, provider_request_id=None, provider_email_id=None):
        self.code, self.status_code = code, status_code
        self.retryable, self.uncertain = retryable, uncertain
        self.provider_request_id = _identifier(provider_request_id)
        self.provider_email_id = _identifier(provider_email_id)
        super().__init__(_MESSAGES[code])

    def as_dict(self) -> dict:
        return {'code': self.code, 'message': str(self), 'retryable': self.retryable,
                'uncertain': self.uncertain,
                **({'provider_request_id': self.provider_request_id} if self.provider_request_id else {}),
                **({'provider_email_id': self.provider_email_id} if self.provider_email_id else {})}


def _identifier(value):
    return value if isinstance(value, str) and _IDENTIFIER.fullmatch(value) else None


def _key(configuration) -> str:
    value = getattr(configuration, 'smtp2go_api_key', '')
    value = value.get_secret_value() if hasattr(value, 'get_secret_value') else value
    value = str(value or '').strip()
    return '' if any(character in value for character in '\r\n\0') else value


def _email(value) -> str | None:
    value = str(value or '').strip()
    return value if len(value) <= 320 and _EMAIL.fullmatch(value) else None


def email_delivery_configuration(settings_override=None) -> dict:
    configuration = settings_override or settings
    missing = []
    if not _key(configuration):
        missing.append('SMTP2GO_API_KEY')
    if not _email(getattr(configuration, 'smtp2go_sender_email', '')):
        missing.append('SMTP2GO_SENDER_EMAIL')
    name = str(getattr(configuration, 'smtp2go_sender_name', 'GuruConnect') or '')
    if '\r' in name or '\n' in name or '\0' in name or len(name) > 160:
        missing.append('SMTP2GO_SENDER_NAME')
    return {'configured': not missing, 'provider': 'smtp2go', 'missing': missing}


def delivery_fingerprint(*, recipient: str, subject: str, message: str, pdf_bytes: bytes, filename: str) -> str:
    """A safe hash for the workflow's durable idempotency record; no email text is logged."""
    value = {'recipient': str(recipient).strip().casefold(), 'subject': subject, 'message': message,
             'attachment_sha256': hashlib.sha256(pdf_bytes).hexdigest(), 'filename': filename}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def delivery_replay(existing: Mapping[str, Any] | None, fingerprint: str) -> dict | None:
    """Apply after taking the workflow's database lock, before submitting again.

    Store fingerprint/status/receipt durably before and after the provider call.
    A crashed `submitting` attempt requires reconciliation, just like an explicit
    uncertain result. A definitive rejection may be retried by an explicit user
    action without creating another Zoho document.
    """
    if not existing:
        return None
    if existing.get('fingerprint') != fingerprint:
        raise EmailDeliveryError('email_idempotency_conflict', 409)
    status = existing.get('status')
    if status in {'accepted', 'sent', 'delivered'}:
        receipt = existing.get('receipt')
        if isinstance(receipt, dict) and _identifier(receipt.get('provider_email_id')):
            return dict(receipt)
        raise EmailDeliveryError('email_delivery_uncertain', 409, uncertain=True,
                                 provider_email_id=existing.get('provider_email_id'))
    if status in {'submitting', 'uncertain'}:
        raise EmailDeliveryError('email_delivery_uncertain', 409, uncertain=True,
                                 provider_request_id=existing.get('provider_request_id'),
                                 provider_email_id=existing.get('provider_email_id'))
    if status in {'queued', 'running'}:
        raise EmailDeliveryError('email_delivery_in_progress', 409)
    if status in {'pending', 'failed', 'rejected', 'not_configured'}:
        return None
    raise EmailDeliveryError('email_delivery_uncertain', 409, uncertain=True)


def _payload(configuration, *, recipient, subject, message, pdf_bytes, filename, delivery_key):
    recipient = _email(recipient)
    if not recipient:
        raise EmailDeliveryError('email_invalid_recipient', 422)
    if not isinstance(subject, str) or not subject.strip() or len(subject) > 240 or any(character in subject for character in '\r\n\0'):
        raise EmailDeliveryError('email_invalid_message', 422)
    if not isinstance(message, str) or not message.strip() or len(message) > 50000 or '\0' in message:
        raise EmailDeliveryError('email_invalid_message', 422)
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes.startswith(b'%PDF-'):
        raise EmailDeliveryError('email_invalid_attachment', 422)
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise EmailDeliveryError('email_attachment_too_large', 413)
    if not isinstance(filename, str) or not filename.lower().endswith('.pdf') or not 5 <= len(filename) <= 180 or any(character in filename for character in '\r\n\0/\\'):
        raise EmailDeliveryError('email_invalid_attachment', 422)
    if not isinstance(delivery_key, str) or not 8 <= len(delivery_key) <= 200 or any(character in delivery_key for character in '\r\n\0'):
        raise EmailDeliveryError('email_invalid_delivery_key', 422)
    sender = _email(getattr(configuration, 'smtp2go_sender_email', ''))
    name = str(getattr(configuration, 'smtp2go_sender_name', 'GuruConnect') or '').strip()
    trace = hashlib.sha256(delivery_key.encode()).hexdigest()
    return {'sender': formataddr((name, sender)), 'to': [recipient], 'subject': subject.strip(),
            'text_body': message, 'fastaccept': True,
            'custom_headers': [{'header': 'X-GuruConnect-Delivery', 'value': trace}],
            'attachments': [{'filename': filename, 'fileblob': base64.b64encode(pdf_bytes).decode('ascii'), 'mimetype': 'application/pdf'}]}


def _receipt(response: httpx.Response, delivery_key: str) -> dict:
    try:
        payload = response.json()
    except (ValueError, UnicodeDecodeError):
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    request_id, email_id = _identifier(payload.get('request_id')), _identifier(data.get('email_id'))
    identifiers = {'provider_request_id': request_id, 'provider_email_id': email_id}
    if response.status_code in {401, 403}:
        raise EmailDeliveryError('email_provider_authentication_failed', **identifiers)
    if response.status_code == 429:
        raise EmailDeliveryError('email_provider_rate_limited', 503, retryable=True, **identifiers)
    if response.status_code >= 500:
        raise EmailDeliveryError('email_delivery_uncertain', uncertain=True, **identifiers)
    if not 200 <= response.status_code < 300:
        raise EmailDeliveryError('email_provider_rejected', **identifiers)
    succeeded, failed = data.get('succeeded'), data.get('failed')
    counts_valid = type(succeeded) is int and type(failed) is int and succeeded >= 0 and failed >= 0
    if counts_valid and succeeded == 0 and (failed > 0 or data.get('error') or data.get('error_code')):
        raise EmailDeliveryError('email_provider_rejected', **identifiers)
    if not counts_valid and (data.get('error') or data.get('error_code')) and not email_id:
        raise EmailDeliveryError('email_provider_rejected', **identifiers)
    if not counts_valid or succeeded != 1 or failed != 0 or data.get('failures') or not email_id:
        raise EmailDeliveryError('email_delivery_uncertain', uncertain=True, **identifiers)
    return {'provider': 'smtp2go', 'status': 'accepted', 'provider_request_id': request_id,
            'provider_email_id': email_id, 'delivery_key': delivery_key}


async def send_document_email(*, recipient: str, subject: str, message: str, pdf_bytes: bytes,
                              filename: str, delivery_key: str, settings_override=None,
                              client: httpx.AsyncClient | None = None) -> dict:
    """Submit once, after the caller commits a durable `submitting` delivery claim.

    `accepted` confirms SMTP2GO handoff, never delivery to the recipient inbox.
    Nothing is submitted if configuration or input validation fails.
    """
    configuration = settings_override or settings
    if not email_delivery_configuration(configuration)['configured']:
        raise EmailDeliveryError('email_not_configured', 503)
    payload = _payload(configuration, recipient=recipient, subject=subject, message=message,
                       pdf_bytes=pdf_bytes, filename=filename, delivery_key=delivery_key)
    timeout = float(getattr(configuration, 'smtp2go_timeout_seconds', 20))
    timeout = min(60, max(2, timeout))
    headers = {'X-Smtp2go-Api-Key': _key(configuration), 'Accept': 'application/json'}

    async def submit(connection):
        try:
            response = await connection.post(SMTP2GO_SEND_URL, json=payload, headers=headers,
                timeout=httpx.Timeout(timeout, connect=min(5, timeout)), follow_redirects=False)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise EmailDeliveryError('email_provider_unreachable', 503, retryable=True) from None
        except httpx.HTTPError:
            raise EmailDeliveryError('email_delivery_uncertain', uncertain=True) from None
        return _receipt(response, delivery_key)

    if client is not None:
        return await submit(client)
    async with httpx.AsyncClient() as connection:
        return await submit(connection)
