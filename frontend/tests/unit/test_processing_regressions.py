"""Regression probes for real publication, large accepted chunks and deletion."""

import hashlib
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from inference.engine import Engine
from recorder import processing
from recorder import worker as module
from recorder.audio import pcm_to_wav, validate_wav
from recorder.config import Settings
from recorder.local_store import LocalStore
from recorder.local_worker import LocalWorker
from recorder.worker import MainWorker


@pytest.mark.asyncio
async def test_maximum_hosted_chunk_is_processed_without_rejecting_saved_audio(
    monkeypatch,
):
    raw = pcm_to_wav(b"\0\0" * (180 * 16000))

    class DB:
        @asynccontextmanager
        async def connection(self):
            yield object()

    class Storage:
        async def read(self, *args):
            return raw

    async def one(*args):
        return {"id": "conversation"}

    async def many(*args):
        return [
            {
                "sample_start": 0,
                "sample_count": 180 * 16000,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "object_path": "fixture.wav",
            }
        ]

    monkeypatch.setattr(module, "one", one)
    monkeypatch.setattr(module, "many", many)
    worker = MainWorker(Settings(_env_file=None))
    worker.db = DB()
    worker.storage = Storage()
    audio, _ = await worker.audio_file(
        {
            "conversation_id": "conversation",
            "payload": {"sample_start": 0, "sample_end": 180 * 16000},
        }
    )
    try:
        assert (
            validate_wav(audio.read(), 180 * 16000, max_samples=180 * 16000).samples
            == 180 * 16000
        )
    finally:
        audio.close()


@pytest.mark.asyncio
async def test_known_english_publication_owns_identical_source_and_english_words(
    monkeypatch,
):
    turns = []

    class Connection:
        async def execute(self, *args):
            pass

    async def one(connection, sql, params=()):
        if sql.startswith("select id from transcript_versions where job_id"):
            return None
        if sql.startswith("select * from processing_runs"):
            return {
                "id": "run",
                "engine_id": "elevenlabs",
                "cancelled_at": None,
                "kind": "production",
            }
        if sql.startswith("select * from conversations"):
            return {"id": "conv", "total_samples": 960000, "active_version_id": None}
        if "select exists(select 1 from transcript_revisions" in sql:
            return {"edited": False}
        if sql.startswith("insert into transcript_versions"):
            return {"id": "v2"}
        if sql.startswith("insert into transcript_turns"):
            turns.append(params)
            return {"id": "turn"}
        if sql.startswith("select t.id from transcript_turns"):
            return None
        raise AssertionError(sql)

    async def many(*args):
        return []

    monkeypatch.setattr(processing, "one", one)
    monkeypatch.setattr(processing, "many", many)
    segment = {
        "start": 0.0,
        "end": 4.0,
        "text": "previous words new words",
        "speaker": "a",
        "words": [
            {"start": 0.0, "end": 1.0, "text": "previous "},
            {"start": 1.0, "end": 2.0, "text": "words "},
            {"start": 2.0, "end": 3.0, "text": "new "},
            {"start": 3.0, "end": 4.0, "text": "words"},
        ],
    }
    job = {
        "id": "j",
        "run_id": "run",
        "kind": "transcribe",
        "conversation_id": "conv",
        "window_id": "win2",
        "payload": {
            "sample_start": 28 * 16000,
            "sample_end": 60 * 16000,
            "ownership_start": 30 * 16000,
            "ownership_end": 60 * 16000,
        },
    }
    await processing.publish_result(
        Connection(),
        job,
        {
            "language": "en",
            "source_segments": [segment],
            "english_segments": [dict(segment)],
            "english_state": "available",
            "diarization_status": "completed",
            "provenance": {},
        },
    )
    assert turns[0][7] == turns[0][8] == "new words"


def test_bounded_speaker_retry_preserves_every_saved_source_word(tmp_path, monkeypatch):
    monkeypatch.setenv("INFERENCE_BLOCK_SECONDS", "30")
    engine = Engine.__new__(Engine)

    def block(path, job):
        return {
            **job["base_result"],
            "diarization_intervals": [],
            "diarization_status": "completed",
            "provenance": {},
        }

    engine._process_block = block
    path = tmp_path / "audio.wav"
    path.write_bytes(pcm_to_wav(b"\0\0" * (31 * 16000)))
    base = {
        "language": "en",
        "source_segments": [
            {
                "start": 29.0,
                "end": 31.0,
                "text": "first second",
                "words": [
                    {"start": 29.0, "end": 30.0, "text": "first "},
                    {"start": 30.0, "end": 31.0, "text": "second"},
                ],
            }
        ],
        "english_segments": [{"start": 29.0, "end": 31.0, "text": "first second"}],
    }
    result = engine.process(
        str(path),
        {"kind": "diarize", "job_id": "retry", "run_id": "run", "base_result": base},
    )
    words = [word for turn in result["source_segments"] for word in turn["words"]]
    assert words == base["source_segments"][0]["words"]


@pytest.mark.asyncio
async def test_local_delete_cancels_paid_fence_and_removes_audio_and_all_versions(
    tmp_path,
):
    store = LocalStore(tmp_path / "data")
    cid, run, part = str(uuid4()), str(uuid4()), str(uuid4())
    store.put(
        "conversations",
        cid,
        {
            "id": cid,
            "capture_state": "stopped",
            "customer_name_snapshot": "Private",
            "title": "Private title",
            "lease_token": "old-lease",
            "total_samples": 16000,
        },
    )
    store.put("runs", run, {"id": run, "conversation_id": cid})
    raw = pcm_to_wav(b"\0\0" * 16000)
    chunk = {
        "part_id": part,
        "sequence": 0,
        "start_sample": 0,
        "sample_count": 16000,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    store.authorize_chunk(cid, chunk)
    store.mark_chunk(cid, part, 0, confirmed=True)
    chunk_path = store.chunk_path(cid, part, 0)
    chunk_path.parent.mkdir(parents=True)
    chunk_path.write_bytes(raw)
    store.audio_path(cid).write_bytes(raw)
    store.replace_turns(
        cid,
        [{"source_text": "private speech", "english_text": "private speech"}],
        {"id": str(uuid4()), "run_id": run},
    )
    store.replace_turns(
        cid,
        [{"source_text": "new speech", "english_text": "new speech"}],
        {"id": str(uuid4()), "run_id": run},
    )
    job = store.enqueue_job(cid, "transcribe", {"run_id": run})
    lease = store.claim_job("old-worker")
    store.mark_job_accepted(job["id"], lease_token=lease["lease_token"])
    assert store.delete_conversation(cid) == {"deleted": True, "cleanup": "queued"}
    with pytest.raises(ValueError, match="local_job_lease_lost"):
        store.complete_job(job["id"], lease_token=lease["lease_token"])
    unrelated = tmp_path / "unrelated.wav"
    unrelated.write_bytes(raw)
    worker = LocalWorker(Settings(_env_file=None, local_data_dir=store.root), store)
    assert await worker.run_once()
    assert not store.audio_path(cid).parent.exists()
    assert not store.get_transcript(cid)["turns"] and not store.chunks(cid)
    assert not store.list("versions") and unrelated.exists()
    assert (
        store.get("conversations", cid)["customer_name_snapshot"] == "Deleted recording"
    )
    with pytest.raises(ValueError, match="conversation_deleted"):
        store.patch("conversations", cid, {"transcription_state": "complete"})
    # Tombstone prevents resurrection; repeated deletes remain safe.
    assert store.delete_conversation(cid)["deleted"]


@pytest.mark.asyncio
async def test_maximum_legacy_chunk_is_assembled_in_local_mode_without_provider_keys(
    tmp_path,
):
    store = LocalStore(tmp_path)
    cid, part, run = str(uuid4()), str(uuid4()), str(uuid4())
    samples = 180 * 16000
    raw = pcm_to_wav(b"\0\0" * samples)
    store.put(
        "conversations",
        cid,
        {
            "id": cid,
            "capture_state": "stopped",
            "total_samples": samples,
            "audio_only": True,
        },
    )
    chunk = {
        "part_id": part,
        "sequence": 0,
        "start_sample": 0,
        "sample_count": samples,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    store.authorize_chunk(cid, chunk)
    path = store.chunk_path(cid, part, 0)
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    store.mark_chunk(cid, part, 0, confirmed=True)
    store.enqueue_job(cid, "assemble", {"run_id": run, "total_samples": samples})
    worker = LocalWorker(Settings(_env_file=None, local_data_dir=store.root), store)
    assert await worker.run_once()
    assert store.audio_path(cid).read_bytes() == raw
