import hashlib
import json
from uuid import UUID
from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials
from psycopg.types.json import Jsonb
from . import schemas as s
from .db import one, many
from .errors import fail
from .processing import publish_result
from .security import bearer, token_hash

router = APIRouter(prefix="/internal/v1")


async def internal_worker(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if not credentials or credentials.scheme.lower() != "bearer":
        fail("worker_authentication_required", 401)
    async with request.app.state.db.connection() as connection:
        row = await one(connection, "select w.* from inference_workers w join engine_connections c on c.id=w.connection_id where w.token_hash=%s and w.revoked_at is null and w.state<>'revoked' and w.connection_generation=c.generation and c.state in ('configured','checking','connected','degraded','disabled')", (token_hash(credentials.credentials),))
        if not row:
            fail("worker_credentials_revoked_or_invalid", 401)
    return row


@router.post("/heartbeat")
async def heartbeat(body: s.WorkerHeartbeat, request: Request, worker: dict = Depends(internal_worker)):
    if set(body.metrics) - {"gpu_memory_mb", "gpu_utilization", "queue_delay_seconds", "processing_seconds", "memory_mb"}:
        fail("unsupported_worker_metric")
    if any(value < 0 or value > 1e9 for value in body.metrics.values()):
        fail("invalid_worker_metric")
    async with request.app.state.db.connection() as connection:
        await connection.execute("update inference_workers set state=%s,last_seen_at=now(),model_version=%s,metrics=%s where id=%s and revoked_at is null", ("ready" if body.ready else "degraded", body.model_version, Jsonb(body.metrics), worker["id"]))
    return {"accepted": True, "connection_generation": worker["connection_generation"]}


@router.post("/claim")
async def claim(body: s.WorkerClaim, request: Request, worker: dict = Depends(internal_worker)):
    async with request.app.state.db.connection() as connection:
        jobs = await many(connection, "select * from claim_jobs(%s,'inference',%s,%s,%s,%s)", (str(worker["id"]), body.limit, request.app.state.settings.worker_lease_seconds, worker["connection_id"], worker["id"]))
        if not jobs:
            return {"jobs": []}
        job = jobs[0]
        conv = await one(connection, "select * from conversations where id=%s and deleted_at is null", (job["conversation_id"],))
        run = await one(connection, "select * from processing_runs where id=%s", (job["run_id"],))
        artifact = await one(connection, "select * from inference_artifacts where job_id=%s and kind='input' order by created_at desc limit 1", (job["id"],))
        base = await one(connection,"select result from processing_jobs where id=%s and conversation_id=%s and run_id=%s and state='succeeded'",(job["payload"].get("source_job_id"),job["conversation_id"],job["run_id"])) if job["kind"]=='diarize' else None
        if not artifact or not conv or not run:
            fail("prepared_job_audio_unavailable", 409)
    start, end = int(job["payload"].get("sample_start", 0)), int(job["payload"]["sample_end"])
    url = await request.app.state.storage.signed_read(artifact["object_path"])
    return {"jobs": [{"job_id": job["id"], "conversation_id": conv["id"], "organisation_id": job["organisation_id"], "lease_token": job["lease_token"], "fence": job["fence"], "fencing_token": job["fence"], "lease_until": job["lease_until"], "audio_url": url, "sha256": artifact["sha256"], "bytes": artifact["bytes"], "sample_start": start, "sample_end": end, "ownership_start": job["payload"].get("ownership_start", start), "ownership_end": job["payload"].get("ownership_end", end), "language_hint": conv["language_hint"], "run_id": run["id"], "connection_generation": job["connection_generation"], "kind": job["kind"], "config": run["config_snapshot"],"base_result":base["result"] if base else None}]}

async def worker_job(connection, worker, job_id):
    row = await one(connection, "select * from processing_jobs where id=%s and inference_worker_id=%s and organisation_id=%s and connection_id=%s", (job_id, worker["id"], worker["organisation_id"], worker["connection_id"]))
    if not row:
        fail("worker_job_not_found", 404)
    return row


async def active_worker_job(connection, worker, job_id, body):
    job = await worker_job(connection, worker, job_id)
    live = await one(connection, "select id from processing_jobs where id=%s and lease_token=%s and fence=%s and state='running' and lease_until>now() and cancelled_at is null and connection_generation=%s", (job_id, body.lease_token, body.fencing_token, worker["connection_generation"]))
    if not live:
        fail("job_lease_expired_or_fenced", 409)
    conv = await one(connection, "select * from conversations where id=%s and deleted_at is null", (job["conversation_id"],))
    if not conv:
        fail("job_conversation_deleted", 409)
    return job, conv


@router.post("/jobs/{job_id}/artifacts/authorize")
async def artifact_authorize(job_id: UUID, body: s.ArtifactUpload, request: Request, worker: dict = Depends(internal_worker)):
    async with request.app.state.db.connection() as connection:
        job, conv = await active_worker_job(connection, worker, job_id, body)
    path = f"{job['organisation_id']}/{conv['owner_id']}/{conv['id']}/artifacts/{job_id}/{body.fencing_token}/output-{body.sha256}.json"
    permit = await request.app.state.storage.authorize_upload(path, "application/json")
    return {**permit, "method": "PUT", "sha256": body.sha256, "bytes": body.bytes}


@router.post("/jobs/{job_id}/artifacts/confirm")
async def artifact_confirm(job_id: UUID, body: s.ArtifactUpload, request: Request, worker: dict = Depends(internal_worker)):
    async with request.app.state.db.connection() as connection:
        job, conv = await active_worker_job(connection, worker, job_id, body)
    path = f"{job['organisation_id']}/{conv['owner_id']}/{conv['id']}/artifacts/{job_id}/{body.fencing_token}/output-{body.sha256}.json"
    data = await request.app.state.storage.read(path, body.bytes)
    if len(data) != body.bytes or hashlib.sha256(data).hexdigest() != body.sha256:
        fail("artifact_integrity_failed", 422)
    try:
        content = json.loads(data)
        if not isinstance(content, dict):
            raise ValueError()
    except ValueError:
        fail("invalid_json_artifact", 422)
    async with request.app.state.db.connection() as connection:
        await active_worker_job(connection, worker, job_id, body)
        artifact = await one(connection, "insert into inference_artifacts(conversation_id,run_id,job_id,kind,object_path,sha256,bytes) values(%s,%s,%s,'worker-output',%s,%s,%s) on conflict(object_path) do nothing returning id", (conv["id"], job["run_id"], job_id, path, body.sha256, body.bytes))
        if not artifact:
            artifact = await one(connection, "select id from inference_artifacts where object_path=%s and sha256=%s and bytes=%s and job_id=%s", (path, body.sha256, body.bytes, job_id))
        if not artifact:
            fail("immutable_artifact_conflict", 409)
    return {"confirmed": True, "artifact_id": artifact["id"]}


@router.post("/jobs/{job_id}/renew")
async def renew(job_id: UUID, body: s.WorkerLease, request: Request, worker: dict = Depends(internal_worker)):
    async with request.app.state.db.connection() as connection:
        await worker_job(connection, worker, job_id)
        row = await one(connection, "select renew_job(%s,%s,%s,%s,%s) as renewed", (job_id, str(worker["id"]), body.lease_token, body.fencing_token, request.app.state.settings.worker_lease_seconds))
    if not row["renewed"]:
        fail("job_lease_expired_or_fenced", 409)
    return {"renewed": True}


@router.post("/jobs/{job_id}/result")
async def result(job_id: UUID, body: s.WorkerResult, request: Request, worker: dict = Depends(internal_worker)):
    async with request.app.state.db.connection() as connection:
        job = await worker_job(connection, worker, job_id)
        duration = (int(job["payload"]["sample_end"]) - int(job["payload"].get("sample_start", 0))) / 16000
        if body.english_segments is None:
            fail("native_english_result_required", 422)
        for segment in (body.source_segments or []) + body.english_segments:
            if segment.end > duration + .25:
                fail("result_audio_range_exceeded", 422)
            for word in segment.words or []:
                if word.end < word.start or word.start < segment.start or word.end > segment.end + .25:
                    fail("invalid_word_timing", 422)
        for interval in body.diarization_intervals:
            if interval.end > duration + .25:
                fail("diarization_audio_range_exceeded", 422)
        normalized = body.model_dump(exclude={"lease_token", "fencing_token", "artifact_id"})
        normalized["provenance"] = {**normalized["provenance"], "native_english": True, "worker_id": str(worker["id"]), "model_version": worker["model_version"]}
        if body.artifact_id:
            artifact = await one(connection, "select id from inference_artifacts where id=%s and job_id=%s and run_id=%s and conversation_id=%s and kind='worker-output'", (body.artifact_id, job_id, job["run_id"], job["conversation_id"]))
            if not artifact:
                fail("artifact_scope_invalid", 422)
        accepted = await one(connection, "select finish_job(%s,%s,%s,%s,%s) as accepted", (job_id, str(worker["id"]), body.lease_token, body.fencing_token, Jsonb(normalized)))
        if not accepted["accepted"]:
            fail("late_or_fenced_result_rejected", 409)
        version_id = await publish_result(connection, job, normalized)
        await connection.execute("insert into usage_records(conversation_id,run_id,job_id,audio_seconds,attempt,processing_kind) values(%s,%s,%s,%s,%s,%s) on conflict(job_id,attempt,processing_kind) do nothing", (job["conversation_id"], job["run_id"], job_id, duration, max(job["attempt"], 1), job["kind"]))
        if body.provenance.get("additional_source_pass") and not body.provenance.get('speaker_only_retry'):
            await connection.execute("insert into usage_records(conversation_id,run_id,job_id,audio_seconds,attempt,processing_kind) values(%s,%s,%s,%s,%s,'source-pass') on conflict(job_id,attempt,processing_kind) do nothing", (job["conversation_id"], job["run_id"], job_id, duration, max(job["attempt"], 1)))
    return {"accepted": True, "version_id": version_id}


@router.post("/jobs/{job_id}/failure")
async def failure(job_id: UUID, body: s.WorkerFailure, request: Request, worker: dict = Depends(internal_worker)):
    async with request.app.state.db.connection() as connection:
        await worker_job(connection, worker, job_id)
        row = await one(connection, "select retry_job(%s,%s,%s,%s,%s,%s) as accepted", (job_id, str(worker["id"]), body.lease_token, body.fencing_token, body.error_code, body.uncertain))
    if not row["accepted"]:
        fail("job_lease_expired_or_fenced", 409)
    return {"accepted": True}
