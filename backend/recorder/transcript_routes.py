import json
import hashlib
import logging
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from psycopg.types.json import Jsonb
from . import schemas as s
from .db import one, many
from .errors import fail
from .processing import plan_windows, schedule
from .audio import MAX_SESSION_SAMPLES, validate_wav
from .routes import audit, create_run
from .security import Workspace, workspace, conversation
from .media import mp3_chunks, wav_header

router = APIRouter(prefix="/api/v1/conversations")
log = logging.getLogger("recorder.api")


async def transcript_data(connection, context, conversation_id, version_id=None):
    conv = await conversation(connection, context, conversation_id)
    version = await one(connection, "select * from transcript_versions where id=%s and conversation_id=%s", (version_id or conv["active_version_id"], conversation_id))
    if not version:
        return {"version": None, "turns": [], "speakers": [], "revisions": []}
    turns = await many(connection, "select * from transcript_turns where version_id=%s order by ordinal", (version["id"],))
    assignments = await many(connection, "select * from speaker_assignments where version_id=%s", (version["id"],))
    revisions = await many(connection, "select * from transcript_revisions where version_id=%s order by created_at", (version["id"],))
    # Corrections are presentation overlays; immutable model output stays recoverable.
    by_id = {str(turn["id"]): turn for turn in turns}
    for revision in revisions:
        turn = by_id.get(str(revision["turn_id"]))
        if turn and revision["kind"] == "text":
            turn.setdefault("original_source_text", turn["source_text"])
            turn.setdefault("original_english_text", turn["english_text"])
            for field in ("source_text", "english_text"):
                if field in revision["change"] and revision["change"][field] is not None:
                    turn[field] = revision["change"][field]
            turn["manually_corrected"] = True
        if revision["kind"] in {"merge", "split"}:
            change = revision["change"]
            selected_ids = set(change.get("turn_ids") or [])
            for entry in turns:
                selected = entry["speaker_ref"] in change.get("speaker_refs", []) if revision["kind"] == "merge" else str(entry["id"]) in selected_ids
                if selected:
                    entry.setdefault("original_speaker_ref", entry["speaker_ref"])
                    entry["speaker_ref"] = change["target_ref"]
                    entry["speaker_group_manually_corrected"] = True
    return {"version": version, "turns": turns, "speakers": assignments, "revisions": revisions}


@router.get("/{conversation_id}/transcript")
async def transcript(conversation_id: UUID, request: Request, context: Workspace = Depends(workspace), version_id: UUID | None = None):
    async with request.app.state.db.connection() as connection:
        return await transcript_data(connection, context, conversation_id, version_id)


@router.post("/{conversation_id}/speakers")
async def assign_speaker(conversation_id: UUID, body: s.SpeakerAssignment, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        await conversation(connection, context, conversation_id, lock=True)
        data = await transcript_data(connection, context, conversation_id, body.version_id)
        if not any(turn["speaker_ref"] == body.speaker_ref for turn in data["turns"]):
            fail("speaker_not_found", 404)
        row = await one(connection, "insert into speaker_assignments(conversation_id,version_id,speaker_ref,role,display_name,assigned_by) values(%s,%s,%s,%s,%s,%s) on conflict(version_id,speaker_ref) do update set role=excluded.role,display_name=excluded.display_name,assigned_by=excluded.assigned_by,updated_at=now() returning *", (conversation_id, body.version_id, body.speaker_ref, body.role, body.name, context.user.id))
        await connection.execute("insert into transcript_revisions(conversation_id,version_id,actor_id,kind,change) values(%s,%s,%s,'speaker',%s)", (conversation_id, body.version_id, context.user.id, Jsonb({"speaker_ref": body.speaker_ref, "role": body.role, "name": body.name})))
        await audit(connection, context, "speaker.assigned", {"version_id": str(body.version_id)}, conversation_id)
        return row


@router.post("/{conversation_id}/speaker-groups")
async def correct_speaker_groups(conversation_id: UUID, body: s.SpeakerGroups, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        await conversation(connection, context, conversation_id, lock=True)
        data = await transcript_data(connection, context, conversation_id, body.version_id)
        if not data["version"]:
            fail("version_not_found", 404)
        present = {turn["speaker_ref"] for turn in data["turns"]}
        if not set(body.speaker_refs) <= present:
            fail("speaker_group_not_found", 404)
        selected_ids = set(body.turn_ids)
        selected = [turn for turn in data["turns"] if turn["id"] in selected_ids]
        if selected_ids and (len(selected) != len(selected_ids) or any(turn["speaker_ref"] not in body.speaker_refs for turn in selected)):
            fail("speaker_turn_scope_invalid", 422)
        if body.operation == "split" and len(selected) >= sum(turn["speaker_ref"] == body.speaker_refs[0] for turn in data["turns"]):
            fail("split_must_leave_original_group", 422)
        target = (body.target_ref or body.speaker_refs[0]) if body.operation == "merge" else f"manual:{body.version_id}:{uuid4()}"
        change = {"speaker_refs": body.speaker_refs, "turn_ids": [str(turn_id) for turn_id in body.turn_ids], "target_ref": target}
        row = await one(connection, "insert into transcript_revisions(conversation_id,version_id,actor_id,kind,change) values(%s,%s,%s,%s,%s) returning *", (conversation_id, body.version_id, context.user.id, body.operation, Jsonb(change)))
        await audit(connection, context, "speaker.groups_corrected", {"version_id": str(body.version_id), "operation": body.operation, "revision_id": str(row["id"])}, conversation_id)
    return {"revision_id": row["id"], "target_ref": target, "operation": body.operation}


@router.post("/{conversation_id}/revisions", status_code=201)
async def revise(conversation_id: UUID, body: s.Revision, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        await conversation(connection, context, conversation_id, lock=True)
        turn = await one(connection, "select id from transcript_turns where id=%s and version_id=%s and conversation_id=%s", (body.turn_id, body.version_id, conversation_id))
        if not turn:
            fail("turn_not_found", 404)
        return await one(connection, "insert into transcript_revisions(conversation_id,version_id,turn_id,actor_id,kind,change) values(%s,%s,%s,%s,'text',%s) returning *", (conversation_id, body.version_id, body.turn_id, context.user.id, Jsonb(body.model_dump(exclude={"version_id", "turn_id"}, exclude_none=True))))


@router.get("/{conversation_id}/playback")
async def playback(conversation_id: UUID, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        conv = await conversation(connection, context, conversation_id)
        assembly = await one(connection, "select id,state,error_code,attempt,updated_at from processing_jobs where conversation_id=%s and kind='assemble' and payload->>'inference_job_id' is null order by created_at desc limit 1", (conversation_id,))
    if not conv["playback_object_path"]:
        log.warning("playback_pending conversation_id=%s job_id=%s job_state=%s error_code=%s",
                    conversation_id, assembly["id"] if assembly else None,
                    assembly["state"] if assembly else "missing",
                    assembly["error_code"] if assembly else None)
        fail("playback_assembly_pending", 409, job_state=assembly["state"] if assembly else "missing",
             job_id=str(assembly["id"]) if assembly else None,
             job_error_code=assembly["error_code"] if assembly else None)
    return {"url": await request.app.state.storage.signed_read(conv["playback_object_path"]), "expires_in": request.app.state.settings.signed_url_seconds}


def _wav_header(samples: int) -> bytes:
    """Return a PCM WAV header whose data section can be streamed progressively."""
    return wav_header(samples)


@router.get("/{conversation_id}/playback/stream")
async def playback_stream(conversation_id: UUID, request: Request, start_sample: int = Query(default=0, ge=0), context: Workspace = Depends(workspace)):
    """Stream one continuous WAV directly from the validated bucket chunks.

    This is the playback fallback for deployments without the background assembly
    worker. The first validated chunk is sent as soon as it is read; subsequent
    chunks are yielded as contiguous PCM so the browser can keep playing.
    """
    async with request.app.state.db.connection() as connection:
        conv = await conversation(connection, context, conversation_id)
        chunks = await many(connection, "select object_path,sample_start,sample_count,bytes,sha256,state from audio_chunks where conversation_id=%s order by sample_start", (conversation_id,))
    total_samples = int(conv.get("total_samples") or 0)
    if not chunks or total_samples <= 0 or total_samples > MAX_SESSION_SAMPLES:
        fail("playback_unavailable", 409, "Saved audio is not ready for playback")
    expected = 0
    for chunk in chunks:
        if chunk["state"] != "validated" or int(chunk["sample_start"]) != expected:
            fail("playback_chunks_incomplete", 409, "Some saved audio is still being verified")
        expected += int(chunk["sample_count"])
    if expected != total_samples or start_sample >= total_samples:
        fail("playback_chunks_incomplete", 409, "Saved audio is incomplete")
    selected = [chunk for chunk in chunks if int(chunk["sample_start"]) + int(chunk["sample_count"]) > start_sample]
    samples_to_stream = total_samples - start_sample

    async def verified_pcm(chunk):
        data = await request.app.state.storage.read(chunk["object_path"], int(chunk["bytes"]))
        try:
            info = validate_wav(data, expected_samples=int(chunk["sample_count"]), max_samples=3 * 60 * 16000)
        except ValueError as exc:
            log.error("playback_chunk_invalid conversation_id=%s sample_start=%s exception_type=%s", conversation_id, chunk["sample_start"], type(exc).__name__)
            fail("playback_chunk_invalid", 409)
        if hashlib.sha256(data).hexdigest() != chunk["sha256"]:
            log.error("playback_chunk_checksum_mismatch conversation_id=%s sample_start=%s", conversation_id, chunk["sample_start"])
            fail("playback_chunk_checksum_mismatch", 409)
        left = max(0, start_sample - int(chunk["sample_start"]))
        return info.pcm[left * 2:]

    # Verify the first chunk before committing audio headers. Storage failures
    # can then return actionable JSON instead of a broken HTTP 200 audio file.
    first_pcm = await verified_pcm(selected[0])

    async def body():
        yield _wav_header(samples_to_stream)
        yield first_pcm
        for chunk in selected[1:]:
            try:
                yield await verified_pcm(chunk)
            except HTTPException:
                # HTTP status cannot change after headers. Abort the stream so
                # the browser detects incomplete audio and can request it again.
                log.warning("playback_stream_interrupted conversation_id=%s sample_start=%s", conversation_id, chunk["sample_start"])
                raise RuntimeError("playback_stream_interrupted") from None

    log.info("playback_stream_started conversation_id=%s start_sample=%s samples=%s chunks=%s", conversation_id, start_sample, samples_to_stream, len(selected))
    return StreamingResponse(body(), media_type="audio/wav", headers={
        "Content-Length": str(44 + samples_to_stream * 2),
        "X-Playback-Mode": "progressive-bucket",
        "X-Playback-Start-Sample": str(start_sample),
        "X-Playback-Sample-Rate": "16000",
    })


@router.get("/{conversation_id}/playback/download")
async def playback_download(conversation_id: UUID, request: Request, context: Workspace = Depends(workspace)):
    """Encode the validated chunk sequence as one downloadable MP3."""
    async with request.app.state.db.connection() as connection:
        conv = await conversation(connection, context, conversation_id)
        chunks = await many(connection, "select object_path,sample_start,sample_count,bytes,sha256,state from audio_chunks where conversation_id=%s order by sample_start", (conversation_id,))
    total_samples = int(conv.get("total_samples") or 0)
    if not chunks or total_samples <= 0 or total_samples > MAX_SESSION_SAMPLES:
        fail("playback_unavailable", 409, "Saved audio is not ready for download")
    expected = 0
    for chunk in chunks:
        if chunk["state"] != "validated" or int(chunk["sample_start"]) != expected:
            fail("playback_chunks_incomplete", 409, "Some saved audio is still being verified")
        expected += int(chunk["sample_count"])
    if expected != total_samples:
        fail("playback_chunks_incomplete", 409, "Saved audio is incomplete")

    async def pcm():
        for chunk in chunks:
            data = await request.app.state.storage.read(chunk["object_path"], int(chunk["bytes"]))
            info = validate_wav(data, expected_samples=int(chunk["sample_count"]), max_samples=3 * 60 * 16000)
            if hashlib.sha256(data).hexdigest() != chunk["sha256"]:
                raise RuntimeError("playback_chunk_checksum_mismatch")
            yield info.pcm

    return StreamingResponse(mp3_chunks(pcm(), total_samples), media_type="audio/mpeg", headers={
        "Content-Disposition": f'attachment; filename="recording-{conversation_id}.mp3"',
        "X-Playback-Mode": "mp3-encoded-chunks",
    })


def srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, rest = divmod(milliseconds, 3600000)
    minutes, rest = divmod(rest, 60000)
    whole, fraction = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{whole:02},{fraction:03}"


@router.get("/{conversation_id}/export")
async def export(conversation_id: UUID, request: Request, context: Workspace = Depends(workspace), format: str = Query(default="txt", pattern="^(txt|srt|json)$"), version_id: UUID | None = None):
    async with request.app.state.db.connection() as connection:
        data = await transcript_data(connection, context, conversation_id, version_id)
        await audit(connection, context, "transcript.exported", {"format": format}, conversation_id)
    if not data["version"]:
        fail("transcript_not_available", 409)
    if format == "json":
        content = json.dumps(data, default=str, ensure_ascii=False, indent=2)
        mime = "application/json"
    else:
        names = {item["speaker_ref"]: item["display_name"] for item in data["speakers"]}
        lines = []
        for turn in data["turns"]:
            # A source-language fallback is explicitly labelled, never exported as English.
            text = turn["english_text"]
            if text is None:
                text = f"[Original; English {data['version']['english_state']}] {turn['source_text'] or ''}"
            speaker = names.get(turn["speaker_ref"], turn["speaker_ref"] or "Speaker uncertain")
            if format == "srt":
                lines.append(f"{len(lines)+1}\n{srt_time(turn['start_seconds'])} --> {srt_time(turn['end_seconds'])}\n{speaker}: {text}\n")
            else:
                lines.append(f"[{srt_time(turn['start_seconds'])}] {speaker}: {text}")
        content = "\n".join(lines)
        mime = "application/x-subrip" if format == "srt" else "text/plain"
    return Response(content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="conversation-{conversation_id}.{format}"', "Cache-Control": "no-store"})


@router.get("/{conversation_id}/runs")
async def runs(conversation_id: UUID, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        await conversation(connection, context, conversation_id)
        items = await many(connection, "select r.*,coalesce((select jsonb_agg(v order by v.created_at desc) from transcript_versions v where v.run_id=r.id),'[]') as versions,coalesce((select sum(u.audio_seconds) from usage_records u where u.run_id=r.id),0) as processed_audio_seconds,coalesce((select sum(u.translation_characters) from usage_records u where u.run_id=r.id),0) as translation_characters,coalesce((select sum(u.input_tokens) from usage_records u where u.run_id=r.id),0) as translation_input_tokens,coalesce((select sum(u.output_tokens) from usage_records u where u.run_id=r.id),0) as translation_output_tokens from processing_runs r where conversation_id=%s order by created_at", (conversation_id,))
    return {"items": items, "next_cursor": None}


@router.get("/{conversation_id}/comparisons")
async def comparisons(conversation_id: UUID, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        await conversation(connection, context, conversation_id)
        items = await many(connection, "select * from comparisons where conversation_id=%s order by created_at desc", (conversation_id,))
    return {"items": items, "next_cursor": None}


@router.patch("/{conversation_id}/comparisons/{comparison_id}")
async def review_comparison(conversation_id: UUID, comparison_id: UUID, body: s.ComparisonReview, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        await conversation(connection, context, conversation_id, lock=True)
        row = await one(connection, "update comparisons set review_notes=%s where id=%s and conversation_id=%s returning *", (body.review_notes, comparison_id, conversation_id))
        if not row:
            fail("comparison_not_found", 404)
        await audit(connection, context, "comparison.reviewed", {"comparison_id": str(comparison_id)}, conversation_id)
    return row


@router.post("/{conversation_id}/runs", status_code=201)
async def replay(conversation_id: UUID, body: s.RunCreate, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        conv = await conversation(connection, context, conversation_id, lock=True)
        policy = await one(connection, "select * from workspace_engine_settings where organisation_id=%s", (context.id,))
        if not context.admin and (not policy["comparison_allowed"] or body.kind == "replay"):
            fail("processing_action_not_allowed", 403)
        if conv["upload_state"] != "saved" or not conv["manifest_hash"]:
            fail("recording_must_be_saved", 409)
        run = await create_run(connection, context, conv, body.engine_id, body.kind, body.idempotency_key, settings=request.app.state.settings)
        await plan_windows(connection, conversation_id, request.app.state.settings, final=True, run_id=run["id"])
        if body.kind == "comparison":
            production = await one(connection, "select id from processing_runs where conversation_id=%s and kind='production' order by created_at limit 1", (conversation_id,))
            if production and production["id"] != run["id"]:
                await connection.execute("insert into comparisons(conversation_id,left_run_id,right_run_id,audio_manifest_hash,requested_by,idempotency_key) values(%s,%s,%s,%s,%s,%s) on conflict(conversation_id,idempotency_key) do nothing", (conversation_id, production["id"], run["id"], conv["manifest_hash"], context.user.id, body.idempotency_key))
        await audit(connection, context, "processing.run_requested", {"run_id": str(run["id"]), "kind": body.kind, "estimated_additional_audio_seconds": conv["duration_seconds"] * 2}, conversation_id)
    return {**run, "estimated_additional_audio_seconds": conv["duration_seconds"] * 2}


@router.post("/{conversation_id}/versions/{version_id}/select")
async def select_version(conversation_id: UUID, version_id: UUID, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        await conversation(connection, context, conversation_id, lock=True)
        version = await one(connection, "select * from transcript_versions where id=%s and conversation_id=%s", (version_id, conversation_id))
        if not version or version["english_state"] not in {"available", "complete"}:
            fail("completed_english_version_required", 409)
        run=await one(connection,"select kind from processing_runs where id=%s",(version['run_id'],))
        if run['kind']!='production' and not context.admin:
            policy=await one(connection,"select comparison_allowed from workspace_engine_settings where organisation_id=%s",(context.id,))
            if not policy['comparison_allowed']:fail('comparison_access_required',403)
        await connection.execute("update conversations set active_version_id=%s,english_state=%s,speaker_state=%s,updated_at=now() where id=%s", (version_id, version["english_state"], version["speaker_state"], conversation_id))
        await audit(connection, context, "transcript.version_selected", {"version_id": str(version_id)}, conversation_id)
    return {"active_version_id": version_id}


@router.post("/{conversation_id}/retry")
async def retry(conversation_id: UUID, body: s.Retry, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        await conversation(connection, context, conversation_id, lock=True)
        kinds = {"all": ["validate", "assemble", "transcribe", "translate", "diarize", "finalise"], "transcription": ["transcribe"], "translation": ["translate"], "diarization": ["diarize"], "finalisation": ["finalise", "assemble"]}[body.stage]
        jobs = await many(connection, "select * from processing_jobs where conversation_id=%s and kind=any(%s) and state in ('failed','blocked','dead','uncertain')", (conversation_id, kinds))
        resumed = []
        uncertain = []
        for job in jobs:
            if job["state"] == "uncertain":
                uncertain.append(job["id"])
                continue
            if job["kind"] == "translate":
                # The explicit retry binds the selected translator and records
                # provenance without changing the original ASR run snapshot.
                row = await one(connection, "select resume_translation_job(%s,%s) as resumed", (job["id"], context.user.id))
            else:
                row = await one(connection, "select resume_job(%s,%s,false) as resumed", (job["id"], context.user.id))
            if row["resumed"]:
                resumed.append(job["id"])
        if body.stage == "diarization" and not jobs:
            previous = await one(connection, "select j.* from processing_jobs j join processing_runs r on r.id=j.run_id where j.conversation_id=%s and j.state='succeeded' and r.engine_id='faster-whisper' and j.result->>'diarization_status'='failed' order by j.completed_at desc limit 1", (conversation_id,))
            if previous:
                payload={**previous["payload"],"source_job_id":str(previous["id"]),"final":previous["kind"]=='finalise' or bool(previous["payload"].get("final"))}
                job_id=await schedule(connection,conversation_id,previous["run_id"],"diarize",f"speaker-retry:{previous['id']}",payload,previous["window_id"],priority=15)
                await schedule(connection,conversation_id,previous["run_id"],"assemble",f"prepare:{job_id}",{**payload,"inference_job_id":str(job_id)},previous["window_id"],priority=14)
                resumed.append(job_id)
        await audit(connection, context, "processing.retry_requested", {"stage": body.stage, "resumed": len(resumed)}, conversation_id)
    return {"resumed_job_ids": resumed, "uncertain_job_ids": uncertain, "message": "Uncertain provider submissions require operator reconciliation before another paid attempt" if uncertain else "Eligible jobs resumed"}


@router.post("/{conversation_id}/cancel")
async def cancel(conversation_id: UUID, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        await conversation(connection, context, conversation_id, lock=True)
        await connection.execute("update processing_jobs set state='cancelled',cancelled_at=now(),finish_in_flight=false,updated_at=now() where conversation_id=%s and state not in ('succeeded','cancelled')", (conversation_id,))
        await connection.execute("update processing_runs set state='cancelled',cancelled_at=now() where conversation_id=%s and state<>'complete'", (conversation_id,))
        await audit(connection, context, "processing.cancelled", conversation_id=conversation_id)
    return {"cancelled": True, "audio_preserved": True}


@router.delete("/{conversation_id}")
async def delete(conversation_id: UUID, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        conv = await conversation(connection, context, conversation_id, lock=True)
        chunks = await many(connection, "select object_path from audio_chunks where conversation_id=%s union select object_path from inference_artifacts where conversation_id=%s", (conversation_id, conversation_id))
        paths = [chunk["object_path"] for chunk in chunks]
        if conv["playback_object_path"]:
            paths.append(conv["playback_object_path"])
        await connection.execute("update conversations set deleted_at=now(),capture_state='deleted',upload_state='pending',active_version_id=null,customer_name_snapshot='Deleted',salesperson_name_snapshot='',title='',location='',updated_at=now() where id=%s", (conversation_id,))
        await connection.execute("delete from recording_leases where conversation_id=%s", (conversation_id,))
        await connection.execute("update processing_jobs set state='cancelled',cancelled_at=now(),finish_in_flight=false where conversation_id=%s and state<>'succeeded'", (conversation_id,))
        await connection.execute("update processing_runs set state='cancelled',cancelled_at=now() where conversation_id=%s", (conversation_id,))
        for table in ("transcript_revisions", "speaker_assignments", "diarization_intervals", "translations"):
            await connection.execute(f"delete from {table} where conversation_id=%s", (conversation_id,))
        await connection.execute("delete from transcript_words where version_id in (select id from transcript_versions where conversation_id=%s)", (conversation_id,))
        await connection.execute("delete from transcript_turns where conversation_id=%s", (conversation_id,))
        await connection.execute("delete from transcript_versions where conversation_id=%s", (conversation_id,))
        await connection.execute("update processing_jobs set result=null where conversation_id=%s", (conversation_id,))
        await schedule(connection, conversation_id, None, "delete", "delete", {"object_paths": paths}, priority=0)
        await audit(connection, context, "conversation.deletion_requested", conversation_id=conversation_id)
    return {"deleted": True, "cleanup": "queued"}
