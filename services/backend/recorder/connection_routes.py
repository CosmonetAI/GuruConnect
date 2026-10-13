import hashlib
import json
import secrets
from uuid import UUID
from fastapi import APIRouter, Depends, Request, HTTPException
from psycopg.types.json import Jsonb
from . import schemas as s
from .db import one, many
from .errors import fail
from .providers import ElevenLabsAdapter, AzureTranslator, ProviderError
from .openai_translator import OpenAITranslator, OPENAI_TRANSLATION_MODEL
from .routes import audit
from .security import Workspace, workspace, administrator, SecretBox, token_hash

router = APIRouter(prefix="/api/v1")


def safe_connection(row: dict) -> dict:
    # Explicit response allowlist prevents future private columns escaping.
    fields = {"id", "organisation_id", "engine_id", "state", "generation", "config", "safe_metadata", "max_concurrency", "tested_at", "last_error_code", "updated_at", "created_at", "worker_last_seen", "queued_jobs", "inflight_jobs"}
    output = {key: value for key, value in row.items() if key in fields}
    output["safe_metadata"] = {key: value for key, value in row.get("safe_metadata", {}).items() if key not in {"idempotency_key", "request_hash"}}
    return output


def validate_connection_config(body: s.ConnectionCreate) -> None:
    allowed = {"model", "max_concurrency"} if body.engine_id == "openai-translator" else {"model_revision", "source_pass", "device", "compute_type", "finalisation_enabled", "max_concurrency", "region"}
    if set(body.config) - allowed or len(json.dumps(body.config)) > 5000:
        fail("invalid_engine_configuration")
    credentials_allowed = {"elevenlabs": {"api_key"}, "azure-translator": {"api_key", "region"}, "openai-translator": {"api_key"}, "faster-whisper": set()}[body.engine_id]
    if set(body.credentials) - credentials_allowed:
        fail("unsupported_credential_field")
    if any(not value or len(value) > 2000 for value in body.credentials.values()):
        fail("invalid_credential")
    if body.engine_id != "faster-whisper" and not body.credentials.get("api_key"):
        fail("provider_api_key_required")
    if body.engine_id == "openai-translator":
        if body.config.get("model", OPENAI_TRANSLATION_MODEL) != OPENAI_TRANSLATION_MODEL:
            fail("openai_model_not_allowed")
        # Persist the requested model explicitly; never silently select a fallback.
        body.config["model"] = OPENAI_TRANSLATION_MODEL
    for key in {"source_pass", "finalisation_enabled"} & body.config.keys():
        if not isinstance(body.config[key], bool):
            fail("invalid_engine_configuration")
    if "device" in body.config and body.config["device"] not in {"cpu", "cuda"}:
        fail("invalid_inference_device")
    if "compute_type" in body.config and body.config["compute_type"] not in {"int8", "float16", "int8_float16", "float32"}:
        fail("invalid_inference_compute_type")
    maximum = body.config.get("max_concurrency", 2)
    if type(maximum) is not int or not 1 <= maximum <= 50:
        fail("invalid_provider_concurrency")


@router.get("/connections")
async def connections(request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        rows = await many(connection, "select c.*,(select max(last_seen_at) from inference_workers w where w.connection_id=c.id and w.revoked_at is null) as worker_last_seen,(select count(*) from processing_jobs j where j.connection_id=c.id and j.state in ('queued','retry','blocked')) as queued_jobs,(select count(*) from processing_jobs j where j.connection_id=c.id and j.state='running' and j.lease_until>now()) as inflight_jobs from engine_connections c where c.organisation_id=%s order by engine_id", (context.id,))
    configured = {row["engine_id"]: safe_connection(row) for row in rows}
    for engine_id in ("elevenlabs", "faster-whisper", "azure-translator", "openai-translator"):
        configured.setdefault(engine_id, {"id": None, "engine_id": engine_id, "state": "disconnected", "generation": None, "config": {}, "tested_at": None, "last_error_code": "not_configured"})
    return {"items": list(configured.values()), "next_cursor": None}


@router.post("/connections", status_code=201)
async def connect(body: s.ConnectionCreate, request: Request, context: Workspace = Depends(administrator)):
    validate_connection_config(body)
    # Credential values never appear in audit events, errors or responses.
    ciphertext = SecretBox(request.app.state.settings).encrypt(body.credentials) if body.credentials else None
    identity = hashlib.sha256(json.dumps(body.model_dump(exclude={"idempotency_key", "expected_generation"}), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    async with request.app.state.db.connection() as connection:
        await connection.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", (f"connection:{context.id}:{body.engine_id}",))
        old = await one(connection, "select * from engine_connections where organisation_id=%s and engine_id=%s for update", (context.id, body.engine_id))
        if old and old["safe_metadata"].get("idempotency_key") == body.idempotency_key:
            if old["safe_metadata"].get("request_hash") != identity:
                fail("idempotency_conflict", 409)
            return safe_connection(old)
        if body.expected_generation is not None and (not old or old["generation"] != body.expected_generation):
            fail("stale_connection_generation", 409,
                 "This connection changed since the form opened. Refresh and review its configuration before saving again",
                 current_generation=old["generation"] if old else None)
        if old:
            await one(connection, "select disconnect_connection(%s,%s,false,false)", (old["id"], context.user.id))
            row = await one(connection, "update engine_connections set state='configured',config=%s,tested_at=null,last_error_code=null,safe_metadata=%s,credential_scope=%s,updated_at=now() where id=%s returning *", (Jsonb(body.config), Jsonb({"idempotency_key": body.idempotency_key, "request_hash": identity}), token_hash(body.credentials["api_key"]) if body.credentials else None, old["id"]))
        else:
            row = await one(connection, "insert into engine_connections(organisation_id,engine_id,state,config,safe_metadata,credential_scope) values(%s,%s,'configured',%s,%s,%s) returning *", (context.id, body.engine_id, Jsonb(body.config), Jsonb({"idempotency_key": body.idempotency_key, "request_hash": identity}), token_hash(body.credentials["api_key"]) if body.credentials else None))
        if ciphertext:
            await connection.execute("insert into provider_secrets(connection_id,encrypted_value,encryption_key_version) values(%s,%s,%s) on conflict(connection_id) do update set encrypted_value=excluded.encrypted_value,encryption_key_version=excluded.encryption_key_version,updated_at=now()", (row["id"], ciphertext, request.app.state.settings.credential_key_version))
        await connection.execute("update engine_connections set max_concurrency=%s where id=%s", (body.config.get("max_concurrency", 2), row["id"]))
        row["max_concurrency"] = body.config.get("max_concurrency", 2)
        if body.engine_id in {"azure-translator", "openai-translator"}:
            await connection.execute("update workspace_engine_settings set translator_connection_id=%s where organisation_id=%s", (row["id"], context.id))
        await audit(connection, context, "connection.configured", {"connection_id": str(row["id"]), "engine_id": body.engine_id, "generation": row["generation"]})
    return safe_connection(row)


async def get_connection(connection, context, connection_id, lock=False):
    row = await one(connection, "select * from engine_connections where id=%s and organisation_id=%s" + (" for update" if lock else ""), (connection_id, context.id))
    if not row:
        fail("connection_not_found", 404)
    return row


@router.post("/connections/{connection_id}/test")
async def test(connection_id: UUID, body: s.TestConnection, request: Request, context: Workspace = Depends(administrator)):
    async with request.app.state.db.connection() as connection:
        row = await get_connection(connection, context, connection_id, True)
        if row["state"] == "disconnected":
            fail("connection_requires_configuration", 409)
        secret = await one(connection, "select encrypted_value from provider_secrets where connection_id=%s", (connection_id,))
        workers = await one(connection, "select count(*) as ready from inference_workers where connection_id=%s and connection_generation=%s and revoked_at is null and state in ('ready','busy') and last_seen_at>now()-interval '2 minutes'", (connection_id, row["generation"]))
        await connection.execute("update engine_connections set state='checking',updated_at=now() where id=%s", (connection_id,))
    error = None
    smoke = None
    try:
        credentials = SecretBox(request.app.state.settings).decrypt(secret["encrypted_value"]) if secret else {}
        if row["engine_id"] == "elevenlabs":
            smoke = await ElevenLabsAdapter(credentials.get("api_key", "")).test(functional=body.functional)
        elif row["engine_id"] == "azure-translator":
            smoke = await AzureTranslator(credentials.get("api_key", ""), credentials.get("region") or row["config"].get("region", "")).test(functional=body.functional)
        elif row["engine_id"] == "openai-translator":
            smoke = await OpenAITranslator(credentials.get("api_key", "")).test(functional=body.functional)
        else:
            if not workers["ready"]:
                fail("inference_worker_unavailable", 409)
            if body.functional:
                fail("functional_test_requires_authorized_audio", 409, "Record a consented test clip and explicitly create a Faster-Whisper comparison to test inference")
            smoke = {"ready": True, "functional": False, "note": "Worker heartbeat checked; no inference accuracy claim"}
    except ProviderError as exc:
        error = exc.code
    except HTTPException as exc:
        error = exc.detail.get("code", "connection_test_failed") if isinstance(exc.detail, dict) else "connection_test_failed"
    finally:
        async with request.app.state.db.connection() as connection:
            current = await get_connection(connection, context, connection_id, True)
            if current["generation"] != row["generation"] or current["state"] != "checking":
                fail("connection_changed_during_test", 409)
            validated = error is None and bool(smoke and (smoke.get("connected") or smoke.get("ready")))
            ready = validated
            tested_now = validated
            metadata = dict(current.get("safe_metadata") or {})
            if row["engine_id"] == "openai-translator":
                # A free model metadata GET proves access to that model listing.
                # Only an explicitly requested successful paid text smoke test
                # validates production translation; retain that same-generation
                # result during later metadata checks.
                previously_ready = bool(metadata.get("functional_translation_ready") and current.get("tested_at"))
                functional_ready = bool(validated and body.functional and smoke and smoke.get("functional"))
                ready = validated and (functional_ready or previously_ready)
                tested_now = functional_ready
                metadata.update({"model_access_verified": validated, "functional_translation_ready": ready})
            state = "invalid_credentials" if error and "credential" in error else "degraded" if error else "connected" if ready else "configured"
            updated = await one(connection, "update engine_connections set state=%s,last_error_code=%s,tested_at=case when %s then now() else tested_at end,safe_metadata=%s,updated_at=now() where id=%s returning *", (state, error, tested_now, Jsonb(metadata), connection_id))
            await audit(connection, context, "connection.tested", {"connection_id": str(connection_id), "functional": body.functional, "error_code": error})
    return {**safe_connection(updated), "test": smoke, "functional": body.functional, "passed": validated}


@router.post("/connections/{connection_id}/enable")
async def enable(connection_id: UUID, request: Request, context: Workspace = Depends(administrator)):
    async with request.app.state.db.connection() as connection:
        row = await get_connection(connection, context, connection_id, True)
        if row["state"] != "disabled" or not row["tested_at"]:
            fail("connection_test_required", 409)
        if row["engine_id"] == "openai-translator" and not row.get("safe_metadata", {}).get("functional_translation_ready"):
            fail("connection_test_required", 409)
        row = await one(connection, "update engine_connections set state='connected',updated_at=now() where id=%s returning *", (connection_id,))
        await audit(connection, context, "connection.enabled", {"connection_id": str(connection_id)})
    return safe_connection(row)


@router.post("/connections/{connection_id}/disable")
async def disable(connection_id: UUID, request: Request, context: Workspace = Depends(administrator)):
    async with request.app.state.db.connection() as connection:
        await get_connection(connection, context, connection_id, True)
        row = await one(connection, "select disconnect_connection(%s,%s,false,true) as impact", (connection_id, context.user.id))
    return row["impact"]


@router.post("/connections/{connection_id}/disconnect")
async def disconnect(connection_id: UUID, body: s.Disconnect, request: Request, context: Workspace = Depends(administrator)):
    async with request.app.state.db.connection() as connection:
        await get_connection(connection, context, connection_id, True)
        row = await one(connection, "select disconnect_connection(%s,%s,%s,false) as impact", (connection_id, context.user.id, body.finish_in_flight))
    return row["impact"]


@router.get("/engine-settings")
async def engine_settings(request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        return await one(connection, "select * from workspace_engine_settings where organisation_id=%s", (context.id,))


@router.patch("/engine-settings")
async def save_settings(body: s.EngineSettings, request: Request, context: Workspace = Depends(administrator)):
    if body.default_engine and body.default_engine not in body.allowed_engines:
        fail("default_engine_not_allowed")
    async with request.app.state.db.connection() as connection:
        if body.default_engine:
            row = await one(connection, "select id from engine_connections where organisation_id=%s and engine_id=%s and state='connected' and tested_at is not null", (context.id, body.default_engine))
            if not row:
                fail("default_engine_not_ready", 409)
        row = await one(connection, "update workspace_engine_settings set default_engine_id=%s,allowed_engine_ids=%s,salesperson_engine_selection=%s,comparison_allowed=%s,budget_policy=%s,updated_at=now() where organisation_id=%s returning *", (body.default_engine, body.allowed_engines, body.allow_salesperson_engine_selection, body.allow_comparison, Jsonb({"max_audio_seconds_per_day": body.budget_minutes*60}), context.id))
        await audit(connection, context, "engine_settings.updated", {"default_engine": body.default_engine, "allow_comparison": body.allow_comparison})
    return row


@router.post("/connections/{connection_id}/workers", status_code=201)
async def provision_worker(connection_id: UUID, request: Request, context: Workspace = Depends(administrator)):
    token = secrets.token_urlsafe(48)
    async with request.app.state.db.connection() as connection:
        row = await get_connection(connection, context, connection_id, True)
        if row["engine_id"] != "faster-whisper" or row["state"] == "disconnected":
            fail("configured_inference_connection_required", 409)
        worker = await one(connection, "insert into inference_workers(organisation_id,connection_id,connection_generation,token_hash,name) values(%s,%s,%s,%s,'Native inference worker') returning id,connection_id,connection_generation", (context.id, connection_id, row["generation"], token_hash(token)))
        await audit(connection, context, "worker.provisioned", {"worker_id": str(worker["id"])})
    return {**worker, "token": token, "message": "Copy this token now; it is returned only once"}


@router.delete("/workers/{worker_id}")
async def revoke_worker(worker_id: UUID, request: Request, context: Workspace = Depends(administrator)):
    async with request.app.state.db.connection() as connection:
        row = await one(connection, "update inference_workers set state='revoked',revoked_at=now() where id=%s and organisation_id=%s returning id", (worker_id, context.id))
        if not row:
            fail("worker_not_found", 404)
        await audit(connection, context, "worker.revoked", {"worker_id": str(worker_id)})
    return {"revoked": True}
