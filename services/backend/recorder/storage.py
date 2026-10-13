from urllib.parse import quote
import httpx
from .config import Settings
from .errors import fail


class Storage:
    """Private immutable objects; never follows redirects or fetches supplied URLs."""
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base = f"{settings.supabase_url}/storage/v1"

    @property
    def headers(self) -> dict[str, str]:
        if not self.settings.supabase_service_role_key:
            fail("storage_not_configured", 503)
        key = self.settings.supabase_service_role_key.get_secret_value()
        return {"Authorization": f"Bearer {key}", "apikey": key}

    def path(self, object_path: str) -> str:
        if object_path.startswith("/") or ".." in object_path.split("/") or ":" in object_path:
            fail("invalid_object_path", 400)
        return f"{quote(self.bucket(object_path), safe='')}/{quote(object_path, safe='/')}"

    def bucket(self, object_path: str) -> str:
        parts = object_path.split("/")
        if len(parts) >= 4 and parts[3] == "playback":
            return "recorder-playback"
        if len(parts) >= 4 and parts[3] in {"artifacts", "results"}:
            return "recorder-artifacts"
        return self.settings.storage_bucket

    async def authorize_upload(self, object_path: str, mime: str = "audio/wav") -> dict:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.post(f"{self.base}/object/upload/sign/{self.path(object_path)}", headers=self.headers, json={"upsert": False})
        if response.status_code >= 400:
            fail("upload_authorization_unavailable", 503)
        data = response.json()
        url = data.get("url") or data.get("signedURL")
        if not isinstance(url, str):
            fail("invalid_storage_response", 503)
        return {"upload_url": f"{self.settings.supabase_url}{url}" if url.startswith("/storage/") else f"{self.base}{url}" if url.startswith("/") else url,
                "headers": {"Content-Type": mime, "x-upsert": "false"}, "object_path": object_path}

    async def read(self, object_path: str, max_bytes: int) -> bytes:
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
            async with client.stream("GET", f"{self.base}/object/authenticated/{self.path(object_path)}", headers=self.headers) as response:
                if response.status_code == 404:
                    fail("audio_object_missing", 409)
                if response.status_code >= 400:
                    fail("storage_unavailable", 503)
                buffer = bytearray()
                async for chunk in response.aiter_bytes():
                    buffer.extend(chunk)
                    if len(buffer) > max_bytes:
                        fail("audio_size_exceeded", 413)
                return bytes(buffer)

    async def put(self, object_path: str, data: bytes, mime: str = "application/json") -> None:
        async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
            response = await client.post(f"{self.base}/object/{self.path(object_path)}", headers={**self.headers, "Content-Type": mime, "x-upsert": "false"}, content=data)
        if response.status_code == 409:
            import hashlib
            existing = await self.read(object_path, len(data))
            if hashlib.sha256(existing).digest() != hashlib.sha256(data).digest():
                fail("immutable_object_conflict", 409)
        elif response.status_code not in (200, 201):
            fail("storage_write_failed", 503)

    async def signed_read(self, object_path: str) -> str:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.post(f"{self.base}/object/sign/{self.path(object_path)}", headers=self.headers, json={"expiresIn": self.settings.signed_url_seconds})
        if response.status_code >= 400:
            fail("playback_unavailable", 503)
        url = response.json().get("signedURL")
        if not isinstance(url, str) or not url.startswith("/object/sign/"):
            fail("invalid_storage_response", 503)
        return f"{self.base}{url}"

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
        async with httpx.AsyncClient(timeout=180, follow_redirects=False) as client:
            response = await client.post(f"{self.base}/object/{self.path(object_path)}", headers={**self.headers, "Content-Type": mime, "Content-Length": str(length), "x-upsert": "false"}, content=body())
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
                async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                    response = await client.request("DELETE", f"{self.base}/object/{quote(bucket, safe='')}", headers=self.headers, json={"prefixes": paths[start:start + 100]})
                if response.status_code >= 400:
                    fail("storage_deletion_pending", 503)

    async def list_paths(self, prefix: str) -> list[str]:
        """Reconcile objects within a trusted conversation prefix after deletion.

        Prefix must contain exactly organisation, owner and conversation UUIDs.
        This is server-only: browser callers cannot request an arbitrary prefix.
        """
        from uuid import UUID
        parts = prefix.strip("/").split("/")
        if len(parts) != 3:
            fail("invalid_cleanup_prefix")
        try:
            for part in parts:
                UUID(part)
        except ValueError:
            fail("invalid_cleanup_prefix")
        output = []
        for bucket in (self.settings.storage_bucket, "recorder-playback", "recorder-artifacts"):
            pending = ["/".join(parts)]
            while pending:
                folder = pending.pop()
                offset = 0
                while True:
                    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
                        response = await client.post(f"{self.base}/object/list/{quote(bucket, safe='')}", headers=self.headers,
                                                     json={"prefix": folder, "limit": 100, "offset": offset, "sortBy": {"column": "name", "order": "asc"}})
                    if response.status_code >= 400:
                        fail("storage_reconciliation_failed", 503)
                    rows = response.json()
                    if not isinstance(rows, list):
                        fail("invalid_storage_response", 503)
                    for row in rows:
                        name = row.get("name")
                        if not isinstance(name, str) or not name or "/" in name or name in {".", ".."}:
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
