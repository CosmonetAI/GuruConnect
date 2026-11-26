"""Redacted error email alerts with optional OpenAI developer summaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
import json
import logging
import re
import smtplib
import ssl
import time
from traceback import format_exception


SENSITIVE_PATTERN = re.compile(
    r"(?i)(api[_-]?key|password|secret|token|authorization|client_secret)(['\"]?\s*[:=]\s*['\"]?)[^'\"\s,;]+"
)
PRIORITY_LABELS = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH"}


@dataclass(frozen=True, slots=True)
class ErrorAlertAnalysis:
    priority: str
    summary: str
    developer_context: str
    suggested_next_steps: list[str]


def redact_sensitive(value: str) -> str:
    return SENSITIVE_PATTERN.sub(r"\1\2[redacted]", value)


def is_openai_quota_error(value: object) -> bool:
    status = getattr(value, "status_code", None)
    text = str(value or "").lower()
    code = str(getattr(value, "code", "") or "").lower()
    return any(term in f"{code} {text}" for term in (
        "insufficient_quota", "quota exceeded", "billing_hard_limit", "credit balance", "credits exhausted",
        "out of credits", "payment required",
    )) or (status == 429 and any(term in text for term in ("quota", "billing", "credit")))


def _record_text(record: logging.LogRecord, settings) -> str:
    occurred_at = datetime.fromtimestamp(record.created, UTC).isoformat()
    route = " ".join(str(value) for value in (getattr(record, "request_method", None), getattr(record, "request_path", None)) if value)
    traceback_text = "".join(format_exception(*record.exc_info)).strip() if record.exc_info else ""
    traceback_text = traceback_text[:5000]
    lines = [
        "GuruConnect backend error alert", "", f"Time: {occurred_at}",
        f"Environment: {settings.environment}",
        f"Level: {record.levelname}", f"Logger: {record.name}",
    ]
    if route:
        lines.append(f"Route: {route}")
    for attr in ("request_id", "organisation_id", "member_id", "actor_id", "provider", "operation"):
        value = getattr(record, attr, None)
        if value:
            lines.append(f"{attr}: {value}")
    lines.extend(["", "Raw error:", record.getMessage()])
    if traceback_text:
        lines.extend(["", "Traceback:", traceback_text])
    return redact_sensitive("\n".join(lines))


def fallback_analysis(record: logging.LogRecord) -> ErrorAlertAnalysis:
    text = f"{record.name}\n{record.getMessage()}\n{record.exc_info}".lower()
    if is_openai_quota_error(record.getMessage()) or ("openai" in text and "quota" in text):
        return ErrorAlertAnalysis("high", "OpenAI rejected a request because its quota, billing limit, or credits appear exhausted.",
                                  "Customer AI features may remain unavailable until the configured OpenAI account is recharged or its limit is raised.",
                                  ["Check OpenAI billing and usage limits.", "Verify the configured model and API key.", "Retry a small request after the billing change is visible."])
    if any(term in text for term in ("database", "authentication", "authorization", "security", "internal_error", "data_loss")):
        priority = "high"
    elif any(term in text for term in ("timeout", "rate_limit", "429", "temporarily", "unavailable")):
        priority = "low"
    else:
        priority = "medium"
    return ErrorAlertAnalysis(priority, f"{record.name} reported an error: {record.getMessage()[:500]}",
                              "The alert contains a redacted operation, provider, workspace identity, and stack trace for correlation.",
                              ["Search logs by request_id, organisation_id, member_id, provider, and operation.",
                               "Check the upstream provider status and credentials.", "Retry only after confirming the operation is safe to repeat."])


def _parse_json(value: str) -> dict:
    try:
        parsed = json.loads(value.strip().removeprefix("```json").removesuffix("```").strip())
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def analyze_with_openai(record: logging.LogRecord, settings, raw: str) -> ErrorAlertAnalysis:
    fallback = fallback_analysis(record)
    if not settings.error_alert_ai_enabled or not settings.openai_api_key:
        return fallback
    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.openai_api_key.get_secret_value(), timeout=12, max_retries=1)
        response = client.responses.create(
            model=settings.error_alert_ai_model,
            input=[{"role": "developer", "content": "Summarize this redacted backend error for developers. Return JSON only with priority (low, medium, high), summary, developer_context, suggested_next_steps (1-5). Low is transient; medium is feature degradation; high is outage, auth/security, billing/quota, data loss, or a broken core workflow."},
                   {"role": "user", "content": raw[:7000]}],
            text={"format": {"type": "json_schema", "name": "error_alert", "strict": True, "schema": {
                "type": "object", "properties": {"priority": {"type": "string", "enum": ["low", "medium", "high"]}, "summary": {"type": "string"}, "developer_context": {"type": "string"}, "suggested_next_steps": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5}},
                "required": ["priority", "summary", "developer_context", "suggested_next_steps"], "additionalProperties": False,
            }}},
            max_output_tokens=900,
        )
        payload = _parse_json(getattr(response, "output_text", ""))
        priority = str(payload.get("priority") or fallback.priority).lower()
        return ErrorAlertAnalysis(priority if priority in PRIORITY_LABELS else fallback.priority,
                                  str(payload.get("summary") or fallback.summary)[:1200],
                                  str(payload.get("developer_context") or fallback.developer_context)[:1800],
                                  [str(item)[:300] for item in payload.get("suggested_next_steps", []) if str(item).strip()][:5] or fallback.suggested_next_steps)
    except Exception:
        logging.getLogger(__name__).warning("error_alert_ai_summary_failed", extra={"suppress_error_email": True})
        return fallback


def _body(analysis: ErrorAlertAnalysis, raw: str) -> str:
    return redact_sensitive("\n".join([f"Priority: {PRIORITY_LABELS[analysis.priority]}", "", "Human summary:", analysis.summary,
        "", "Developer context:", analysis.developer_context, "", "Suggested next steps:",
        *[f"- {item}" for item in analysis.suggested_next_steps], "", "Related redacted logs:", raw]))


def send_error_alert_email(settings, subject: str, body: str, html_body: str | None = None) -> None:
    recipients = [item.strip() for item in settings.error_alert_emails if item.strip()]
    if not recipients or not settings.smtp_host or not settings.smtp_from_email:
        return
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    message["To"] = recipients[0]
    if len(recipients) > 1:
        message["Cc"] = ", ".join(recipients[1:])
    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_username and settings.smtp_password:
            smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        smtp.send_message(message)


class ErrorAlertEmailHandler(logging.Handler):
    def __init__(self, settings) -> None:
        super().__init__(level=logging.ERROR)
        self.settings = settings
        self._last_sent: dict[str, float] = {}

    def emit(self, record: logging.LogRecord) -> None:
        if (getattr(record, "suppress_error_email", False) or not self.settings.error_alert_emails
                or not self.settings.smtp_host or not self.settings.smtp_from_email):
            return
        fingerprint = re.sub(r"(?:request_id|duration_ms)=[^\s]+", "", record.getMessage())
        key = f"{record.name}:{fingerprint[:240]}"
        now = time.monotonic()
        if now - self._last_sent.get(key, 0) < self.settings.error_alert_cooldown_seconds:
            return
        self._last_sent[key] = now
        try:
            raw = _record_text(record, self.settings)
            analysis = analyze_with_openai(record, self.settings, raw)
            label = PRIORITY_LABELS[analysis.priority]
            subject = f"[GuruConnect] [{self.settings.environment}] [{label}] Backend error: {record.name}"
            send_error_alert_email(self.settings, subject, _body(analysis, raw))
        except Exception:
            logging.getLogger(__name__).warning("error_alert_email_failed", extra={"suppress_error_email": True})
