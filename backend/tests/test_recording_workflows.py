from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.recorder import processing
from backend.recorder.audio import ownership_segments
from backend.recorder.processing import processing_window_seconds


def test_walkin_processing_keeps_thirty_second_window():
    assert processing_window_seconds("walkin-audio", 180) == 30


def test_sessions_process_live_instead_of_waiting_for_legacy_three_minute_window():
    assert processing_window_seconds("session-audio", 180) == 10
    assert processing_window_seconds("session-audio", 180, 15) == 15


def test_unknown_bucket_does_not_become_visitor_workflow():
    assert processing_window_seconds(None, 180) == 180


def test_live_audio_overlap_keeps_a_boundary_word_once_and_preserves_real_repetitions():
    words = [{"start": 8.4, "end": 8.8, "text": "very "},
             {"start": 8.9, "end": 9.3, "text": "very "},
             {"start": 9.4, "end": 9.8, "text": "quiet "},
             {"start": 9.8, "end": 10.2, "text": "fan "},
             {"start": 10.3, "end": 10.8, "text": "motor"}]
    first = ownership_segments([{"start": 8.4, "end": 10.2, "words": words[:4]}], 0, 0, 10, "window-one")
    # The next STT input contains two seconds of prior audio. Timestamps are
    # relative to that input, while ownership remains on the shared timeline.
    following = [{**word, "start": word["start"] - 8, "end": word["end"] - 8} for word in words]
    second = ownership_segments([{"start": .4, "end": 2.8, "words": following}], 8, 10, 20, "window-two")
    assert first[0]["text"] + second[0]["text"] == "very very quiet fan motor"
    assert second[0]["start"] == 10
    assert second[0]["words"][0]["text"] == "fan "


@pytest.mark.asyncio
async def test_live_windows_publish_during_capture_and_finalize_only_the_remainder(monkeypatch):
    """Five-second uploads trigger live text without waiting for a stop manifest."""
    conversation_id, run_id = uuid4(), uuid4()
    chunks, windows, scheduled = [], [], []

    async def many(_connection, sql, _params):
        if "from audio_chunks" in sql:
            return chunks
        if "from processing_runs" in sql:
            return [{"id": run_id, "kind": "production", "engine_id": "elevenlabs"}]
        raise AssertionError(sql)

    async def one(_connection, sql, params):
        if "select audio_bucket" in sql:
            return {"audio_bucket": "session-audio"}
        if "as covered" in sql:
            return {"covered": windows[-1]["end"] if windows else 0, "sequence": len(windows)}
        if "insert into transcription_windows" in sql:
            window = {"id": uuid4(), "start": params[3], "end": params[4], "final": params[7]}
            windows.append(window)
            return window
        if "select manifest_hash" in sql:
            return {"manifest_hash": "stopped-recording"}
        raise AssertionError(sql)

    async def schedule(_connection, _conversation, _run, kind, _dedupe, payload=None, window_id=None, priority=10):
        job_id = uuid4()
        scheduled.append({"id": job_id, "kind": kind, "payload": payload, "window_id": window_id})
        return job_id

    monkeypatch.setattr(processing, "many", many)
    monkeypatch.setattr(processing, "one", one)
    monkeypatch.setattr(processing, "schedule", schedule)
    settings = SimpleNamespace(window_seconds=180, live_window_seconds=10, context_seconds=2)

    def upload():
        chunks.append({"sample_start": len(chunks) * 5 * 16000, "sample_count": 5 * 16000,
                       "state": "validated", "sha256": f"chunk-{len(chunks)}"})

    upload()
    assert await processing.plan_windows(None, conversation_id, settings) == []
    upload()
    assert len(await processing.plan_windows(None, conversation_id, settings)) == 1
    assert scheduled[0]["payload"]["ownership_start"] == 0
    assert scheduled[0]["payload"]["ownership_end"] == 10 * 16000
    assert scheduled[0]["kind"] == "transcribe"
    assert all(job["kind"] != "finalise" for job in scheduled)

    upload()
    assert await processing.plan_windows(None, conversation_id, settings) == []
    upload()
    assert len(await processing.plan_windows(None, conversation_id, settings)) == 1
    assert scheduled[1]["payload"]["sample_start"] == 8 * 16000
    assert scheduled[1]["payload"]["ownership_start"] == 10 * 16000
    assert scheduled[1]["payload"]["ownership_end"] == 20 * 16000

    upload()
    assert await processing.plan_windows(None, conversation_id, settings) == []
    assert len(await processing.plan_windows(None, conversation_id, settings, final=True)) == 2
    assert scheduled[2]["payload"]["ownership_start"] == 20 * 16000
    assert scheduled[2]["payload"]["ownership_end"] == 25 * 16000
    assert windows[-1]["final"] is True
    assert scheduled[3]["kind"] == "finalise"
    assert scheduled[3]["payload"] == {"sample_start": 0, "sample_end": 25 * 16000, "final": True}
