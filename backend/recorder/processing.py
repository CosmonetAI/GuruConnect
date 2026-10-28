"""Transactional planning/publication shared by the API and durable workers."""
import hashlib
from uuid import UUID
from psycopg.types.json import Jsonb
from .db import one, many
from .errors import fail
from .audio import ownership_segments


async def schedule(connection, conversation_id, run_id, kind: str, dedupe_key: str,
                   payload: dict | None = None, window_id=None, priority: int = 10) -> UUID:
    row = await one(connection, "select schedule_job(%s,%s,%s,%s,%s,%s,%s) as id",
                    (conversation_id, run_id, kind, dedupe_key, Jsonb(payload or {}), window_id, priority))
    return row["id"]


def contiguous_samples(chunks: list[dict]) -> int:
    expected = 0
    for chunk in sorted(chunks, key=lambda item: item["sample_start"]):
        if chunk["state"] != "validated" or chunk["sample_start"] != expected:
            break
        expected += chunk["sample_count"]
    return expected


def processing_window_seconds(audio_bucket: str | None, configured_seconds: int) -> int:
    """Keep visitor registration and customer Sessions processing windows distinct."""
    return 30 if audio_bucket == "walkin-audio" else configured_seconds


async def plan_windows(connection, conversation_id: UUID, settings, final: bool = False, run_id: UUID | None = None) -> list[UUID]:
    """Lock conversation before calling. Jobs own new audio; overlap only supplies context."""
    chunks = await many(connection, "select * from audio_chunks where conversation_id=%s order by sample_start", (conversation_id,))
    coverage = contiguous_samples(chunks)
    conversation = await one(connection, "select audio_bucket from conversations where id=%s", (conversation_id,))
    # Visitor registration is intentionally lightweight. Sessions use the
    # three-minute product window; the bucket keeps the workflows distinct
    # even though they share the durable recorder implementation.
    window_seconds = processing_window_seconds(conversation.get("audio_bucket") if conversation else None, settings.window_seconds)
    runs = await many(connection, "select * from processing_runs where conversation_id=%s and cancelled_at is null and (%s::uuid is null or id=%s) and (kind='production' or id=%s) order by created_at", (conversation_id, run_id, run_id, run_id))
    jobs = []
    for run in runs:
        previous = await one(connection, "select coalesce(max(sample_end),0) as covered,coalesce(max(sequence),-1)+1 as sequence from transcription_windows where run_id=%s", (run["id"],))
        start, sequence = previous["covered"], previous["sequence"]
        window_samples = window_seconds * 16000
        while coverage - start >= window_samples or (final and coverage > start):
            end = min(start + window_samples, coverage)
            context_start = max(0, start - settings.context_seconds * 16000)
            input_hash = hashlib.sha256("".join(chunk["sha256"] for chunk in chunks if chunk["sample_start"] < end and chunk["sample_start"] + chunk["sample_count"] > context_start).encode()).hexdigest()
            window = await one(connection, "insert into transcription_windows(run_id,conversation_id,sequence,sample_start,sample_end,context_start,input_hash,final_window,state) values(%s,%s,%s,%s,%s,%s,%s,%s,'queued') returning *", (run["id"], conversation_id, sequence, start, end, context_start, input_hash, final and end == coverage))
            payload = {"sample_start": context_start, "sample_end": end, "ownership_start": start, "ownership_end": end, "input_hash": input_hash}
            job_id = await schedule(connection, conversation_id, run["id"], "transcribe", f"window:{run['id']}:{sequence}", payload, window["id"], 5 if run["kind"] == "production" else 40)
            jobs.append(job_id)
            if run["engine_id"] == "faster-whisper":
                await schedule(connection, conversation_id, run["id"], "assemble", f"prepare:{job_id}", {**payload, "inference_job_id": str(job_id)}, window["id"], 4 if run["kind"] == "production" else 39)
            start, sequence = end, sequence + 1
        if final and coverage:
            conv = await one(connection, "select manifest_hash from conversations where id=%s", (conversation_id,))
            payload = {"sample_start": 0, "sample_end": coverage, "final": True}
            job_id = await schedule(connection, conversation_id, run["id"], "finalise", f"final:{run['id']}:{conv['manifest_hash']}", payload, priority=30 if run["kind"] == "production" else 45)
            jobs.append(job_id)
            if run["engine_id"] == "faster-whisper":
                await schedule(connection, conversation_id, run["id"], "assemble", f"prepare:{job_id}", {**payload, "inference_job_id": str(job_id)}, priority=29 if run["kind"] == "production" else 44)
    return jobs


async def publish_result(connection, job: dict, result: dict) -> UUID:
    """Call only after finish_job returned true in this same transaction.

    Result times are relative to job input. English timestamps are segment times;
    translated-word alignment is deliberately absent. Machine data stays immutable.
    """
    existing = await one(connection, "select id from transcript_versions where job_id=%s", (job["id"],))
    if existing:
        return existing["id"]
    run = await one(connection, "select * from processing_runs where id=%s for update", (job["run_id"],))
    conv = await one(connection, "select * from conversations where id=%s and deleted_at is null for update", (job["conversation_id"],))
    if not run or not conv or run["cancelled_at"]:
        fail("result_publication_cancelled", 409)
    final = job["kind"] == "finalise" or bool(job["payload"].get("final"))
    source, english = result.get("source_segments") or [], result.get("english_segments") or []
    english_state = result.get("english_state") or ("complete" if final and result.get("english_segments") is not None else "available" if result.get("english_segments") is not None else "pending")
    unresolved = bool((result.get("provenance") or {}).get("session_speakers_unresolved"))
    speaker_state = "review" if final and unresolved else "complete" if final and result.get("diarization_status") == "completed" else "failed" if result.get("diarization_status") == "failed" else "unavailable" if result.get("diarization_status") == "unavailable" else "provisional"
    completed_end = int(job["payload"].get("ownership_end") or job["payload"].get("sample_end") or conv["total_samples"])
    if job.get("window_id"):
        await connection.execute("update transcription_windows set state='complete' where id=%s", (job["window_id"],))
    windows = await many(connection, "select sample_start,sample_end,state from transcription_windows where run_id=%s order by sample_start", (run["id"],))
    coverage = 0
    for window in windows:
        if window["state"] != "complete" or window["sample_start"] != coverage:
            break
        coverage = window["sample_end"]
    if final:
        coverage = completed_end
    prior_edits=await one(connection,"select exists(select 1 from transcript_revisions where conversation_id=%s) as edited",(conv['id'],))
    if final and prior_edits['edited']:
        result['provenance']={**result.get('provenance',{}),'manual_review_required':True}
    version = await one(connection, "insert into transcript_versions(conversation_id,run_id,kind,coverage_samples,source_available,english_state,speaker_state,provenance,job_id,state) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id", (conv["id"], run["id"], "final" if final else "progressive", coverage, bool(source), english_state, speaker_state, Jsonb(result.get("provenance") or {}), job["id"], "complete" if final and english_state == "complete" else "available"))
    version_id = version["id"]
    offset = int(job["payload"].get("sample_start", 0)) / 16000
    own_start = int(job["payload"].get("ownership_start", 0)) / 16000
    own_end = int(job["payload"].get("ownership_end", completed_end)) / 16000
    turns: list[dict] = []
    if not final:
        # Rebuild all completed windows deterministically; out-of-order completion
        # must not erase an earlier result or announce a contiguous final transcript.
        earlier = await many(connection, "select t.* from transcript_turns t join (select distinct on (pj.window_id) v.id,pj.window_id from transcript_versions v join processing_jobs pj on pj.id=v.job_id where v.run_id=%s and v.kind='progressive' and pj.window_id is not null and pj.window_id is distinct from %s order by pj.window_id,v.created_at desc) latest on latest.id=t.version_id and latest.window_id=t.window_id order by t.start_seconds", (run["id"], job.get("window_id")))
        seen = set()
        for row in earlier:
            key = (row["window_id"], row["start_seconds"], row["end_seconds"], row["source_text"], row["english_text"])
            if key not in seen:
                seen.add(key)
                turns.append(row)
    speaker_job = job["payload"].get("source_job_id", job["id"]) if job["kind"] == "translate" else job["id"]
    scope = f"{run['id']}:{job.get('window_id') or speaker_job}"
    def owned(items):
        # Provider adapters may already scope labels; ownership supplies a stable
        # run/window scope to prevent accidental identity across independent runs.
        prepared = []
        for item in items:
            item = dict(item)
            if item.get("speaker") and str(item["speaker"]).startswith(scope + ":"):
                item["speaker"] = str(item["speaker"])[len(scope)+1:]
            prepared.append(item)
        return ownership_segments(prepared, offset, own_start, own_end, scope)
    native = bool((result.get("provenance") or {}).get("native_english")) or run["engine_id"] == "faster-whisper"
    source_owned = owned([dict(item, _source_index=index) for index, item in enumerate(source)])
    # Known-English source copies carry real source word timings; ownership must
    # trim their text exactly like the source. Actual translations have no words.
    english_owned = owned([dict(item, _source_index=index) for index, item in enumerate(english)])
    if native:
        # Native translation and an optional source pass have independent timing.
        # Preserve both without asserting translated-word or cross-pass alignment.
        rows = [(segment, None, segment["text"]) for segment in english_owned]
        rows += [(segment, segment["text"], None) for segment in source_owned]
    else:
        rows = []
        for segment in source_owned:
            matching = [item for item in english_owned if item["_source_index"] == segment["_source_index"]]
            rows.append((segment, segment["text"], matching[0]["text"] if len(matching) == 1 else None))
        if not source_owned:
            rows = [(segment, None, segment["text"]) for segment in english_owned]
    for segment, source_text, english_text in rows:
        turns.append({"start_seconds": segment["start"], "end_seconds": segment["end"], "source_text": source_text,
                      "english_text": english_text, "source_language": result.get("language"),
                      "speaker_ref": segment.get("speaker"), "window_id": job.get("window_id"), "words": segment.get("words") if source_text is not None else None,
                      "speaker_confidence": segment.get("speaker_confidence", "supported" if segment.get("speaker") and final else "provisional" if segment.get("speaker") else "uncertain"), "provenance": result.get("provenance") or {}})
    for ordinal, turn in enumerate(sorted(turns, key=lambda item: (item["start_seconds"], item["end_seconds"]))):
        row = await one(connection, "insert into transcript_turns(version_id,run_id,conversation_id,window_id,ordinal,start_seconds,end_seconds,source_text,english_text,source_language,speaker_ref,speaker_confidence,provenance) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id", (version_id, run["id"], conv["id"], turn.get("window_id"), ordinal, turn["start_seconds"], turn["end_seconds"], turn.get("source_text"), turn.get("english_text"), turn.get("source_language"), turn.get("speaker_ref"), turn.get("speaker_confidence", "uncertain"), Jsonb(turn.get("provenance") or {})))
        for word in turn.get("words") or []:
            await connection.execute("insert into transcript_words(turn_id,version_id,word,start_seconds,end_seconds) values(%s,%s,%s,%s,%s)", (row["id"], version_id, str(word.get("text") or word.get("word") or ""), float(word["start"]), float(word["end"])))
        prior=await one(connection,"select t.id from transcript_turns t join transcript_versions v on v.id=t.version_id where t.run_id=%s and t.version_id<>%s and t.window_id is not distinct from %s and t.start_seconds=%s and t.end_seconds=%s and ((%s::text is not null and t.source_text=%s) or (%s::text is null and t.source_text is null and t.english_text=%s)) order by v.created_at desc limit 1",(run['id'],version_id,turn.get('window_id'),turn['start_seconds'],turn['end_seconds'],turn.get('source_text'),turn.get('source_text'),turn.get('source_text'),turn.get('english_text')))
        if prior:
            await connection.execute("insert into transcript_revisions(conversation_id,version_id,turn_id,actor_id,kind,change,created_at) select conversation_id,%s,%s,actor_id,kind,change,created_at from transcript_revisions where turn_id=%s and kind='text'",(version_id,row['id'],prior['id']))
            if not turn.get('words') and turn.get('id'):
                await connection.execute("insert into transcript_words(turn_id,version_id,word,start_seconds,end_seconds) select %s,%s,word,start_seconds,end_seconds from transcript_words where turn_id=%s",(row['id'],version_id,turn['id']))
    await connection.execute("insert into speaker_assignments(conversation_id,version_id,speaker_ref,role,display_name,assigned_by,created_at,updated_at) select distinct on(a.speaker_ref) a.conversation_id,%s,a.speaker_ref,a.role,a.display_name,a.assigned_by,a.created_at,a.updated_at from speaker_assignments a join transcript_versions v on v.id=a.version_id where v.run_id=%s and a.version_id<>%s and exists(select 1 from transcript_turns t where t.version_id=%s and t.speaker_ref=a.speaker_ref) order by a.speaker_ref,a.updated_at desc on conflict(version_id,speaker_ref) do nothing",(version_id,run['id'],version_id,version_id))
    for interval in result.get("diarization_intervals") or []:
        await connection.execute("insert into diarization_intervals(version_id,conversation_id,speaker_ref,start_seconds,end_seconds,overlap) values(%s,%s,%s,%s,%s,%s)", (version_id, conv["id"], interval["speaker"], float(interval["start"]) + offset, float(interval["end"]) + offset, bool(interval.get("overlap", False))))
    next_state = "complete" if final and english_state == "complete" else "running"
    await connection.execute("update processing_runs set coverage_samples=greatest(coverage_samples,%s),state=case when state='complete' and %s<>'complete' then state else %s end where id=%s", (coverage, next_state, next_state, run["id"]))
    active = await one(connection, "select * from transcript_versions where id=%s", (conv["active_version_id"],)) if conv["active_version_id"] else None
    publish_active = run["kind"] == "production" and not (active and active["kind"] == "final" and active["english_state"] == "complete" and not final)
    if active and active["run_id"] != run["id"]:
        publish_active = False
    if final and english_state not in {"available", "complete"} and active and active["english_state"] in {"available", "complete"}:
        publish_active = False
    if publish_active:
        await connection.execute("update conversations set active_version_id=%s,transcription_state=%s,english_state=%s,speaker_state=%s,final_state=%s,updated_at=now() where id=%s", (version_id, "complete" if final else "available", english_state, speaker_state, "complete" if final and english_state == "complete" and speaker_state in {"complete", "review"} else "blocked" if final and english_state in {"pending", "blocked"} else "failed" if final else "pending", conv["id"]))
    elif run["kind"] == "production" and final and english_state not in {"available", "complete"}:
        await connection.execute("update conversations set final_state='blocked',updated_at=now() where id=%s", (conv["id"],))
    return version_id
