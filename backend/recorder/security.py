import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from uuid import UUID
import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from .config import Settings
from .db import one
from .errors import fail

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class User:
    id: UUID
    email: str
    name: str
    aal: str = "aal1"


class JWTVerifier:
    """Short-lived JWKS cache with one refresh for unknown rotation keys."""
    JWKS_TIMEOUT_SECONDS = 8

    def __init__(self, settings: Settings):
        self.settings = settings
        self.keys: dict = {}
        self.cached_at = 0.0
        self.last_fetch_at = 0.0
        self.fetch_failed = False
        self.lock = asyncio.Lock()

    async def refresh_keys(self):
        now = time.monotonic()
        if now - self.last_fetch_at < 10:
            if self.fetch_failed or not self.keys:
                fail("auth_temporarily_unavailable", 503)
            return
        self.last_fetch_at = now
        self.fetch_failed = True
        async with asyncio.timeout(self.JWKS_TIMEOUT_SECONDS), httpx.AsyncClient(timeout=httpx.Timeout(5, connect=3), follow_redirects=False, trust_env=False) as client:
            async with client.stream("GET", f"{self.settings.issuer}/.well-known/jwks.json") as response:
                if response.status_code != 200:
                    fail("auth_temporarily_unavailable", 503)
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(payload) + len(chunk) > 262144:
                        fail("auth_temporarily_unavailable", 503)
                    payload.extend(chunk)
        try:
            items = json.loads(payload)["keys"]
            if not isinstance(items, list) or not 1 <= len(items) <= 64:
                raise ValueError()
            keys = {}
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError()
                kid = item.get("kid")
                if not isinstance(kid, str) or not 1 <= len(kid) <= 200 or kid in keys:
                    raise ValueError()
                if item.get("use", "sig") != "sig" or "verify" not in item.get("key_ops", ["verify"]):
                    continue
                keys[kid] = item
            if not keys:
                raise ValueError()
        except (ValueError, KeyError, TypeError, RecursionError):
            fail("auth_temporarily_unavailable", 503)
        self.keys = keys
        self.cached_at = time.monotonic()
        self.fetch_failed = False

    async def verify(self, token: str) -> User:
        if not self.settings.supabase_url:
            fail("auth_not_configured", 503)
        try:
            if not isinstance(token, str) or not 1 <= len(token) <= 16384:
                fail("invalid_session", 401)
            header = jwt.get_unverified_header(token)
            if (header.get("alg") not in {"ES256", "RS256"} or not isinstance(header.get("kid"), str)
                    or not 1 <= len(header["kid"]) <= 200 or header.get("crit")):
                fail("invalid_session", 401)
            for force in (False, True):
                async with self.lock:
                    if force or time.monotonic() - self.cached_at > 300 or not self.keys:
                        await self.refresh_keys()
                    key = self.keys.get(header["kid"])
                if key:
                    break
            if not key or key.get("alg", header["alg"]) != header["alg"]:
                fail("invalid_session", 401)
            if ((header["alg"] == "ES256" and (key.get("kty") != "EC" or key.get("crv") != "P-256"))
                    or (header["alg"] == "RS256" and key.get("kty") != "RSA")):
                fail("invalid_session", 401)
            public_key = jwt.PyJWK.from_dict(key).key
            if header["alg"] == "RS256" and public_key.key_size < 2048:
                fail("invalid_session", 401)
            claims = jwt.decode(token, public_key, algorithms=[header["alg"]],
                                audience=self.settings.jwt_audience, issuer=self.settings.issuer,
                                options={"require": ["exp", "iat", "sub", "aud", "iss"]}, leeway=10)
            if claims.get("role") != "authenticated":
                fail("invalid_session", 401)
            meta = claims.get("user_metadata") or {}
            if not isinstance(meta, dict) or not isinstance(claims["sub"], str):
                fail("invalid_session", 401)
            aal = claims.get("aal", "aal1")
            if aal not in {"aal1", "aal2"}:
                fail("invalid_session", 401)
            return User(UUID(claims["sub"]), str(claims.get("email", ""))[:254],
                        str(meta.get("display_name") or meta.get("full_name") or "Salesperson")[:120], aal)
        except (jwt.PyJWTError, ValueError, KeyError, TypeError, RecursionError):
            fail("invalid_or_expired_session", 401)
        except (httpx.HTTPError, TimeoutError):
            fail("auth_temporarily_unavailable", 503)


class SecretBox:
    def __init__(self, settings: Settings):
        if not settings.credential_encryption_key:
            fail("credential_encryption_not_configured", 503)
        try:
            self.fernet = Fernet(settings.credential_encryption_key.get_secret_value().encode())
        except (ValueError, TypeError):
            fail("credential_encryption_invalid", 503)

    def encrypt(self, payload: dict) -> str:
        return self.fernet.encrypt(json.dumps(payload, separators=(",", ":")).encode()).decode()

    def decrypt(self, ciphertext: str) -> dict:
        try:
            result = json.loads(self.fernet.decrypt(ciphertext.encode()))
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (InvalidToken, ValueError, TypeError, AttributeError):
            fail("credential_decryption_failed", 503)


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> User:
    if not credentials or credentials.scheme.lower() != "bearer":
        fail("authentication_required", 401)
    user = await request.app.state.verifier.verify(credentials.credentials)
    async with request.app.state.db.connection() as connection:
        allowance = await one(connection, "select allow_api_request(%s,%s,60) as allowed", (f"user:{user.id}", request.app.state.settings.rate_limit_per_minute))
        if not allowance["allowed"]:
            fail("request_rate_limit", 429)
    return user


@dataclass(frozen=True)
class Workspace:
    id: UUID
    user: User
    role: str
    member_id: UUID
    actor_role: str

    @property
    def admin(self) -> bool:
        return self.actor_role == "owner"

    @property
    def personal(self) -> bool:
        return self.member_id == self.user.id


async def workspace(request: Request, user: User = Depends(current_user),
                    x_workspace_id: UUID | None = Header(default=None),
                    x_workspace_member_id: UUID | None = Header(default=None)) -> Workspace:
    if not x_workspace_id:
        fail("workspace_required", 400)
    async with request.app.state.db.connection() as connection:
        actor = await one(connection, "select role from members where organisation_id=%s and user_id=%s and status='active'", (x_workspace_id, user.id))
        selected_id = x_workspace_member_id or user.id
        selected = await one(connection, "select role,status from members where organisation_id=%s and user_id=%s", (x_workspace_id, selected_id))
    if not actor or not selected or (selected_id != user.id and actor["role"] != "owner"):
        fail("workspace_access_denied", 403)
    if selected.get("status", "active") != "active" and (actor["role"] != "owner" or request.method not in {"GET", "HEAD"}):
        fail("workspace_access_denied", 403)
    return Workspace(x_workspace_id, user, selected["role"], selected_id, actor["role"])


async def administrator(request: Request, context: Workspace = Depends(workspace)) -> Workspace:
    if context.role != "owner":
        fail("administrator_required", 403)
    if request.app.state.settings.require_admin_mfa and context.user.aal != "aal2":
        fail("mfa_required", 403)
    return context


async def conversation(connection, context: Workspace, conversation_id: UUID, *, lock: bool = False) -> dict:
    row = await one(connection, "select * from conversations where id=%s and organisation_id=%s and deleted_at is null" + (" for update" if lock else ""), (conversation_id, context.id))
    # A workspace owner may select another member through the verified workspace
    # dependency, but ordinary agents never see a peer's recording by ID.
    if not row or row["owner_id"] != context.member_id:
        fail("conversation_not_found", 404)
    return row


async def owned_conversation(connection, context: Workspace, conversation_id: UUID, *, lock: bool = True) -> dict:
    row = await conversation(connection, context, conversation_id, lock=lock)
    if row["owner_id"] != context.user.id:
        fail("recording_owner_required", 403)
    return row
