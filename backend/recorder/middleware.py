"""Bound resource use before parsing/authentication and attach safe response metadata."""

import asyncio
from collections import OrderedDict
import ipaddress
import logging
import time
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi.responses import JSONResponse

log = logging.getLogger("recorder.api")


def security_headers(settings, request_id: str) -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
        "X-Frame-Options": "DENY",
        "X-Request-ID": request_id,
    }
    if settings.deployed:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


class RequestBoundary:
    """Per-process admission limits; deployment ingress must also bound traffic.

    Peer identity comes only from the ASGI server. Forwarded headers must be
    accepted there only from explicitly trusted proxies, never from browsers.
    """

    def __init__(self, app, settings):
        self.app = app
        self.settings = settings
        self.active = 0
        self.peers = OrderedDict()

    def allow_peer(self, peer: str) -> bool:
        now = time.monotonic()
        while self.peers and now - next(iter(self.peers.values()))[0] >= 60:
            self.peers.popitem(last=False)
        entry = self.peers.get(peer)
        if entry:
            started, count = entry
            if count >= self.settings.preauth_rate_limit_per_minute:
                return False
            self.peers[peer] = (started, count + 1)
        else:
            # Do not evict live counters: rotating attacker identities must not
            # bypass the cap or cause an unbounded in-memory dictionary.
            if len(self.peers) >= self.settings.preauth_rate_limit_max_peers:
                return False
            self.peers[peer] = (now, 1)
        return True

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        started_at = time.perf_counter()
        status_code = 500
        scope.setdefault("state", {})["request_id"] = request_id
        response_headers = security_headers(self.settings, request_id)
        response_started, response_ended = False, False

        async def secure_send(message):
            nonlocal response_started, response_ended, status_code
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                replacement = {key.lower().encode(): value.encode() for key, value in response_headers.items()}
                message = {**message, "headers": [(key, value) for key, value in message.get("headers", []) if key.lower() not in replacement]
                           + list(replacement.items())}
                existing = {key.lower() for key, _ in message["headers"]}
                if message["status"] == 401 and b"www-authenticate" not in existing:
                    message["headers"].append((b"www-authenticate", b"Bearer"))
                if message["status"] == 429 and b"retry-after" not in existing:
                    message["headers"].append((b"retry-after", b"60"))
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                response_ended = True
            await send(message)

        async def reject(code, status, retry=False):
            headers = {"Retry-After": "60"} if retry else None
            await JSONResponse({"detail": {"code": code}}, status_code=status, headers=headers)(scope, receive, secure_send)

        raw_headers = scope.get("headers", [])
        if sum(len(key) + len(value) for key, value in raw_headers) > 32768 or len(raw_headers) > 100:
            await reject("request_headers_too_large", 431)
            return
        headers = {}
        sensitive = {b"host", b"origin", b"authorization", b"content-length", b"transfer-encoding", b"x-workspace-id", b"x-workspace-member-id"}
        for key, value in raw_headers:
            key = key.lower()
            if key in sensitive and key in headers:
                await reject("ambiguous_request_headers", 400)
                return
            headers[key] = value
        try:
            authority = headers.get(b"host", b"").decode("ascii")
            parsed = urlsplit("http://" + authority)
            host = parsed.hostname
            parsed.port
            if (not host or host not in self.settings.trusted_hosts or parsed.username or parsed.password
                    or parsed.path or parsed.query or parsed.fragment or any(char.isspace() for char in authority)):
                await reject("invalid_host", 400)
                return
        except (ValueError, UnicodeDecodeError):
            await reject("invalid_host", 400)
            return
        origin = headers.get(b"origin")
        if origin is not None and origin.decode("latin-1") not in self.settings.cors_origins:
            await reject("origin_not_allowed", 403)
            return
        if origin is not None:
            response_headers["Access-Control-Allow-Origin"] = origin.decode("latin-1")
            response_headers["Access-Control-Expose-Headers"] = "Content-Disposition, X-Request-ID"
        if len(scope.get("query_string", b"")) > 8192 or len(scope.get("path", "")) > 8192:
            await reject("request_target_too_large", 414)
            return
        try:
            peer = str(ipaddress.ip_address((scope.get("client") or ("0.0.0.0",))[0]))
        except ValueError:
            # ASGI test transports need not supply a real IP. All unknown peers
            # share a counter; their arbitrary text never becomes a map key.
            peer = "unknown"
        if scope.get("path") != "/health" and not self.allow_peer(peer):
            await reject("request_rate_limit", 429, retry=True)
            return
        if self.active >= self.settings.max_concurrent_requests:
            await reject("server_busy", 503, retry=True)
            return
        self.active += 1
        try:
            maximum = 5_000_000 if scope.get("path", "").startswith("/internal/v1/") else 1_000_000
            length = headers.get(b"content-length")
            encoding = headers.get(b"transfer-encoding")
            if length is not None:
                if encoding is not None or not length.isdigit():
                    await reject("invalid_content_length", 400)
                    return
                # Reject absurdly long digit strings before converting to int.
                if len(length) > 10 or int(length) > maximum:
                    await reject("request_too_large", 413)
                    return
            if encoding is not None and encoding.lower() != b"chunked":
                await reject("unsupported_transfer_encoding", 400)
                return
            body = bytearray()
            try:
                async with asyncio.timeout(self.settings.request_body_timeout_seconds):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        if message["type"] != "http.request":
                            await reject("invalid_request_body", 400)
                            return
                        chunk = message.get("body", b"")
                        if len(body) + len(chunk) > maximum:
                            await reject("request_too_large", 413)
                            return
                        body.extend(chunk)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                await reject("request_body_timeout", 408)
                return
            if length is not None and len(body) != int(length):
                await reject("content_length_mismatch", 400)
                return
            delivered = False

            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            try:
                await self.app(scope, bounded_receive, secure_send)
            except Exception as exc:
                # ASGI servers otherwise log the exception and traceback even
                # after FastAPI's generic exception handler has sent a response.
                log.error("unexpected_error request_id=%s exception_type=%s", request_id, type(exc).__name__)
                if not response_started:
                    await JSONResponse({"detail": {"code": "internal_error", "request_id": request_id}}, 500)(scope, bounded_receive, secure_send)
                elif not response_ended:
                    # Completing a partial response violates Content-Length and
                    # also makes chunked downloads appear successful. Let the
                    # server close the connection without fabricating success.
                    raise RuntimeError("stream_response_incomplete") from None
            log.info("request_complete request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
                     request_id, scope.get("method", ""), scope.get("path", ""), status_code,
                     (time.perf_counter() - started_at) * 1000)
        finally:
            self.active -= 1
