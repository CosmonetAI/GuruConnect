"""Loopback-only independent development API; no cloud authentication bypass."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import schemas as s
from .audio import MAX_SESSION_SAMPLES, validate_wav
from .connection_routes import validate_connection_config
from .enquiry_routes import EnquiryCreate
from .errors import fail
from .local_store import LOCAL_USER_ID, LOCAL_WORKSPACE_ID, LocalStore, encoded, now
from .openai_translator import OpenAITranslator
from .providers import ElevenLabsAdapter, ProviderError


class LocalBoundary:
    """Loopback transport and explicit browser origins, including upload bodies."""
    def __init__(self, app, origins):
        self.app = app
        self.origins = set(origins)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send); return
        headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
        try:
            peer = ipaddress.ip_address(scope.get("client", ("",))[0])
            host = urlsplit("http://" + headers.get("host", "")).hostname
            allowed = peer.is_loopback and host in {"127.0.0.1", "localhost", "::1"}
        except ValueError:
            allowed = False
        origin = headers.get("origin")
        if not allowed or (origin is not None and origin not in self.origins):
            await JSONResponse({"detail": {"code": "local_access_denied", "message": "The local API accepts only loopback clients and configured local browser origins."}}, 403)(scope, receive, send)
            return
        workspace = headers.get("x-workspace-id")
        if workspace and workspace != LOCAL_WORKSPACE_ID:
            await JSONResponse({"detail": {"code": "workspace_not_found"}}, 403)(scope, receive, send); return
        if scope["method"] in {"POST", "PUT", "PATCH"}:
            maximum = 400000 if scope["method"] == "PUT" and scope["path"].startswith("/api/v1/local-audio/") else 100000
            try:
                size = int(headers.get("content-length", "0"))
                if size < 0: raise ValueError()
            except ValueError:
                await JSONResponse({"detail": {"code": "invalid_content_length"}}, 400)(scope, receive, send); return
            chunks, size_read = [], 0
            if size > maximum:
                await JSONResponse({"detail": {"code": "request_too_large"}}, 413)(scope, receive, send); return
            while True:
                incoming = await receive()
                if incoming["type"] == "http.disconnect": return
                data = incoming.get("body", b"")
                size_read += len(data)
                if size_read > maximum:
                    await JSONResponse({"detail": {"code": "request_too_large"}}, 413)(scope, receive, send); return
                chunks.append(data)
                if not incoming.get("more_body", False): break
            body = b"".join(chunks)
            sent_body = False
            original_receive = receive
            async def bounded_receive():
                nonlocal sent_body
                if not sent_body:
                    sent_body = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await original_receive()
            receive = bounded_receive
        async def secure_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [(b"x-guruconnect-mode", b"local"), (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"), (b"x-frame-options", b"DENY")]
            await send(message)
        await self.app(scope, receive, secure_send)


def safe_connection(row: dict) -> dict:
    allowed = {"id", "engine_id", "state", "generation", "config", "safe_metadata", "tested_at", "last_error_code", "updated_at", "queued_jobs", "inflight_jobs"}
    return {key: value for key, value in row.items() if key in allowed}


def public_conversation(row: dict) -> dict:
    return {key: value for key, value in row.items() if key not in {"lease_token", "lease_tab", "lease_until", "idempotency_key", "request_hash", "playback_path", "playback_object_path"}}


def conflict(error: ValueError):
    code = str(error)
    if code in {"idempotency_conflict", "stale_connection_generation", "chunk_conflict", "recording_not_saved", "recording_already_linked", "local_record_changed", "recording_lease_lost", "recording_already_finalized", "recording_already_leased", "manifest_conflict", "manifest_audio_missing"}:
        fail(code, 409)
    if code == "conversation_not_found": fail(code, 404)
    fail("local_request_invalid", 400)


def create_local_app(settings) -> FastAPI:
    if settings.environment not in {"local", "development"}:
        raise ValueError("Local mode cannot run in production")
    app = FastAPI(title="GuruConnect local development API")
    app.state.settings = settings
    app.state.local_store = LocalStore(settings.local_data_dir)
    store = app.state.local_store
    local_origins = [origin for origin in settings.cors_origins if urlsplit(origin).hostname in {"127.0.0.1", "localhost", "::1"} and urlsplit(origin).scheme == "http"]
    app.add_middleware(CORSMiddleware, allow_origins=local_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PATCH", "DELETE", "PUT", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "X-Workspace-ID", "Idempotency-Key"],
                       expose_headers=["Content-Disposition", "X-GuruConnect-Mode"], max_age=600)
    app.add_middleware(LocalBoundary, origins=local_origins)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, error):
        return JSONResponse({"detail": {"code": "validation_failed", "message": "Check the submitted fields.", "errors": [{"location": list(item["loc"]), "type": item["type"]} for item in error.errors()]}}, 422)

    def conversation(conversation_id: UUID | str):
        row = store.get("conversations", str(conversation_id))
        if not row or row.get("capture_state") == "deleted": fail("conversation_not_found", 404)
        return row

    def lease(row, token, part=None):
        if str(token) != row.get("lease_token") or row.get("lease_until", 0) < time.time(): fail("recording_lease_lost", 409)
        if part and str(part) != row.get("part_id"): fail("recording_part_mismatch", 409)

    def enquiry_response(data):
        value = dict(data)
        row = store.get("conversations", data.get("conversationId", "")) if data.get("conversationId") else None
        value["recording"] = {"captureState": row["capture_state"], "uploadState": row["upload_state"], "transcriptionState": row["transcription_state"], "englishState": row["english_state"], "speakerState": row["speaker_state"], "finalState": row["final_state"], "durationSeconds": row.get("duration_seconds", 0)} if row and row.get("capture_state") != "deleted" else None
        return value

    def get_connection(connection_id: UUID | str):
        selected = next((row for row in store.list("connections") if row["id"] == str(connection_id)), None)
        if not selected: fail("connection_not_found", 404)
        return selected

    @app.get("/health")
    @app.get("/ready")
    @app.get("/health/live")
    @app.get("/health/ready")
    async def health():
        store.settings()
        return {"status": "ok", "mode": "local", "database": "sqlite", "provider_keys_required_for_processing": True}

    @app.get("/api/v1/workspaces")
    async def workspaces():
        return {"items": [{"id": LOCAL_WORKSPACE_ID, "name": "Local workspace", "role": "owner"}], "next_cursor": None}

    @app.get("/api/v1/profile")
    async def profile():
        return store.get("profiles", LOCAL_USER_ID)

    @app.patch("/api/v1/profile")
    async def save_profile(body: s.ProfilePatch):
        return store.patch("profiles", LOCAL_USER_ID, body.model_dump(mode="json"))

    @app.get("/api/v1/connections")
    async def connections():
        items = []
        for engine in ("elevenlabs", "openai-translator"):
            row = store.connection(engine)
            if row:
                kinds = {"transcribe"} if engine == "elevenlabs" else {"translate"}
                jobs = [job for job in store.list("jobs") if job["kind"] in kinds]
                row.update(queued_jobs=sum(job["state"] in {"queued", "blocked"} for job in jobs), inflight_jobs=sum(job["state"] == "running" for job in jobs))
                items.append(safe_connection(row))
            else:
                items.append({"id": None, "engine_id": engine, "state": "disconnected", "generation": None, "config": {}, "tested_at": None, "last_error_code": "not_configured"})
        return {"items": items, "next_cursor": None}

    @app.post("/api/v1/connections", status_code=201)
    async def configure(body: s.ConnectionCreate):
        if body.engine_id not in {"elevenlabs", "openai-translator"}: fail("local_engine_not_supported", 400)
        validate_connection_config(body)
        try: row = store.configure_connection(body.engine_id, body.credentials, body.config, body.idempotency_key, body.expected_generation)
        except ValueError as error: conflict(error)
        return safe_connection(row)

    @app.post("/api/v1/connections/{connection_id}/test")
    async def test_connection(connection_id: UUID, body: s.TestConnection):
        row = get_connection(connection_id)
        if row["state"] in {"disconnected", "disabled"}: fail("connection_requires_configuration", 409)
        engine, generation = row["engine_id"], row["generation"]
        try: store.patch("connections", engine, {"state": "checking"}, {"generation": generation, "state": row["state"]})
        except ValueError: fail("connection_changed_during_test", 409)
        error_code, smoke = None, None
        try:
            key = store.secrets(engine).get("api_key", "")
            provider = ElevenLabsAdapter(key) if engine == "elevenlabs" else OpenAITranslator(key)
            smoke = await provider.test(functional=body.functional)
        except ProviderError as error:
            error_code = error.code
        except Exception:
            error_code = "provider_connection_failed"
        valid = error_code is None and bool(smoke and (smoke.get("connected") or smoke.get("ready")))
        metadata = dict(row.get("safe_metadata") or {})
        ready = valid
        if engine == "openai-translator":
            ready = valid and (bool(body.functional and smoke and smoke.get("functional")) or bool(metadata.get("functional_translation_ready") and row.get("tested_at")))
            metadata.update(model_access_verified=valid, functional_translation_ready=ready)
        updates = {"state": "invalid_credentials" if error_code and "credential" in error_code else "degraded" if error_code else "connected" if ready else "configured", "last_error_code": error_code, "safe_metadata": metadata, "tested_at": now() if ready else row.get("tested_at")}
        try: saved = store.patch("connections", engine, updates, {"generation": generation, "state": "checking"})
        except ValueError: fail("connection_changed_during_test", 409)
        return {**safe_connection(saved), "test": smoke, "functional": body.functional, "passed": valid}

    @app.post("/api/v1/connections/{connection_id}/disable")
    async def disable(connection_id: UUID):
        row = get_connection(connection_id)
        return safe_connection(store.change_connection_state(row["engine_id"], "disabled"))

    @app.post("/api/v1/connections/{connection_id}/enable")
    async def enable(connection_id: UUID):
        row = get_connection(connection_id)
        if row["state"] != "disabled" or not row.get("tested_at") or (row["engine_id"] == "openai-translator" and not row.get("safe_metadata", {}).get("functional_translation_ready")): fail("connection_test_required", 409)
        return safe_connection(store.patch("connections", row["engine_id"], {"state": "connected"}))

    @app.post("/api/v1/connections/{connection_id}/disconnect")
    async def disconnect(connection_id: UUID, body: s.Disconnect):
        row = get_connection(connection_id)
        engine = row["engine_id"]
        store.change_connection_state(engine, "disconnected", body.finish_in_flight)
        return {"disconnected": True, "audio_preserved": True}

    @app.get("/api/v1/engine-settings")
    async def engine_settings(): return store.settings()

    @app.patch("/api/v1/engine-settings")
    async def engine_settings_save(body: s.EngineSettings):
        if body.default_engine not in {None, "elevenlabs"} or any(engine != "elevenlabs" for engine in body.allowed_engines): fail("local_engine_not_supported", 400)
        if body.default_engine and body.default_engine not in body.allowed_engines: fail("default_engine_not_allowed")
        return store.put("settings", "engine", {"default_engine_id": body.default_engine, "allowed_engine_ids": body.allowed_engines, "salesperson_engine_selection": body.allow_salesperson_engine_selection, "comparison_allowed": False, "budget_policy": {"max_audio_seconds_per_day": body.budget_minutes * 60}})

    @app.post("/api/v1/enquiries", status_code=201)
    async def create_enquiry(body: EnquiryCreate, response: Response, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
        if not idempotency_key or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", idempotency_key): fail("idempotency_key_required", 400)
        try: row, created = store.create_enquiry(body.model_dump(mode="json"), idempotency_key)
        except ValueError as error: conflict(error)
        response.status_code = 201 if created else 200
        return enquiry_response(row)

    @app.get("/api/v1/enquiries")
    async def list_enquiries(page: int = Query(1, ge=1, le=100000), pageSize: int = Query(10, ge=1, le=100), query: str = Query("", max_length=120), channel: str = Query("", max_length=80), branch: str = Query("", max_length=160)):
        rows = [row for row in store.enquiries() if (not query or query.casefold() in " ".join(str(row.get(field, "")) for field in ("id", "enquiryNumber", "customerName", "phone")).casefold()) and (not channel or row["channel"] == channel) and (not branch or row["branch"] == branch)]
        return {"items": [enquiry_response(row) for row in rows[(page - 1) * pageSize:page * pageSize]], "total": len(rows), "page": page, "pageSize": pageSize, "totalPages": (len(rows) + pageSize - 1) // pageSize}

    @app.get("/api/v1/enquiries/{enquiry_id}")
    async def detail_enquiry(enquiry_id: UUID):
        row = next((row for row in store.enquiries() if row["id"] == str(enquiry_id)), None)
        if not row: fail("enquiry_not_found", 404)
        return enquiry_response(row)

    @app.post("/api/v1/conversations", status_code=201)
    async def create_conversation(body: s.ConversationCreate, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
        if not idempotency_key or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", idempotency_key): fail("idempotency_key_required", 400)
        data = body.model_dump(mode="json")
        if body.engine_id not in {None, "elevenlabs"}: fail("local_engine_not_supported")
        digest = hashlib.sha256(encoded(data).encode()).hexdigest()
        with store.transaction() as db:
            saved = db.execute("select data from records where table_name='conversation_keys' and id=?", (idempotency_key,)).fetchone()
            if saved:
                prior = json.loads(saved["data"])
                if prior["request_hash"] != digest: fail("idempotency_conflict", 409)
                current = db.execute("select data from records where table_name='conversations' and id=?", (prior["id"],)).fetchone()
                return public_conversation(json.loads(current["data"]))
            conversation_id, run_id = str(uuid4()), str(uuid4())
            row = {"id": conversation_id, "organisation_id": LOCAL_WORKSPACE_ID, "owner_id": LOCAL_USER_ID, "customer_name_snapshot": "Name not provided" if body.is_unnamed else body.customer_name, "salesperson_name_snapshot": "Local user", "title": body.title or "", "location": body.location or "", "created_at": now(), "capture_state": "idle", "upload_state": "pending", "transcription_state": "pending", "english_state": "pending", "speaker_state": "pending", "final_state": "pending", "total_samples": 0, "duration_seconds": 0, "consent_confirmed": True, "engine_id": "elevenlabs", "language_hint": body.language_hint, "audio_only": body.audio_only, "run_id": run_id, "active_version_id": None}
            db.execute("insert into records values('conversations',?,?)", (conversation_id, encoded(row)))
            db.execute("insert into records values('conversation_keys',?,?)", (idempotency_key, encoded({"id": conversation_id, "request_hash": digest})))
            db.execute("insert into records values('runs',?,?)", (run_id, encoded({"id": run_id, "conversation_id": conversation_id, "kind": "production", "engine_id": "elevenlabs", "model_version": "scribe_v2", "state": "pending", "created_at": now(), "coverage_samples": 0})))
        return public_conversation(row)

    @app.get("/api/v1/conversations")
    async def conversations(q: str = Query("", max_length=120), limit: int = Query(30, ge=1, le=100)):
        rows = [row for row in store.list("conversations") if row.get("capture_state") != "deleted" and (not q or q.casefold() in f"{row['customer_name_snapshot']} {row['title']}".casefold())]
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        return {"items": [public_conversation(row) for row in rows[:limit]], "next_cursor": None}

    @app.get("/api/v1/conversations/{conversation_id}")
    async def conversation_detail(conversation_id: UUID): return public_conversation(conversation(conversation_id))

    @app.patch("/api/v1/conversations/{conversation_id}")
    async def update_conversation(conversation_id: UUID, body: s.ConversationPatch):
        conversation(conversation_id)
        fields = body.model_dump(exclude_unset=True)
        if not fields: fail("empty_update", 400)
        try: return public_conversation(store.patch("conversations", str(conversation_id), fields))
        except ValueError as error: conflict(error)

    @app.post("/api/v1/conversations/{conversation_id}/lease")
    async def acquire(conversation_id: UUID, body: s.LeaseAcquire):
        conversation(conversation_id)
        try: return store.acquire_lease(str(conversation_id), body.tab_id, body.purpose)
        except ValueError as error: conflict(error)

    @app.post("/api/v1/conversations/{conversation_id}/lease/heartbeat")
    async def heartbeat(conversation_id: UUID, body: s.LeaseHeartbeat):
        row = conversation(conversation_id); lease(row, body.lease_token)
        if row.get("lease_tab") != body.tab_id: fail("recording_lease_lost", 409)
        try: store.patch("conversations", str(conversation_id), {"lease_until": time.time() + 90}, {"lease_token": str(body.lease_token)})
        except ValueError: fail("recording_lease_lost", 409)
        return {"renewed": True, "expires_in": 90}

    @app.post("/api/v1/conversations/{conversation_id}/events")
    async def events(conversation_id: UUID, body: s.CaptureEvent):
        row = conversation(conversation_id); lease(row, body.lease_token, body.part_id)
        if row["upload_state"] == "saved": fail("recording_already_finalized", 409)
        states = {"started": "recording", "pause": "paused", "paused": "paused", "resume": "recording", "resumed": "recording", "stop": "stopped", "stopped": "stopped", "interrupted": "interrupted", "device_removed": "interrupted", "storage_full": "interrupted", "limit_reached": "stopped"}
        try: store.patch("conversations", str(conversation_id), {"capture_state": states[body.kind]}, {"lease_token": str(body.lease_token), "upload_state": "pending"})
        except ValueError: fail("recording_lease_lost", 409)
        return {"saved": True}

    @app.post("/api/v1/conversations/{conversation_id}/chunks/authorize")
    async def authorize(conversation_id: UUID, body: s.Chunk, request: Request):
        row = conversation(conversation_id)
        value = body.model_dump(mode="json", exclude={"lease_token", "conversation_id"})
        prior = next((item for item in store.chunks(str(conversation_id), False) if item["part_id"] == str(body.part_id) and item["sequence"] == body.sequence), None)
        if row["upload_state"] == "saved":
            if prior and prior["confirmed"] and all(prior.get(key) == value[key] for key in value): return {"confirmed": True}
            fail("recording_already_finalized", 409)
        lease(row, body.lease_token, body.part_id)
        if body.start_sample + body.sample_count > MAX_SESSION_SAMPLES: fail("conversation_duration_exceeded")
        try: stored = store.authorize_chunk(str(conversation_id), value, str(body.lease_token))
        except ValueError as error: conflict(error)
        if stored["confirmed"]: return {"confirmed": True}
        expires = int(time.time()) + 300
        binding = f"{conversation_id}:{body.part_id}:{body.sequence}:{body.sha256}"
        token = store.token("upload", binding, expires)
        return {"upload_url": f"{str(request.base_url).rstrip('/')}/api/v1/local-audio/{conversation_id}/{body.part_id}/{body.sequence}?token={token}", "headers": {"Content-Type": "audio/wav"}, "method": "PUT", "expires_in": 300}

    @app.put("/api/v1/local-audio/{conversation_id}/{part_id}/{sequence}")
    async def upload(conversation_id: UUID, part_id: UUID, sequence: int, request: Request, token: str = Query(..., max_length=150)):
        row = conversation(conversation_id)
        metadata = next((item for item in store.chunks(str(conversation_id), False) if item["part_id"] == str(part_id) and item["sequence"] == sequence), None)
        if not metadata: fail("chunk_not_authorized", 404)
        if not store.verify_token(token, "upload", f"{conversation_id}:{part_id}:{sequence}:{metadata['sha256']}"): fail("upload_permission_expired", 403)
        if row["upload_state"] == "saved": fail("recording_already_finalized", 409)
        data = await request.body()
        try: info = validate_wav(data, metadata["sample_count"], max_samples=160000)
        except ValueError: fail("invalid_audio", 400)
        if len(data) != metadata["bytes"] or info.sha256 != metadata["sha256"]: fail("input_hash_mismatch", 409)
        path = store.chunk_path(str(conversation_id), str(part_id), sequence)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.parent.resolve().is_relative_to(store.root): fail("local_audio_path_invalid", 400)
        scratch = path.with_name(f".{path.name}.{uuid4()}.tmp")
        try:
            descriptor = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as file:
                file.write(data); file.flush(); os.fsync(file.fileno())
            os.replace(scratch, path)
        finally: scratch.unlink(missing_ok=True)
        store.mark_chunk(str(conversation_id), str(part_id), sequence)
        return {"uploaded": True}

    @app.post("/api/v1/conversations/{conversation_id}/chunks/confirm")
    async def confirm(conversation_id: UUID, body: s.Chunk):
        row = conversation(conversation_id)
        value = body.model_dump(mode="json", exclude={"lease_token", "conversation_id"})
        prior = next((item for item in store.chunks(str(conversation_id), False) if item["part_id"] == str(body.part_id) and item["sequence"] == body.sequence), None)
        if not prior or any(prior.get(key) != value[key] for key in value): fail("chunk_conflict", 409)
        if prior["confirmed"]: return {"confirmed": True}
        lease(row, body.lease_token, body.part_id)
        path = store.chunk_path(str(conversation_id), str(body.part_id), body.sequence)
        if not path.is_file() or path.is_symlink(): fail("chunk_upload_missing", 409)
        data = path.read_bytes()
        if len(data) != body.bytes or hashlib.sha256(data).hexdigest() != body.sha256: fail("input_hash_mismatch", 409)
        try: store.mark_chunk(str(conversation_id), str(body.part_id), body.sequence, confirmed=True, lease_token=str(body.lease_token))
        except ValueError as error: conflict(error)
        return {"confirmed": True, "saved_samples": sum(item["sample_count"] for item in store.chunks(str(conversation_id)))}

    @app.post("/api/v1/conversations/{conversation_id}/finalize")
    async def finalize(conversation_id: UUID, body: s.Manifest):
        conversation(conversation_id)
        value = body.model_dump(mode="json", exclude={"lease_token"})
        try: return store.finalize_conversation(str(conversation_id), value, str(body.lease_token))
        except ValueError as error: conflict(error)

    @app.get("/api/v1/conversations/{conversation_id}/progress")
    async def progress(conversation_id: UUID):
        row = conversation(conversation_id)
        jobs = [{"id": job["id"], "run_id": job["run_id"], "kind": job["kind"], "state": job["state"], "error_code": job["error_code"]} for job in store.list("jobs", {"conversation_id": str(conversation_id)})]
        runs = store.list("runs", {"conversation_id": str(conversation_id)})
        return {"conversation": public_conversation(row), "runs": runs, "jobs": jobs, "chunks": {"saved_samples": sum(chunk["sample_count"] for chunk in store.chunks(str(conversation_id)))}, "missing_chunks": []}

    @app.get("/api/v1/conversations/{conversation_id}/transcript")
    async def transcript(conversation_id: UUID, version_id: UUID | None = None):
        conversation(conversation_id)
        value = store.get_transcript(str(conversation_id))
        if version_id and (value.get("version") or {}).get("id") != str(version_id):
            value = store.get("versions", str(version_id))
            if not value or str(version_id) not in {(item.get("version") or {}).get("id") for item in [store.get_transcript(str(conversation_id))]}:
                # Explicit historic selection is not part of localhost mode yet.
                fail("version_not_found", 404)
        return value

    @app.get("/api/v1/conversations/{conversation_id}/playback")
    async def playback(conversation_id: UUID, request: Request):
        conversation(conversation_id)
        path = store.audio_path(str(conversation_id))
        if not path.is_file() or path.is_symlink(): fail("playback_assembly_pending", 409)
        expires = int(time.time()) + 300
        token = store.token("playback", str(conversation_id), expires)
        return {"url": f"{str(request.base_url).rstrip('/')}/api/v1/local-playback/{conversation_id}?token={token}", "expires_in": 300}

    @app.get("/api/v1/local-playback/{conversation_id}")
    async def read_playback(conversation_id: UUID, token: str = Query(..., max_length=150)):
        conversation(conversation_id)
        if not store.verify_token(token, "playback", str(conversation_id)): fail("playback_permission_expired", 403)
        path = store.audio_path(str(conversation_id))
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(store.root): fail("playback_assembly_pending", 409)
        return FileResponse(path, media_type="audio/wav")

    @app.get("/api/v1/conversations/{conversation_id}/runs")
    async def runs(conversation_id: UUID):
        conversation(conversation_id)
        return {"items": store.list("runs", {"conversation_id": str(conversation_id)}), "next_cursor": None}

    @app.post("/api/v1/conversations/{conversation_id}/runs")
    async def replay(conversation_id: UUID):
        conversation(conversation_id)
        fail("local_replay_not_supported", 409, "Additional paid speech passes require the authenticated workspace version-review workflow.")

    @app.post("/api/v1/conversations/{conversation_id}/retry")
    async def retry(conversation_id: UUID, body: s.Retry):
        conversation(conversation_id)
        kinds = {"all": ["assemble", "transcribe", "translate"], "transcription": ["transcribe"], "translation": ["translate"], "diarization": ["transcribe"], "finalisation": ["assemble"]}[body.stage]
        source = store.get_transcript(str(conversation_id))
        version = source.get("version")
        if body.stage in {"translation", "all"} and version and any(turn.get("english_text") is None for turn in source["turns"]) and not any(job["kind"] == "translate" and job.get("run_id") == version.get("run_id") for job in store.list("jobs", {"conversation_id": str(conversation_id)})):
            # Recover the commit-to-queue crash window using source only, never a new speech request.
            store.enqueue_job(str(conversation_id), "translate", {"run_id": version["run_id"], "source_version_id": version["id"]})
        resumed, uncertain = store.retry_jobs(str(conversation_id), kinds)
        if resumed:
            updates = {"final_state": "queued", "processing_error_code": None}
            updates["english_state" if body.stage == "translation" else "transcription_state"] = "queued"
            store.patch("conversations", str(conversation_id), updates)
        return {"resumed_job_ids": resumed, "uncertain_job_ids": uncertain, "message": "Uncertain submissions require administrator reconciliation." if uncertain else "Eligible jobs resumed."}

    @app.post("/api/v1/jobs/{job_id}/retry")
    async def retry_single(job_id: UUID):
        job = store.get("jobs", str(job_id))
        if not job: fail("job_not_found", 404)
        resumed, uncertain = store.retry_jobs(job["conversation_id"], [job["kind"]])
        return {"resumed_job_ids": resumed, "uncertain_job_ids": uncertain}

    def amend(conversation_id, version_id, operation):
        with store.transaction() as db:
            if db.execute("select 1 from jobs where conversation_id=? and kind in ('transcribe','translate') and state in ('queued','running')", (str(conversation_id),)).fetchone(): fail("processing_revision_conflict", 409, "Wait for processing to finish or become blocked before editing this result.")
            row = db.execute("select data from records where table_name='transcripts' and id=?", (str(conversation_id),)).fetchone()
            value = json.loads(row["data"]) if row else None
            if not value or (value.get("version") or {}).get("id") != str(version_id): fail("version_not_found", 404)
            result = operation(value)
            db.execute("update records set data=? where table_name='transcripts' and id=?", (encoded(value), str(conversation_id)))
            db.execute("insert into records values('versions',?,?) on conflict(table_name,id) do update set data=excluded.data", (str(version_id), encoded(value)))
            return result

    @app.post("/api/v1/conversations/{conversation_id}/revisions", status_code=201)
    async def revision(conversation_id: UUID, body: s.Revision):
        conversation(conversation_id)
        def revise(value):
            turn = next((turn for turn in value["turns"] if turn["id"] == str(body.turn_id)), None)
            if not turn: fail("turn_not_found", 404)
            change = body.model_dump(mode="json", exclude={"version_id", "turn_id"}, exclude_none=True)
            for key, text in change.items():
                turn.setdefault(f"original_{key}", turn.get(key)); turn[key] = text
            turn["manually_corrected"] = True
            record = {"id": str(uuid4()), "kind": "text", "actor_id": LOCAL_USER_ID, "turn_id": str(body.turn_id), "change": change, "created_at": now()}
            value["revisions"].append(record)
            return record
        return amend(conversation_id, body.version_id, revise)

    @app.post("/api/v1/conversations/{conversation_id}/speakers")
    async def assign_speaker(conversation_id: UUID, body: s.SpeakerAssignment):
        conversation(conversation_id)
        def assign(value):
            if not any(turn["speaker_ref"] == body.speaker_ref for turn in value["turns"]): fail("speaker_not_found", 404)
            selected = next((speaker for speaker in value["speakers"] if speaker["speaker_ref"] == body.speaker_ref), None)
            if not selected:
                selected = {"speaker_ref": body.speaker_ref}; value["speakers"].append(selected)
            selected.update(display_name=body.name, role=body.role, is_provisional=False)
            value["revisions"].append({"id": str(uuid4()), "kind": "speaker", "actor_id": LOCAL_USER_ID, "change": {"speaker_ref": body.speaker_ref, "name": body.name, "role": body.role}, "created_at": now()})
            return selected
        return amend(conversation_id, body.version_id, assign)

    @app.get("/api/v1/conversations/{conversation_id}/export")
    async def export(conversation_id: UUID, format: str = Query("txt", pattern="^(txt|srt|json)$")):
        conversation(conversation_id)
        value = store.get_transcript(str(conversation_id))
        if not value["version"]: fail("transcript_not_available", 409)
        names = {speaker["speaker_ref"]: speaker.get("display_name") for speaker in value["speakers"]}
        def stamp(seconds):
            milliseconds = max(0, round(seconds * 1000)); hours, rest = divmod(milliseconds, 3600000); minutes, rest = divmod(rest, 60000); whole, fraction = divmod(rest, 1000)
            return f"{hours:02}:{minutes:02}:{whole:02},{fraction:03}"
        if format == "json": content, mime = json.dumps(value, ensure_ascii=False, indent=2), "application/json"
        else:
            lines = []
            for index, turn in enumerate(value["turns"]):
                text = turn["english_text"] if turn.get("english_text") is not None else f"[Original; English {value['version']['english_state']}] {turn.get('source_text') or ''}"
                speaker = names.get(turn["speaker_ref"]) or turn["speaker_ref"] or "Unresolved speaker"
                lines.append(f"{index + 1}\n{stamp(turn['start_seconds'])} --> {stamp(turn['end_seconds'])}\n{speaker}: {text}\n" if format == "srt" else f"[{stamp(turn['start_seconds'])}] {speaker}: {text}")
            content, mime = "\n".join(lines), "application/x-subrip" if format == "srt" else "text/plain"
        return Response(content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="conversation-{conversation_id}.{format}"'})

    @app.post("/api/v1/conversations/{conversation_id}/cancel")
    async def cancel(conversation_id: UUID):
        conversation(conversation_id)
        for job in store.list("jobs", {"conversation_id": str(conversation_id)}):
            if job["state"] not in {"succeeded", "cancelled"}: store.patch("jobs", job["id"], {"state": "cancelled", "lease_token": None, "lease_until": None})
        store.patch("conversations", str(conversation_id), {"final_state": "cancelled"})
        return {"cancelled": True, "audio_preserved": True}

    return app
