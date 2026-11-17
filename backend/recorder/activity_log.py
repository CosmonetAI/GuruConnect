"""Safe, searchable activity logging for requests, routes and workers."""

from __future__ import annotations

from contextvars import ContextVar
import json
import logging
import time
from typing import Any, Awaitable, TypeVar

log = logging.getLogger("recorder.activity")
T = TypeVar("T")
_context: ContextVar[dict[str, str | None]] = ContextVar(
    "activity_context",
    default={"request_id": None, "organisation_id": None, "member_id": None, "actor_id": None},
)
_REDACTED_KEYS = {
    "authorization", "api_key", "access_token", "refresh_token", "token", "secret", "password",
    "email", "phone", "mobile", "content", "message", "transcript", "source_text", "english_text",
    "customer_name", "customer_email", "customer_phone",
}


def set_context(**values: str | None):
    current = dict(_context.get())
    current.update({key: value for key, value in values.items() if key in current})
    return _context.set(current)


def update_context(**values: str | None) -> None:
    current = dict(_context.get())
    current.update({key: value for key, value in values.items() if key in current and value is not None})
    _context.set(current)


def reset_context(token) -> None:
    _context.reset(token)


def _safe(value: Any, key: str | None = None) -> Any:
    if key and key.lower() in _REDACTED_KEYS:
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): _safe(v, str(k)) for k, v in list(value.items())[:20]}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in list(value)[:20]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def emit(action: str, *, level: int = logging.INFO, metadata: dict[str, Any] | None = None, **identity: Any) -> None:
    """Write one activity line; never write customer text or provider secrets."""
    fields = dict(_context.get())
    fields.update({key: str(value) for key, value in identity.items() if value is not None})
    parts = ["event=activity", f"action={action}"]
    for key in ("request_id", "organisation_id", "member_id", "actor_id", "conversation_id", "session_id", "job_id",
                "provider", "operation", "status", "duration_ms", "external_request_id"):
        if fields.get(key) is not None:
            parts.append(f"{key}={fields[key]}")
    if metadata:
        parts.append("details=" + json.dumps(_safe(metadata), separators=(",", ":"), ensure_ascii=True))
    log.log(level, "%s", " ".join(parts))


def external_started(provider: str, operation: str) -> float:
    started_at = time.perf_counter()
    emit("external_api.started", provider=provider, operation=operation)
    return started_at


def external_completed(provider: str, operation: str, started_at: float, *, status: int | str = "ok",
                       external_request_id: str | None = None, metadata: dict[str, Any] | None = None) -> None:
    emit("external_api.completed", provider=provider, operation=operation, status=status,
         duration_ms=round((time.perf_counter() - started_at) * 1000, 1),
         external_request_id=external_request_id, metadata=metadata)


def external_failed(provider: str, operation: str, started_at: float, error: BaseException,
                    *, status: int | str | None = None, metadata: dict[str, Any] | None = None) -> None:
    details = {"exception_type": type(error).__name__, "error_code": getattr(error, "code", None) or "external_call_failed"}
    if metadata:
        details.update(metadata)
    emit("external_api.failed", level=logging.ERROR, provider=provider, operation=operation,
         status=status, duration_ms=round((time.perf_counter() - started_at) * 1000, 1), metadata=details)


async def monitored_call(awaitable: Awaitable[T], provider: str, operation: str, *, metadata: dict[str, Any] | None = None) -> T:
    """Monitor an SDK call whose response is not an httpx.Response."""
    started_at = external_started(provider, operation)
    try:
        result = await awaitable
    except Exception as error:
        external_failed(provider, operation, started_at, error, status=getattr(error, "status_code", None), metadata=metadata)
        raise
    external_completed(provider, operation, started_at, status=getattr(result, "status_code", "ok"),
                       external_request_id=getattr(result, "_request_id", None), metadata=metadata)
    return result
