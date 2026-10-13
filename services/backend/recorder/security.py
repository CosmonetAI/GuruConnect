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


class JWTVerifier:
    """Short-lived JWKS cache with one refresh for unknown rotation keys."""
    def __init__(self, settings: Settings):
        self.settings = settings
        self.keys: dict = {}
        self.cached_at = 0.0
        self.last_fetch_at = 0.0
        self.lock = asyncio.Lock()

    async def verify(self, token: str) -> User:
        if not self.settings.supabase_url:
            fail("auth_not_configured", 503)
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in {"ES256", "RS256"} or not isinstance(header.get("kid"), str):
                fail("invalid_session", 401)
            for force in (False, True):
                async with self.lock:
                    if force or time.monotonic() - self.cached_at > 300 or not self.keys:
                        if force and time.monotonic() - self.last_fetch_at < 10:
                            break
                        self.last_fetch_at = time.monotonic()
                        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                            response = await client.get(f"{self.settings.issuer}/.well-known/jwks.json")
                            response.raise_for_status()
                            self.keys = {item["kid"]: item for item in response.json()["keys"]}
                            self.cached_at = time.monotonic()
                    key = self.keys.get(header["kid"])
                if key:
                    break
            if not key or key.get("alg", header["alg"]) != header["alg"]:
                fail("invalid_session", 401)
            claims = jwt.decode(token, jwt.PyJWK.from_dict(key).key, algorithms=[header["alg"]],
                                audience=self.settings.jwt_audience, issuer=self.settings.issuer,
                                options={"require": ["exp", "iat", "sub", "aud", "iss"]}, leeway=10)
            if claims.get("role") != "authenticated":
                fail("invalid_session", 401)
            meta = claims.get("user_metadata") or {}
            return User(UUID(claims["sub"]), str(claims.get("email", "")), str(meta.get("display_name") or meta.get("full_name") or "Salesperson")[:120])
        except (jwt.PyJWTError, ValueError, KeyError, TypeError):
            fail("invalid_or_expired_session", 401)
        except httpx.HTTPError:
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
            return json.loads(self.fernet.decrypt(ciphertext.encode()))
        except (InvalidToken, ValueError):
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

    @property
    def admin(self) -> bool:
        return self.role in {"owner", "admin"}


async def workspace(request: Request, user: User = Depends(current_user),
                    x_workspace_id: UUID | None = Header(default=None)) -> Workspace:
    if not x_workspace_id:
        fail("workspace_required", 400)
    async with request.app.state.db.connection() as connection:
        member = await one(connection, "select role from members where organisation_id=%s and user_id=%s and status='active'", (x_workspace_id, user.id))
    if not member:
        fail("workspace_access_denied", 403)
    return Workspace(x_workspace_id, user, member["role"])


async def administrator(context: Workspace = Depends(workspace)) -> Workspace:
    if not context.admin:
        fail("administrator_required", 403)
    return context


async def conversation(connection, context: Workspace, conversation_id: UUID, *, lock: bool = False) -> dict:
    row = await one(connection, "select * from conversations where id=%s and organisation_id=%s and deleted_at is null" + (" for update" if lock else ""), (conversation_id, context.id))
    if not row or (row["owner_id"] != context.user.id and not context.admin):
        fail("conversation_not_found", 404)
    return row


async def owned_conversation(connection, context: Workspace, conversation_id: UUID, *, lock: bool = True) -> dict:
    row = await conversation(connection, context, conversation_id, lock=lock)
    if row["owner_id"] != context.user.id:
        fail("recording_owner_required", 403)
    return row
