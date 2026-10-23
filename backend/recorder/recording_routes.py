import hashlib
import json
from uuid import UUID
import logging
from fastapi import APIRouter, Depends, Request
from psycopg.types.json import Jsonb
from . import schemas as s
from .audio import validate_wav
from .db import one, many
from .errors import fail
from .processing import plan_windows
from .routes import audit
from .security import Workspace, workspace, conversation, owned_conversation

router = APIRouter(prefix="/api/v1/conversations")
log = logging.getLogger("recorder.api")


async def valid_lease(connection, context: Workspace, conversation_id: UUID, token: UUID):
    row = await one(connection, "select * from recording_leases where conversation_id=%s and user_id=%s and organisation_id=%s and token=%s and expires_at>now()", (conversation_id, context.user.id, context.id, token))
    if not row:
        fail("recording_lease_expired", 409, "Resume with this tab or explicitly take over the interrupted recording")
    return row


@router.post("/{conversation_id}/lease")
async def acquire(conversation_id: UUID, body: s.LeaseAcquire, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        conv = await owned_conversation(connection, context, conversation_id)
        if conv["capture_state"] == "deleted" or conv["manifest_hash"]:
            fail("recording_already_stopped", 409)
        if body.purpose=='capture' and conv['total_samples']>0:
            fail('recover_saved_audio_before_capture',409,'Recover and finish this interaction, then start a new conversation.')
        lease = await one(connection, "select * from acquire_recording_lease(%s,%s,%s,%s)", (conversation_id, context.user.id, body.tab_id, body.takeover))
        if not lease:
            fail("recording_already_active", 409)
        part = await one(connection, "select * from recording_parts where conversation_id=%s order by ordinal desc limit 1", (conversation_id,))
        if not part:
            part = await one(connection, "insert into recording_parts(conversation_id,ordinal,start_sample) values(%s,0,0) returning *", (conversation_id,))
        elif body.takeover and body.purpose=='capture' and part["end_sample"] is not None:
            part = await one(connection, "insert into recording_parts(conversation_id,ordinal,start_sample) values(%s,%s,%s) returning *", (conversation_id, part["ordinal"]+1, part["end_sample"]))
        await connection.execute("update conversations set capture_state=case when capture_state='stopped' then 'stopped' when %s='recover' then 'interrupted' else 'recording' end,started_at=coalesce(started_at,now()),updated_at=now() where id=%s", (body.purpose,conversation_id))
    return {"part_id": part["id"], "lease_token": lease["token"], "fence": lease["fence"], "expires_at": lease["expires_at"], "start_sample": part["start_sample"], "capture_state": "stopped" if conv["capture_state"] == "stopped" else "recording"}


@router.post("/{conversation_id}/lease/heartbeat")
async def heartbeat(conversation_id: UUID, body: s.LeaseHeartbeat, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        await owned_conversation(connection, context, conversation_id)
        row = await one(connection, "select heartbeat_recording_lease(%s,%s,%s,%s) as renewed", (conversation_id, context.user.id, body.tab_id, body.lease_token))
        if not row["renewed"]:
            fail("recording_lease_expired", 409)
    return {"renewed": True}


@router.post("/{conversation_id}/events")
async def capture_event(conversation_id: UUID, body: s.CaptureEvent, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        conv = await owned_conversation(connection, context, conversation_id)
        await valid_lease(connection, context, conversation_id, body.lease_token)
        if conv["capture_state"] == "stopped" and body.kind not in {"stop", "stopped", "interrupted"}:
            fail("recording_already_stopped", 409)
        part = await one(connection, "select * from recording_parts where id=%s and conversation_id=%s for update", (body.part_id, conversation_id))
        if not part:
            fail("recording_part_not_found", 404)
        if part["events"] and body.sample < part["events"][-1]["sample"]:
            fail("capture_event_out_of_order", 409)
        state = "paused" if body.kind in {"pause", "paused"} else "recording" if body.kind in {"started", "resume", "resumed"} else "stopped" if body.kind in {"stop", "stopped"} else "interrupted"
        if conv["capture_state"] == "stopped":
            state = "stopped"
        event = {"kind": body.kind, "sample": body.sample}
        if event not in part["events"]:
            if len(part["events"]) >= 10_000:
                fail("capture_event_limit", 413)
            await connection.execute("update recording_parts set events=events||%s::jsonb where id=%s", (Jsonb([event]), body.part_id))
        await connection.execute("update conversations set capture_state=%s,stopped_at=case when %s='stopped' then now() else stopped_at end,updated_at=now() where id=%s", (state, state, conversation_id))
    return {"capture_state": state}


def chunk_matches(row: dict, body: s.Chunk) -> bool:
    return all(row[key] == value for key, value in {"sample_start": body.start_sample, "sample_count": body.sample_count, "sample_rate": body.sample_rate, "bytes": body.bytes, "sha256": body.sha256, "mime": body.mime}.items())


@router.post("/{conversation_id}/chunks/authorize")
async def authorize(conversation_id: UUID, body: s.Chunk, request: Request, context: Workspace = Depends(workspace)):
    if body.conversation_id and body.conversation_id != conversation_id:
        fail("conversation_mismatch", 409)
    if body.bytes != 44 + body.sample_count * 2 or body.bytes > request.app.state.settings.upload_max_bytes:
        fail("invalid_chunk_size")
    if body.start_sample + body.sample_count > request.app.state.settings.recording_max_seconds * 16000:
        fail("recording_duration_limit", 413)
    async with request.app.state.db.connection() as connection:
        conv = await owned_conversation(connection, context, conversation_id)
        part = await one(connection, "select * from recording_parts where id=%s and conversation_id=%s", (body.part_id, conversation_id))
        if not part or body.start_sample < part["start_sample"]:
            fail("recording_part_not_found", 404)
        row = await one(connection, "select * from audio_chunks where conversation_id=%s and part_id=%s and sequence=%s for update", (conversation_id, body.part_id, body.sequence))
        if row and not chunk_matches(row, body):
            fail("chunk_content_conflict", 409)
        if row and row["state"] == "validated":
            return {"confirmed": True, "object_path": row["object_path"], "upload_url": None, "headers": {}}
        await valid_lease(connection, context, conversation_id, body.lease_token)
        if conv["manifest_hash"]:
            fail("recording_manifest_already_committed", 409)
        overlap = await one(connection, "select id from audio_chunks where conversation_id=%s and id<>coalesce(%s::uuid,'00000000-0000-0000-0000-000000000000') and sample_start<%s and sample_start+sample_count>%s limit 1", (conversation_id, row["id"] if row else None, body.start_sample+body.sample_count, body.start_sample))
        if overlap:
            fail("chunk_timeline_conflict", 409)
        customer = await one(connection, "select phone from customers where id=%s and organisation_id=%s", (conv["customer_id"], context.id))
        phone = ''.join(character for character in str(customer.get("phone") if customer else "") if character.isdigit())[-15:] or "no-phone"
        bucket = conv.get("audio_bucket") or "session-audio"
        path = f"{context.id}/{context.user.id}/{conversation_id}/{bucket}/{phone}/{body.part_id}/{body.sequence}.wav"
        if not row:
            row = await one(connection, "insert into audio_chunks(conversation_id,part_id,sequence,sample_start,sample_count,sample_rate,bytes,mime,sha256,object_path) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict(conversation_id,part_id,sequence) do update set upload_expires_at=now()+interval '15 minutes' returning *", (conversation_id, body.part_id, body.sequence, body.start_sample, body.sample_count, body.sample_rate, body.bytes, body.mime, body.sha256, path))
            if not chunk_matches(row, body):
                fail("chunk_content_conflict", 409)
        else:
            await connection.execute("update audio_chunks set upload_expires_at=now()+interval '15 minutes' where id=%s", (row["id"],))
        await connection.execute("update conversations set upload_state='uploading',updated_at=now() where id=%s", (conversation_id,))
    # Reservation commits first. Storage can fail independently and be retried safely.
    settings = request.app.state.settings
    if not settings.supabase_anon_key:
        fail("public_storage_key_not_configured", 503)
    return {"upload_url": f"{request.app.state.storage.base}/object/{request.app.state.storage.path(path)}",
            "method": "POST", "headers": {"Authorization": request.headers["authorization"], "apikey": settings.supabase_anon_key,
                                            "Content-Type": "audio/wav", "x-upsert": "false"}, "object_path": path}


@router.post("/{conversation_id}/chunks/confirm")
async def confirm(conversation_id: UUID, body: s.Chunk, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        await owned_conversation(connection, context, conversation_id)
        row = await one(connection, "select * from audio_chunks where conversation_id=%s and part_id=%s and sequence=%s", (conversation_id, body.part_id, body.sequence))
        if not row or not chunk_matches(row, body):
            fail("chunk_reservation_mismatch", 409)
        if row["state"] == "validated":
            return {"confirmed": True, "chunk_id": row["id"]}
        await valid_lease(connection, context, conversation_id, body.lease_token)
    data = await request.app.state.storage.read(row["object_path"], request.app.state.settings.upload_max_bytes)
    try:
        # The final WAV chunk may be shorter than the page's configured chunk duration.
        info = validate_wav(data, expected_samples=body.sample_count, max_samples=3 * 60 * 16000)
    except ValueError:
        fail("invalid_wav_content", 422)
    if len(data) != body.bytes or info.sha256 != body.sha256:
        fail("chunk_hash_or_size_conflict", 409)
    async with request.app.state.db.connection() as connection:
        await owned_conversation(connection, context, conversation_id)
        await valid_lease(connection, context, conversation_id, body.lease_token)
        await connection.execute("update audio_chunks set state='validated',confirmed_at=now(),validated_at=now() where id=%s", (row["id"],))
        await connection.execute("update conversations set total_samples=greatest(total_samples,%s),duration_seconds=greatest(duration_seconds,%s),updated_at=now() where id=%s", (body.start_sample+body.sample_count, (body.start_sample+body.sample_count)/16000, conversation_id))
        await plan_windows(connection, conversation_id, request.app.state.settings)
    log.info("chunk_validated conversation_id=%s part_id=%s sequence=%s bytes=%s", conversation_id, body.part_id, body.sequence, len(data))
    return {"confirmed": True, "chunk_id": row["id"], "sample_count": info.sample_count}


@router.post("/{conversation_id}/finalize")
async def finalize(conversation_id: UUID, body: s.Manifest, request: Request, context: Workspace = Depends(workspace)):
    canonical = {"part_id": str(body.part_id), "expected_chunks": body.expected_chunks, "total_samples": body.total_samples}
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    async with request.app.state.db.connection() as connection:
        conv = await owned_conversation(connection, context, conversation_id)
        if conv["manifest_hash"]:
            if conv["manifest_hash"] != digest:
                fail("manifest_conflict", 409)
            return {"saved": True, "manifest_hash": digest, "final_state": conv["final_state"]}
        await valid_lease(connection, context, conversation_id, body.lease_token)
        chunks = await many(connection, "select * from audio_chunks where conversation_id=%s order by sample_start", (conversation_id,))
        part_chunks = [chunk for chunk in chunks if chunk["part_id"] == body.part_id]
        missing = [sequence for sequence in range(body.expected_chunks) if not any(chunk["sequence"] == sequence and chunk["state"] == "validated" for chunk in part_chunks)]
        if missing:
            fail("manifest_missing_chunks", 409, missing_sequences=missing)
        if len(part_chunks) != body.expected_chunks:
            fail("manifest_chunk_count_conflict", 409)
        expected = 0
        for chunk in chunks:
            if chunk["state"] != "validated" or chunk["sample_start"] != expected:
                fail("manifest_audio_gap", 409, expected_sample=expected)
            expected += chunk["sample_count"]
        if expected != body.total_samples or expected > request.app.state.settings.recording_max_seconds * 16000:
            fail("manifest_sample_count_conflict", 409)
        await connection.execute("update recording_parts set end_sample=%s where id=%s and conversation_id=%s", (expected, body.part_id, conversation_id))
        await connection.execute("update conversations set manifest=%s,manifest_hash=%s,total_samples=%s,duration_seconds=%s,capture_state='stopped',upload_state='saved',stopped_at=coalesce(stopped_at,now()),final_state='queued',updated_at=now() where id=%s", (Jsonb(canonical), digest, expected, expected/16000, conversation_id))
        await connection.execute("update processing_runs set audio_manifest_hash=%s where conversation_id=%s", (digest, conversation_id))
        await connection.execute("delete from recording_leases where conversation_id=%s and user_id=%s and token=%s", (conversation_id, context.user.id, body.lease_token))
        if expected:
            # Playback is assembled on demand from the validated bucket chunks.
            # No background playback job is required for a recording to be playable.
            log.info("playback_ready_for_on_demand_stream conversation_id=%s chunks=%s samples=%s", conversation_id, len(chunks), expected)
            await plan_windows(connection, conversation_id, request.app.state.settings, final=True)
        else:
            await connection.execute("update conversations set final_state='complete',english_state='unavailable',speaker_state='unavailable',transcription_state='complete' where id=%s", (conversation_id,))
        await audit(connection, context, "recording.stopped", {"samples": expected, "chunks": len(chunks)}, conversation_id)
    return {"saved": True, "manifest_hash": digest, "final_state": "queued" if expected else "complete"}


@router.get("/{conversation_id}/recovery")
async def recovery(conversation_id: UUID, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        conv = await conversation(connection, context, conversation_id)
        chunks = await many(connection, "select part_id,sequence,sample_start,sample_count,state,sha256 from audio_chunks where conversation_id=%s order by sample_start", (conversation_id,))
        lease = await one(connection, "select tab_id,expires_at from recording_leases where conversation_id=%s", (conversation_id,))
    return {"conversation": conv, "chunks": chunks, "lease": lease}
