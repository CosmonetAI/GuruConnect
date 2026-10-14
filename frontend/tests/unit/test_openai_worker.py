"""Worker/provider boundary tests; all storage, DB and provider IO is isolated."""
import asyncio
import copy
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

import recorder.worker as module
import recorder.processing as processing
from recorder.openai_translator import MODEL, TranslationResult
from recorder.providers import ProviderError
from recorder.worker import MainWorker


@pytest.fixture
def worker_case(monkeypatch):
    job = {
        "id": "job-translation", "conversation_id": "conversation-1", "run_id": "run-1",
        "organisation_id": "organisation-1", "connection_id": "connection-translation",
        "connection_generation": 1, "kind": "translate", "attempt": 1, "fence": 3,
        "lease_token": "lease-1", "window_id": None,
        "payload": {
            "source_job_id": "job-source", "source_version_id": "source-version",
            "sample_start": 160000, "sample_end": 480000, "final": True,
            "translation_strategy": "openai", "translation_provider": "openai-translator", "translator_model": MODEL,
        },
    }
    turn = {
        "id": "source-turn-1", "ordinal": 0, "source_text": "ऑर्डर X123 25 kg",
        "source_language": "hi", "start_seconds": 12, "end_seconds": 15,
        "speaker_ref": "detected-source-voice", "speaker_confidence": "supported",
    }
    original = {
        "result": {
            "language": "hi", "source_segments": [{"text": turn["source_text"], "start": 2, "end": 5}],
            "english_state": "pending", "diarization_status": "completed",
            "provenance": {"engine": "elevenlabs", "model": "scribe_v2", "speaker_scope": "job-source"},
        },
    }
    state = SimpleNamespace(
        job=job, source=original, turns=[turn], cache={}, executed=[], finished=[], published=[],
        words=[
            {"turn_id": turn["id"], "word": "ऑर्डर ", "start_seconds": 12, "end_seconds": 12.5},
            {"turn_id": turn["id"], "word": "X123 ", "start_seconds": 12.5, "end_seconds": 13},
            {"turn_id": turn["id"], "word": "25 kg", "start_seconds": 13, "end_seconds": 15},
        ],
        submitted=[], openai_calls=[], azure_calls=[], provider_failure=None, usage_chunks=1,
        connection={"id": job["connection_id"], "organisation_id": job["organisation_id"], "engine_id": "openai-translator", "generation": 1,
                    "status": "ready", "readiness": "functional", "config": {"model": MODEL}, "encrypted_value": "fixture-ciphertext"},
    )

    class Connection:
        async def execute(self, sql, params=()):
            state.executed.append((sql, params))
            if sql.startswith("insert into translations"):
                state.cache[(params[1], params[3])] = {"english_text": params[6]}

    class DB:
        @asynccontextmanager
        async def connection(self):
            yield Connection()

    async def one(connection, sql, params=()):
        if "select p.result from processing_jobs" in sql:
            assert params == ("job-source", "conversation-1", "run-1", "source-version")
            return copy.deepcopy(state.source)
        if "from engine_connections" in sql:
            if "provider_secrets" not in sql:
                assert "organisation_id=%s" in sql and params[1] == state.job["organisation_id"]
            return copy.deepcopy(state.connection)
        if "from translations" in sql:
            return state.cache.get((params[0], params[1]))
        if "finish_job(" in sql:
            state.finished.append(copy.deepcopy(params[-1].obj))
            return {"ok": True}
        if "begin_job_submission(" in sql:
            state.submitted.append(params)
            return {"ok": True}
        if "retry_job(" in sql:
            state.executed.append((sql, params))
            return {"accepted": True}
        raise AssertionError(f"Unexpected isolated worker query: {sql}")

    async def many(connection, sql, params=()):
        assert "v.conversation_id=%s" in sql and "v.run_id=%s" in sql
        assert params[:3] == ("source-version", "conversation-1", "run-1")
        if "from transcript_words" in sql:
            return copy.deepcopy(state.words)
        assert "from transcript_turns" in sql
        return copy.deepcopy(state.turns)

    async def publish(connection, selected_job, result):
        state.published.append(copy.deepcopy(result))
        return "translated-version"

    class SecretBox:
        def __init__(self, settings):
            pass

        def decrypt(self, encrypted):
            assert encrypted == "fixture-ciphertext"
            return {"api_key": f"fixture-{state.connection['engine_id']}", "region": "centralindia"}

    class FakeOpenAI:
        def __init__(self, key, *, usage_callback=None):
            assert key == "fixture-openai-translator"
            self.usage_callback = usage_callback

        async def translate(self, source, source_language=None):
            state.openai_calls.append((source, source_language))
            result = TranslationResult("Order X123 25 kg", 101, 19, MODEL, "req-translation")
            if self.usage_callback:
                for index in range(state.usage_chunks):
                    await self.usage_callback(TranslationResult("", 101, 19, MODEL, f"req-translation-{index}"))
            if state.provider_failure:
                raise state.provider_failure
            return result

    class FakeAzure:
        def __init__(self, key, region):
            assert key == "fixture-azure-translator" and region == "centralindia"

        async def translate(self, source, source_language=None):
            state.azure_calls.append((source, source_language))
            return "Order X123 25 kg"

    worker = object.__new__(MainWorker)
    worker.db = DB()
    worker.settings = SimpleNamespace(
        azure_translator_key=SecretStr("fixture-legacy-azure"), azure_translator_region="centralindia",
        elevenlabs_api_key=SecretStr("fixture-legacy-eleven"), worker_lease_seconds=180,
    )
    worker.id = "isolated-worker"
    worker.stopping = asyncio.Event()
    worker.storage = SimpleNamespace()  # Translation must never read or replace saved audio.
    monkeypatch.setattr(module, "one", one)
    monkeypatch.setattr(module, "many", many)
    monkeypatch.setattr(module, "publish_result", publish)
    monkeypatch.setattr(module, "SecretBox", SecretBox)
    monkeypatch.setattr(module, "OpenAITranslator", FakeOpenAI)
    monkeypatch.setattr(module, "AzureTranslator", FakeAzure)
    state.worker = worker
    return state


@pytest.mark.asyncio
async def test_openai_worker_preserves_source_speaker_interval_and_records_actual_usage(worker_case):
    state = worker_case
    source_before = copy.deepcopy(state.source)
    await state.worker.translate(state.job)
    assert state.openai_calls == [("ऑर्डर X123 25 kg", None)]
    assert not state.azure_calls and len(state.submitted) == 1
    assert state.source == source_before
    result = state.published[-1]
    assert result["english_state"] == "complete"
    source, english = result["source_segments"][0], result["english_segments"][0]
    assert source["text"] == "ऑर्डर X123 25 kg" and english["text"] == "Order X123 25 kg"
    assert (english["start"], english["end"]) == (2, 5)
    assert english["speaker"] == source["speaker"] == "detected-source-voice"
    assert english["speaker_confidence"] == "supported" and english["words"] is None
    assert source["words"] == [
        {"text": "ऑर्डर ", "start": 2, "end": 2.5},
        {"text": "X123 ", "start": 2.5, "end": 3},
        {"text": "25 kg", "start": 3, "end": 5},
    ]
    provenance = result["provenance"]
    assert provenance["engine"] == "elevenlabs" and provenance["model"] == "scribe_v2"
    assert MODEL in str(provenance) and "openai" in str(provenance)
    assert provenance["translation_connection_id"] == "connection-translation"
    assert provenance["translation_generation"] == 1 and provenance["translated_word_alignment"] is None
    usage = [(sql, params) for sql, params in state.executed if "insert into usage_records" in sql]
    tokens = [(sql, params) for sql, params in usage if params[7] or params[8]]
    assert len(tokens) == 1 and "input_tokens" in tokens[0][0] and "output_tokens" in tokens[0][0]
    assert tokens[0][1][7:10] == (101, 19, MODEL)
    assert tokens[0][1][6] == "turn-0-chunk-1"
    assert "fixture-openai-translator" not in str(state.executed) and "fixture-openai-translator" not in str(provenance)


@pytest.mark.asyncio
async def test_cached_openai_turn_is_reused_without_new_paid_call_or_usage(worker_case):
    state = worker_case
    await state.worker.translate(state.job)
    assert state.cache
    state.openai_calls.clear()
    state.executed.clear()
    await state.worker.translate(state.job)
    assert not state.openai_calls and not state.azure_calls
    assert not any("insert into usage_records" in sql for sql, _ in state.executed)
    assert state.published[-1]["english_segments"][0]["text"] == "Order X123 25 kg"


@pytest.mark.asyncio
async def test_chunk_usage_has_unique_accounting_key_for_every_paid_call(worker_case):
    state = worker_case
    state.usage_chunks = 2
    await state.worker.translate(state.job)
    tokens = [params for sql, params in state.executed if "insert into usage_records" in sql and params[7]]
    assert len(tokens) == 2
    assert [params[6] for params in tokens] == ["turn-0-chunk-1", "turn-0-chunk-2"]
    assert sum(params[7] for params in tokens) == 202 and sum(params[8] for params in tokens) == 38


@pytest.mark.asyncio
async def test_new_provider_generation_cannot_reuse_old_translation_cache(worker_case):
    state = worker_case
    await state.worker.translate(state.job)
    state.openai_calls.clear()
    state.job["connection_generation"] = state.connection["generation"] = 2
    await state.worker.translate(state.job)
    assert len(state.openai_calls) == 1 and len(state.cache) == 2


@pytest.mark.asyncio
async def test_legacy_azure_translation_is_dispatched_to_azure_only(worker_case):
    state = worker_case
    state.connection.update(engine_id="azure-translator", config={})
    state.job["payload"].update(translation_strategy="azure", translation_provider="azure-translator", translator_model=None)
    await state.worker.translate(state.job)
    assert state.azure_calls == [("ऑर्डर X123 25 kg", None)] and not state.openai_calls
    assert state.published[-1]["provenance"]["translation"] == "azure-standard-v3"


@pytest.mark.asyncio
async def test_openai_credentials_never_fall_back_to_azure_platform_key(worker_case):
    state = worker_case
    state.connection.update(encrypted_value=None, config={"platform_managed": True, "model": MODEL})
    with pytest.raises(ProviderError):
        await state.worker.credentials(state.job["connection_id"])
    assert not state.openai_calls and not state.azure_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    lambda state: state.connection.update(generation=99),
    lambda state: state.connection.update(engine_id="elevenlabs"),
    lambda state: state.connection.update(config={"model": "gpt-another-model"}),
    lambda state: state.job["payload"].update(translation_provider="azure-translator"),
    lambda state: state.job["payload"].update(translator_model="gpt-another-model"),
])
async def test_invalid_provider_model_or_generation_is_rejected_before_paid_submission(worker_case, change):
    state = worker_case
    change(state)
    with pytest.raises(ProviderError):
        await state.worker.translate(state.job)
    assert not state.submitted and not state.openai_calls and not state.azure_calls
    assert not state.finished and not state.published and not state.cache


@pytest.mark.asyncio
async def test_paid_translation_failure_preserves_source_and_submission_uncertainty(worker_case):
    state = worker_case
    original = copy.deepcopy(state.source)
    state.provider_failure = ProviderError("translation_term_preservation_failed", uncertain=True)

    async def renewal(job):
        await asyncio.Event().wait()

    state.worker.renew = renewal
    await state.worker.execute(state.job)
    assert state.source == original and not state.finished and not state.published and not state.cache
    assert len(state.submitted) == 1
    retries = [(sql, params) for sql, params in state.executed if "retry_job(" in sql]
    assert len(retries) == 1 and retries[0][1][-2:] == ("translation_term_preservation_failed", True)
    assert not any("submission_state='not_submitted'" in sql for sql, _ in state.executed)
    assert any("insert into usage_records" in sql for sql, _ in state.executed)
    assert not any("transcript_turns" in sql or "audio_chunks" in sql for sql, _ in state.executed)


@pytest.mark.asyncio
async def test_uncertain_api_timeout_never_removes_saved_source(worker_case):
    state = worker_case
    original = copy.deepcopy(state.source)
    state.provider_failure = ProviderError("translator_execution_uncertain", uncertain=True)

    async def renewal(job):
        await asyncio.Event().wait()

    state.worker.renew = renewal
    await state.worker.execute(state.job)
    assert state.source == original and not state.published
    assert not any("submission_state='not_submitted'" in sql for sql, _ in state.executed)
    assert not any(sql.startswith("delete") for sql, _ in state.executed)


@pytest.mark.asyncio
async def test_real_publication_preserves_source_voice_scope_and_word_timings(worker_case, monkeypatch):
    state = worker_case
    speaker = "run-1:job-source:speaker-0"
    state.turns[0]["speaker_ref"] = speaker
    await state.worker.translate(state.job)
    result = state.finished[-1]
    turns, words, updates = [], [], []

    class PublicationConnection:
        async def execute(self, sql, params=()):
            if sql.startswith("insert into transcript_words"):
                words.append(params)
            if sql.startswith("update conversations set active_version_id"):
                updates.append(params)

    async def one(connection, sql, params=()):
        if sql.startswith("select id from transcript_versions where job_id"):
            return None
        if sql.startswith("select * from processing_runs"):
            return {"id": "run-1", "engine_id": "elevenlabs", "cancelled_at": None, "kind": "production"}
        if sql.startswith("select * from conversations"):
            return {"id": "conversation-1", "total_samples": 480000, "active_version_id": None}
        if "select exists(select 1 from transcript_revisions" in sql:
            return {"edited": False}
        if sql.startswith("insert into transcript_versions"):
            return {"id": "translated-version"}
        if sql.startswith("insert into transcript_turns"):
            turns.append(params)
            return {"id": "published-turn"}
        if sql.startswith("select t.id from transcript_turns"):
            return None
        raise AssertionError(f"Unexpected publication query: {sql}")

    async def many(connection, sql, params=()):
        assert "from transcription_windows" in sql
        return []

    monkeypatch.setattr(processing, "one", one)
    monkeypatch.setattr(processing, "many", many)
    version = await processing.publish_result(PublicationConnection(), state.job, result)
    assert version == "translated-version" and len(turns) == 1
    assert turns[0][5:10] == (12, 15, "ऑर्डर X123 25 kg", "Order X123 25 kg", "hi")
    assert turns[0][10] == speaker  # No extra translation-job speaker prefix.
    assert words == [
        ("published-turn", "translated-version", "ऑर्डर ", 12, 12.5),
        ("published-turn", "translated-version", "X123 ", 12.5, 13),
        ("published-turn", "translated-version", "25 kg", 13, 15),
    ]
    assert updates[0][0] == "translated-version" and updates[0][2] == "complete"
