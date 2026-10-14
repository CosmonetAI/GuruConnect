import asyncio
from contextlib import asynccontextmanager
import re
from urllib.parse import quote, urlsplit, unquote
from uuid import UUID
import httpx
from .config import Settings
from .errors import fail


class Storage:
    """Private immutable objects; never follows redirects or fetches supplied URLs."""
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base = f"{settings.supabase_url}/storage/v1"

    @asynccontextmanager
    async def client(self, timeout):
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                    yield client
        except (httpx.HTTPError, TimeoutError, ValueError):
            fail("storage_unavailable", 503)

    async def request(self, client, method, url, **kwargs):
        # Write/list/sign responses must never buffer unbounded upstream data.
        async with client.stream(method, url, **kwargs) as response:
            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(content) + len(chunk) > 262144:
                    fail("invalid_storage_response", 503)
                content.extend(chunk)
            return httpx.Response(response.status_code, headers=response.headers, content=bytes(content))

    @staticmethod
    def json_body(response):
        try:
            return response.json()
        except (ValueError, RecursionError):
            fail("invalid_storage_response", 503)

    @property
    def headers(self) -> dict[str, str]:
        if not self.settings.supabase_service_role_key:
            fail("storage_not_configured", 503)
        key = self.settings.supabase_service_role_key.get_secret_value()
        return {"Authorization": f"Bearer {key}", "apikey": key}

    def path(self, object_path: str) -> str:
        if not isinstance(object_path, str) or not re.fullmatch(r"[A-Za-z0-9._/-]{1,1024}", object_path):
            fail("invalid_object_path", 400)
        parts = object_path.split("/")
        if len(parts) < 5 or any(part in {"", ".", ".."} for part in parts):
            fail("invalid_object_path", 400)
        try:
            for part in parts[:3]:
                if str(UUID(part)) != part:
                    raise ValueError()
        except ValueError:
            fail("invalid_object_path", 400)
        return f"{quote(self.bucket(object_path), safe='')}/{quote(object_path, safe='/')}"

    def signed_url(self, value: str, object_path: str, operation: str) -> str:
        if not isinstance(value, str) or any(ord(char) < 33 for char in value):
            fail("invalid_storage_response", 503)
        if value.startswith("/storage/v1/"):
            absolute = f"{self.settings.supabase_url}{value}"
        elif value.startswith("/object/"):
            absolute = f"{self.base}{value}"
        else:
            absolute = value
        try:
            parsed, expected = urlsplit(absolute), urlsplit(self.settings.supabase_url)
            parsed.port
        except ValueError:
            fail("invalid_storage_response", 503)
        expected_path = f"/storage/v1/object/{operation}/{self.path(object_path)}"
        if (parsed.scheme != "https" or parsed.netloc != expected.netloc or parsed.username or parsed.password
                or parsed.fragment or unquote(parsed.path) != expected_path or not parsed.query):
            fail("invalid_storage_response", 503)
        return absolute

    def bucket(self, object_path: str) -> str:
        parts = object_path.split("/")
        if len(parts) >= 4 and parts[3] == "playback":
            return "recorder-playback"
        if len(parts) >= 4 and parts[3] in {"artifacts", "results"}:
            return "recorder-artifacts"
        return self.settings.storage_bucket

    async def authorize_upload(self, object_path: str, mime: str = "audio/wav") -> dict:
        async with self.client(15) as client:
            response = await self.request(client, "POST", f"{self.base}/object/upload/sign/{self.path(object_path)}", headers=self.headers, json={"upsert": False})
        if response.status_code != 200:
            fail("upload_authorization_unavailable", 503)
        data = self.json_body(response)
        if not isinstance(data, dict):
            fail("invalid_storage_response", 503)
        url = data.get("url") or data.get("signedURL")
        if not isinstance(url, str):
            fail("invalid_storage_response", 503)
        return {"upload_url": self.signed_url(url, object_path, "upload/sign"),
                "headers": {"Content-Type": mime, "x-upsert": "false"}, "object_path": object_path}

    async def read(self, object_path: str, max_bytes: int) -> bytes:
        if max_bytes <= 0:
            fail("invalid_object_size", 400)
        async with self.client(60) as client:
            async with client.stream("GET", f"{self.base}/object/authenticated/{self.path(object_path)}", headers=self.headers) as response:
                if response.status_code == 404:
                    fail("audio_object_missing", 409)
                if response.status_code != 200:
                    fail("storage_unavailable", 503)
                buffer = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(buffer) + len(chunk) > max_bytes:
                        fail("audio_size_exceeded", 413)
                    buffer.extend(chunk)
                return bytes(buffer)

    async def put(self, object_path: str, data: bytes, mime: str = "application/json") -> None:
        async with self.client(120) as client:
            response = await self.request(client, "POST", f"{self.base}/object/{self.path(object_path)}", headers={**self.headers, "Content-Type": mime, "x-upsert": "false"}, content=data)
        if response.status_code == 409:
            import hashlib
            existing = await self.read(object_path, len(data))
            if hashlib.sha256(existing).digest() != hashlib.sha256(data).digest():
                fail("immutable_object_conflict", 409)
        elif response.status_code not in (200, 201):
            fail("storage_write_failed", 503)

    async def signed_read(self, object_path: str) -> str:
        async with self.client(15) as client:
            response = await self.request(client, "POST", f"{self.base}/object/sign/{self.path(object_path)}", headers=self.headers, json={"expiresIn": self.settings.signed_url_seconds})
        if response.status_code != 200:
            fail("playback_unavailable", 503)
        data = self.json_body(response)
        if not isinstance(data, dict):
            fail("invalid_storage_response", 503)
        url = data.get("signedURL")
        return self.signed_url(url, object_path, "sign")

    async def put_file(self, object_path: str, file_obj, mime: str = "audio/wav") -> None:
        """Stream full recordings without retaining another recording in memory."""
        import hashlib
        file_obj.seek(0)
        digest = hashlib.sha256()
        length = 0
        while chunk := file_obj.read(1024 * 1024):
            digest.update(chunk)
            length += len(chunk)
        file_obj.seek(0)
        async def body():
            while chunk := file_obj.read(1024 * 1024):
                yield chunk
        async with self.client(180) as client:
            response = await self.request(client, "POST", f"{self.base}/object/{self.path(object_path)}", headers={**self.headers, "Content-Type": mime, "Content-Length": str(length), "x-upsert": "false"}, content=body())
            if response.status_code == 409:
                current, received = hashlib.sha256(), 0
                async with client.stream("GET", f"{self.base}/object/authenticated/{self.path(object_path)}", headers=self.headers) as existing:
                    if existing.status_code != 200:
                        fail("immutable_object_conflict", 409)
                    async for chunk in existing.aiter_bytes():
                        received += len(chunk)
                        if received > length:
                            fail("immutable_object_conflict", 409)
                        current.update(chunk)
                if received != length or current.digest() != digest.digest():
                    fail("immutable_object_conflict", 409)
            elif response.status_code not in (200, 201):
                fail("storage_write_failed", 503)

    async def delete(self, object_paths: list[str]) -> None:
        if not object_paths:
            return
        groups: dict[str, list[str]] = {}
        for path in object_paths:
            self.path(path)
            groups.setdefault(self.bucket(path), []).append(path)
        for bucket, paths in groups.items():
            for start in range(0, len(paths), 100):
                async with self.client(60) as client:
                    response = await self.request(client, "DELETE", f"{self.base}/object/{quote(bucket, safe='')}", headers=self.headers, json={"prefixes": paths[start:start + 100]})
                if response.status_code not in {200, 204}:
                    fail("storage_deletion_pending", 503)

    async def list_paths(self, prefix: str) -> list[str]:
        """Reconcile objects within a trusted conversation prefix after deletion.

        Prefix must contain exactly organisation, owner and conversation UUIDs.
        This is server-only: browser callers cannot request an arbitrary prefix.
        """
        parts = prefix.strip("/").split("/")
        if len(parts) != 3:
            fail("invalid_cleanup_prefix")
        try:
            for part in parts:
                if str(UUID(part)) != part:
                    raise ValueError()
        except ValueError:
            fail("invalid_cleanup_prefix")
        output = []
        scans = 0
        for bucket in (self.settings.storage_bucket, "recorder-playback", "recorder-artifacts"):
            pending = ["/".join(parts)]
            while pending:
                folder = pending.pop()
                offset = 0
                while True:
                    scans += 1
                    if scans > 20000 or len(folder.split("/")) > 10:
                        fail("storage_reconciliation_limit", 503)
                    async with self.client(30) as client:
                        response = await self.request(client, "POST", f"{self.base}/object/list/{quote(bucket, safe='')}", headers=self.headers,
                                                     json={"prefix": folder, "limit": 100, "offset": offset, "sortBy": {"column": "name", "order": "asc"}})
                    if response.status_code != 200:
                        fail("storage_reconciliation_failed", 503)
                    rows = self.json_body(response)
                    if not isinstance(rows, list) or len(rows) > 100:
                        fail("invalid_storage_response", 503)
                    for row in rows:
                        if not isinstance(row, dict):
                            fail("invalid_storage_response", 503)
                        name = row.get("name")
                        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", name) or name in {".", ".."}:
                            fail("invalid_storage_response", 503)
                        path = f"{folder}/{name}"
                        if row.get("id") is None:
                            pending.append(path)
                        else:
                            output.append(path)
                        if len(output) + len(pending) > 20000:
                            fail("storage_reconciliation_limit", 503)
                    if len(rows) < 100:
                        break
                    offset += len(rows)
                    if offset > 20000:
                        fail("storage_reconciliation_limit", 503)
        return output
