"""Adversarial worker/provider checks. Every network request uses MockTransport."""
import asyncio
import hashlib
import json
import struct
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from inference.engine import Engine, validate_audio_file
from inference.worker import Worker, validate_audio_url
from recorder.audio import MAX_SESSION_SAMPLES, pcm_to_wav
from recorder.config import Settings
from recorder.local_store import LocalStore
from recorder.local_worker import LocalWorker
from recorder.providers import AzureTranslator, ElevenLabsAdapter, ProviderError, bounded_request
from recorder.worker import MainWorker, audio_range


@pytest.fixture
def transport(monkeypatch):
    original = httpx.AsyncClient

    def install(handler):
        def client(**kwargs):
            assert kwargs.get("trust_env") is False
            assert kwargs.get("follow_redirects") is False
            return original(transport=httpx.MockTransport(handler), **kwargs)
        monkeypatch.setattr(httpx, "AsyncClient", client)
    return install


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["speech", "azure"])
@pytest.mark.parametrize("failure", [httpx.WriteError, httpx.WriteTimeout])
async def test_partial_request_write_never_becomes_safe_to_auto_bill_again(transport, provider, failure):
    calls = []

    def handler(request):
        calls.append(request)
        raise failure("private diagnostic", request=request)

    transport(handler)
    operation = (ElevenLabsAdapter("fixture-key").process(pcm_to_wav(b"\0\0" * 16000))
                 if provider == "speech" else AzureTranslator("fixture-key", "centralindia").translate("नमस्ते"))
    with pytest.raises(ProviderError) as error:
        await operation
    assert error.value.uncertain and not error.value.retryable
    assert len(calls) == 1 and "private diagnostic" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    lambda result: result.update(words=None),
    lambda result: result.update(words=[None]),
    lambda result: result["words"][0].update(start=True),
    lambda result: result["words"][0].update(start=float("nan")),
    lambda result: result["words"][0].update(end=float("inf")),
    lambda result: result["words"][0].update(end=99),
    lambda result: result["words"][0].update(speaker_id={"malformed": True}),
    lambda result: result.update(language_probability=float("nan")),
    lambda result: result.update(language_code={"malformed": True}),
])
async def test_unusable_paid_speech_never_reaches_publication_or_releases_charge_fence(transport, mutation):
    body = {"text": "Hello", "language_code": "en", "language_probability": 0.99,
            "words": [{"type": "word", "text": "Hello", "start": 0.1, "end": 0.7, "speaker_id": "speaker_0"}]}
    mutation(body)
    transport(lambda request: httpx.Response(200, content=json.dumps(body).encode()))
    with pytest.raises(ProviderError) as error:
        await ElevenLabsAdapter("fixture-key").process(pcm_to_wav(b"\0\0" * 16000))
    assert error.value.uncertain and not error.value.retryable


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [[], [{"translations": []}], [{"translations": [{"text": None}]}],
                                  [{"translations": [{"text": "Hello 123"}]}], [{"translations": [{"text": "<span></span>"}]}]])
async def test_unusable_paid_azure_translation_is_held_for_review(transport, body):
    transport(lambda request: httpx.Response(200, json=body))
    with pytest.raises(ProviderError) as error:
        await AzureTranslator("fixture-key", "centralindia").translate("नमस्ते")
    assert error.value.uncertain and not error.value.retryable


@pytest.mark.asyncio
async def test_azure_cannot_change_indic_quantities_adjacent_to_native_words(transport):
    transport(lambda request: httpx.Response(200, json=[{"translations": [{"text": "₹26 rupees"}]}]))
    with pytest.raises(ProviderError, match="translation_term_preservation_failed") as error:
        await AzureTranslator("fixture-key", "centralindia").translate("₹२५रुपये")
    assert error.value.uncertain


@pytest.mark.asyncio
async def test_second_azure_chunk_rejection_preserves_first_chunk_charge(transport):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 2:
            return httpx.Response(429)
        text = json.loads(request.content)[0]["Text"]
        return httpx.Response(200, json=[{"translations": [{"text": text}]}])

    transport(handler)
    with pytest.raises(ProviderError) as error:
        await AzureTranslator("fixture-key", "centralindia").translate("Hello world " * 5000)
    assert len(calls) == 2 and error.value.uncertain and not error.value.retryable


@pytest.mark.asyncio
async def test_long_azure_turn_splits_without_changing_unicode_or_technical_literals(transport):
    calls = []
    def handler(request):
        protected = json.loads(request.content)[0]["Text"]
        calls.append(protected)
        assert len(protected) <= 45000
        return httpx.Response(200, json=[{"translations": [{"text": protected}]}])
    transport(handler)
    source = ("किं X123 25 kg " * 5000)
    translated = await AzureTranslator("fixture-key", "centralindia").translate(source)
    assert len(calls) > 1
    assert translated.count("किं") == translated.count("X123") == translated.count("25 kg") == 5000


class LargeBody(httpx.AsyncByteStream):
    def __init__(self):
        self.chunks = 0
        self.closed = False

    async def __aiter__(self):
        for _ in range(100):
            self.chunks += 1
            yield b"x" * 65536

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_provider_stream_stops_reading_at_bound_and_holds_paid_request():
    stream = LargeBody()
    handler = lambda request: httpx.Response(200, stream=stream)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
        with pytest.raises(ProviderError) as error:
            await bounded_request(client, "POST", "https://provider.example", max_bytes=65536, paid=True)
    assert error.value.uncertain and stream.chunks == 2 and stream.closed


@pytest.mark.asyncio
async def test_compressed_provider_body_is_rejected_before_decoding():
    stream = LargeBody()
    def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=stream)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
        with pytest.raises(ProviderError) as error:
            await bounded_request(client, "POST", "https://provider.example", paid=True)
    assert error.value.uncertain and stream.chunks == 0 and stream.closed


@pytest.mark.parametrize("sample_start,sample_end", [(False, 1), (0, True), (-1, 2), (0, 0),
                                                    (2, 1), (0, MAX_SESSION_SAMPLES + 1), ("0", 1), (0, None)])
def test_invalid_job_audio_ranges_never_assemble(sample_start, sample_end):
    with pytest.raises(ProviderError, match="invalid_audio"):
        audio_range({"payload": {"sample_start": sample_start, "sample_end": sample_end}})


@pytest.mark.parametrize("part", ["../", "%2e%2e/", "%252e%252e/", "\\", "%2f", "%5c", "./", "//"])
def test_signed_object_urls_reject_ambiguous_path_normalization(part):
    url = f"https://project.supabase.co/storage/v1/object/sign/recorder-artifacts/org/user/conv-1/{part}input.wav?token=fixture"
    with pytest.raises(ValueError):
        validate_audio_url(url, "https://project.supabase.co", {"conversation_id": "conv-1"})


def test_signed_audio_must_be_scoped_to_the_claimed_job():
    url = "https://project.supabase.co/storage/v1/object/sign/recorder-artifacts/org/user/conv-1/artifacts/job-2/input.wav"
    with pytest.raises(ValueError, match="invalid_audio_scope"):
        validate_audio_url(url, "https://project.supabase.co", {"conversation_id": "conv-1", "job_id": "job-1"})


@pytest.mark.asyncio
@pytest.mark.parametrize("expected_bytes", [True, -1, 0, 44, 1.5, "32044", 230_404_097])
async def test_invalid_download_manifest_never_opens_a_network_connection(expected_bytes, tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: pytest.fail("Invalid manifest must not download"))
    worker = Worker.__new__(Worker)
    worker.storage_origin = "https://project.supabase.co"
    job = {"conversation_id": "conv-1", "audio_url": "https://project.supabase.co/storage/v1/object/sign/recorder-artifacts/conv-1/input.wav",
           "sha256": "0" * 64, "bytes": expected_bytes}
    with pytest.raises(ValueError, match="invalid_audio_manifest"):
        await worker.download(job, tmp_path / "input.wav")


@pytest.mark.parametrize("mutation", [
    lambda wav: wav[:30],
    lambda wav: wav[:22] + struct.pack("<H", 2) + wav[24:],
    lambda wav: wav[:24] + struct.pack("<I", 8000) + wav[28:],
    lambda wav: wav[:40] + struct.pack("<I", 500000000) + wav[44:],
])
def test_invalid_audio_is_rejected_before_native_model_decoder(tmp_path, mutation):
    data = mutation(pcm_to_wav(b"\0\0" * 16000))
    path = tmp_path / "input.wav"
    path.write_bytes(data)
    engine = Engine.__new__(Engine)
    engine._process_block = lambda *args: pytest.fail("Malformed audio must not reach a native decoder")
    with pytest.raises((ValueError, EOFError)):
        engine.process(str(path), {"job_id": "job"})


def test_valid_inference_pcm_passes_streamed_validation(tmp_path):
    path = tmp_path / "input.wav"
    path.write_bytes(pcm_to_wav(b"\0\0" * 16000))
    assert validate_audio_file(str(path)) == 16000


@pytest.mark.asyncio
async def test_shutdown_cancels_in_flight_provider_without_resetting_durable_submission(monkeypatch):
    worker = MainWorker(Settings(_env_file=None))
    accepted, cancelled = asyncio.Event(), asyncio.Event()

    async def transcribe(_job):
        accepted.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def renew(_job):
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "transcribe", transcribe)
    monkeypatch.setattr(worker, "renew", renew)
    worker.db = SimpleNamespace(connection=lambda: pytest.fail("Shutdown must preserve durable submission state"))
    execution = asyncio.create_task(worker.execute({"id": "shutdown-job", "kind": "transcribe", "conversation_id": "shutdown-conversation", "attempt": 1}))
    await asyncio.wait_for(accepted.wait(), timeout=1)
    worker.stopping.set()
    await asyncio.wait_for(execution, timeout=1)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_local_assembly_rejects_chunk_symlink_before_reading_other_files(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "store")
    cid, part = str(uuid4()), str(uuid4())
    store.put("conversations", cid, {"id": cid, "capture_state": "stopped", "total_samples": 16000})
    content = pcm_to_wav(b"\0\0" * 16000)
    row = {"part_id": part, "sequence": 0, "start_sample": 0, "sample_count": 16000,
           "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    store.authorize_chunk(cid, row)
    store.mark_chunk(cid, part, 0, confirmed=True)
    private = tmp_path / "private.wav"
    private.write_bytes(content)
    path = store.chunk_path(cid, part, 0)
    path.parent.mkdir(parents=True)
    path.symlink_to(private)
    worker = LocalWorker(Settings(_env_file=None, local_data_dir=store.root), store)
    monkeypatch.setattr(store, "assert_job", lambda *args: None)
    with pytest.raises(ProviderError, match="invalid_audio"):
        await worker.assemble({"id": "fixture-job", "lease_token": "fixture-lease", "conversation_id": cid})
    assert not store.audio_path(cid).exists()
