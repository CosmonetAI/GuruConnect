"""Isolated localhost worker regressions: no external requests or paid calls."""

from pathlib import Path
from uuid import uuid4
import hashlib

import pytest

from recorder.audio import pcm_to_wav
from recorder.config import Settings
from recorder.local_store import LocalStore
from recorder.local_worker import LocalWorker
from recorder.openai_translator import OPENAI_TRANSLATION_MODEL, TranslationResult
from recorder.providers import ProviderError


def recording(tmp_path: Path):
    store = LocalStore(tmp_path)
    cid, run_id, part_id = [str(uuid4()) for _ in range(3)]
    store.put(
        "conversations",
        cid,
        {
            "id": cid,
            "total_samples": 16000,
            "capture_state": "stopped",
            "upload_state": "saved",
            "language_hint": "auto",
            "transcription_state": "queued",
            "english_state": "pending",
            "speaker_state": "pending",
            "final_state": "queued",
        },
    )
    store.put(
        "runs",
        run_id,
        {
            "id": run_id,
            "conversation_id": cid,
            "kind": "production",
            "engine_id": "elevenlabs",
            "state": "queued",
        },
    )
    data = pcm_to_wav(b"\x00\x00" * 16000)
    chunk = {
        "conversation_id": cid,
        "part_id": part_id,
        "sequence": 0,
        "start_sample": 0,
        "sample_count": 16000,
        "sample_rate": 16000,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "mime": "audio/wav",
        "lease_token": str(uuid4()),
    }
    store.authorize_chunk(cid, chunk)
    path = store.chunk_path(cid, part_id, 0)
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    store.mark_chunk(cid, part_id, 0, confirmed=True)
    store.enqueue_job(cid, "assemble", {"run_id": run_id, "total_samples": 16000})
    worker = LocalWorker(
        Settings(_env_file=None, local_data_dir=tmp_path, worker_lease_seconds=30),
        store,
    )
    return store, worker, cid, run_id


def connected(store, engine):
    store.configure_connection(
        engine,
        {"api_key": "synthetic-key"},
        {"model": OPENAI_TRANSLATION_MODEL} if engine == "openai-translator" else {},
        str(uuid4()),
        None,
    )
    store.patch("connections", engine, {"state": "connected"})


def result(language="hi", text="नमस्ते 42"):
    return {
        "language": language,
        "language_probability": 0.999,
        "source_segments": [
            {
                "start": 0.1,
                "end": 0.7,
                "text": text,
                "speaker": "speaker_0",
                "words": [{"text": text, "start": 0.1, "end": 0.7}],
            }
        ],
        "provenance": {"engine": "elevenlabs", "model": "scribe_v2"},
        "raw": {"text": text},
    }


@pytest.mark.asyncio
async def test_assembly_saves_playback_without_provider_credentials(tmp_path):
    store, worker, cid, _ = recording(tmp_path)
    assert await worker.run_once()
    assert (
        store.audio_path(cid).read_bytes()
        == store.chunk_path(cid, store.chunks(cid)[0]["part_id"], 0).read_bytes()
    )
    assert store.get("conversations", cid)["playback_state"] == "complete"
    assert await worker.run_once()
    job = next(job for job in store.list("jobs") if job["kind"] == "transcribe")
    assert job["state"] == "blocked" and job["error_code"] == "engine_unconfigured"
    assert job["submission_state"] == "not_submitted"
    assert store.get_turns(cid) == []
    assert store.get("conversations", cid)["upload_state"] == "saved"
    assert not await worker.run_once()


@pytest.mark.asyncio
async def test_full_pipeline_preserves_original_words_speakers_and_numeric_english(
    tmp_path, monkeypatch
):
    store, worker, cid, run_id = recording(tmp_path)
    connected(store, "elevenlabs")
    connected(store, "openai-translator")
    calls = []

    class Speech:
        def __init__(self, key):
            assert key == "synthetic-key"

        async def process(self, audio, language_hint, scope):
            assert audio.read(4) == b"RIFF"
            calls.append("speech")
            return result()

    class Translate:
        def __init__(self, key, usage_callback=None, model=OPENAI_TRANSLATION_MODEL):
            self.usage_callback = usage_callback

        async def translate(self, text, language):
            assert text == "नमस्ते 42" and language == "hi"
            calls.append("translate")
            usage = TranslationResult(
                "Hello 42", 20, 5, OPENAI_TRANSLATION_MODEL, "synthetic-request"
            )
            await self.usage_callback(usage)
            return usage

    monkeypatch.setattr("recorder.local_worker.ElevenLabsAdapter", Speech)
    monkeypatch.setattr("recorder.local_worker.OpenAITranslator", Translate)
    assert await worker.run_once()
    assert await worker.run_once()
    assert await worker.run_once()
    assert calls == ["speech", "translate"]
    transcript = store.get_transcript(cid)
    turn = transcript["turns"][0]
    assert turn["source_text"] == "नमस्ते 42" and turn["english_text"] == "Hello 42"
    assert turn["words"] == [{"text": "नमस्ते 42", "start": 0.1, "end": 0.7}]
    assert turn["speaker_ref"] == f"{cid}:speaker_0"
    assert transcript["version"]["provenance"]["translated_word_alignment"] is None
    assert store.get("conversations", cid)["final_state"] == "complete"
    assert store.get("runs", run_id)["translation_input_tokens"] == 20
    assert store.get("runs", run_id)["translation_output_tokens"] == 5
    assert not await worker.run_once()


@pytest.mark.asyncio
async def test_english_audio_does_not_require_or_call_translation(
    tmp_path, monkeypatch
):
    store, worker, cid, _ = recording(tmp_path)
    connected(store, "elevenlabs")

    class Speech:
        def __init__(self, key):
            pass

        async def process(self, *args):
            return result("en", "Order 42")

    monkeypatch.setattr("recorder.local_worker.ElevenLabsAdapter", Speech)
    assert await worker.run_once()
    assert await worker.run_once()
    assert store.get_turns(cid)[0]["english_text"] == "Order 42"
    assert store.get("conversations", cid)["english_state"] == "complete"
    assert not any(job["kind"] == "translate" for job in store.list("jobs"))


@pytest.mark.asyncio
async def test_missing_translator_preserves_source_and_retries_translation_without_new_speech(
    tmp_path, monkeypatch
):
    store, worker, cid, _ = recording(tmp_path)
    connected(store, "elevenlabs")
    speech_calls = []

    class Speech:
        def __init__(self, key):
            pass

        async def process(self, *args):
            speech_calls.append(1)
            return result()

    class Translate:
        def __init__(self, key, usage_callback=None, model=OPENAI_TRANSLATION_MODEL):
            pass

        async def translate(self, *args):
            return TranslationResult(
                "Hello 42", 0, 0, OPENAI_TRANSLATION_MODEL, "synthetic"
            )

    monkeypatch.setattr("recorder.local_worker.ElevenLabsAdapter", Speech)
    monkeypatch.setattr("recorder.local_worker.OpenAITranslator", Translate)
    await worker.run_once()
    await worker.run_once()
    await worker.run_once()
    assert store.get("conversations", cid)["transcription_state"] == "complete"
    assert store.get("conversations", cid)["english_state"] == "blocked"
    assert store.get_turns(cid)[0]["source_text"] == "नमस्ते 42"
    connected(store, "openai-translator")
    resumed, uncertain = store.retry_jobs(cid, ["translate"])
    assert len(resumed) == 1 and not uncertain
    await worker.run_once()
    assert (
        speech_calls == [1]
        and store.get("conversations", cid)["english_state"] == "complete"
    )


@pytest.mark.asyncio
async def test_uncertain_provider_submission_is_never_claimed_or_retried_automatically(
    tmp_path, monkeypatch
):
    store, worker, cid, _ = recording(tmp_path)
    connected(store, "elevenlabs")
    calls = []

    class Speech:
        def __init__(self, key):
            pass

        async def process(self, *args):
            calls.append(1)
            raise ProviderError("external_execution_uncertain", uncertain=True)

    monkeypatch.setattr("recorder.local_worker.ElevenLabsAdapter", Speech)
    await worker.run_once()
    await worker.run_once()
    assert not await worker.run_once()
    resumed, uncertain = store.retry_jobs(cid, ["transcribe"])
    assert not resumed and len(uncertain) == 1 and calls == [1]
    assert store.get("conversations", cid)["final_state"] == "uncertain"


@pytest.mark.asyncio
async def test_bad_chunk_checksum_stops_before_provider_submission(tmp_path):
    store, worker, cid, _ = recording(tmp_path)
    row = store.chunks(cid)[0]
    store.chunk_path(cid, row["part_id"], 0).write_bytes(
        pcm_to_wav(b"\x01\x00" * 16000)
    )
    await worker.run_once()
    assert store.list("jobs")[0]["state"] == "failed"
    assert store.list("jobs")[0]["error_code"] == "input_hash_mismatch"
    assert not store.audio_path(cid).exists()
    assert not any(job["kind"] == "transcribe" for job in store.list("jobs"))


@pytest.mark.asyncio
async def test_invalid_paid_timestamps_preserve_audio_and_report_uncertainty(
    tmp_path, monkeypatch
):
    store, worker, cid, _ = recording(tmp_path)
    connected(store, "elevenlabs")

    class Speech:
        def __init__(self, key):
            pass

        async def process(self, *args):
            invalid = result()
            invalid["source_segments"][0]["end"] = float("nan")
            return invalid

    monkeypatch.setattr("recorder.local_worker.ElevenLabsAdapter", Speech)
    await worker.run_once()
    await worker.run_once()
    assert store.get_turns(cid) == []
    assert store.get("conversations", cid)["transcription_state"] == "uncertain"
    assert store.audio_path(cid).exists()


@pytest.mark.asyncio
async def test_partial_translation_is_cached_and_explicit_retry_only_translates_missing_turn(
    tmp_path, monkeypatch
):
    store, worker, cid, run_id = recording(tmp_path)
    connected(store, "elevenlabs")
    connected(store, "openai-translator")
    speech_calls, translated = [], []
    fail_second = True

    class Speech:
        def __init__(self, key):
            pass

        async def process(self, *args):
            speech_calls.append(1)
            value = result()
            value["source_segments"] = [
                {
                    "start": 0.1,
                    "end": 0.3,
                    "text": "नमस्ते 42",
                    "speaker": "speaker_0",
                    "words": [{"text": "नमस्ते 42", "start": 0.1, "end": 0.3}],
                },
                {
                    "start": 0.4,
                    "end": 0.7,
                    "text": "आदेश 43",
                    "speaker": "speaker_1",
                    "words": [{"text": "आदेश 43", "start": 0.4, "end": 0.7}],
                },
            ]
            return value

    class Translate:
        def __init__(self, key, usage_callback=None, model=OPENAI_TRANSLATION_MODEL):
            self.usage_callback = usage_callback

        async def translate(self, text, *args):
            translated.append(text)
            if text == "आदेश 43" and fail_second:
                raise ProviderError("provider_quota_exceeded")
            output = TranslationResult(
                "Hello 42" if text == "नमस्ते 42" else "Order 43",
                10,
                3,
                OPENAI_TRANSLATION_MODEL,
                "synthetic",
            )
            await self.usage_callback(output)
            return output

    monkeypatch.setattr("recorder.local_worker.ElevenLabsAdapter", Speech)
    monkeypatch.setattr("recorder.local_worker.OpenAITranslator", Translate)
    await worker.run_once()
    await worker.run_once()
    await worker.run_once()
    assert [turn["english_text"] for turn in store.get_turns(cid)] == ["Hello 42", None]
    assert store.get("conversations", cid)["english_state"] == "blocked"
    fail_second = False
    store.retry_jobs(cid, ["translate"])
    await worker.run_once()
    assert speech_calls == [1] and translated == ["नमस्ते 42", "आदेश 43", "आदेश 43"]
    assert [turn["english_text"] for turn in store.get_turns(cid)] == [
        "Hello 42",
        "Order 43",
    ]
    assert store.get("runs", run_id)["translation_input_tokens"] == 20


def test_paid_claim_crash_is_fenced_as_uncertain_after_lease_expiry(tmp_path):
    store, _, cid, _ = recording(tmp_path)
    job = store.claim_job("worker-one")
    store.mark_job_accepted(job["id"], lease_token=job["lease_token"])
    store.patch("jobs", job["id"], {"lease_until": 0})
    assert store.claim_job("worker-two") is None
    assert store.get("jobs", job["id"])["state"] == "uncertain"
