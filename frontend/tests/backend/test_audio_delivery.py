"""Real local HTTP + background lifecycle and real FFmpeg; no paid providers."""
import asyncio
import hashlib
import io
import shutil
import time
import wave
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from recorder.audio import pcm_to_wav
from recorder.config import Settings
from recorder.local_api import create_local_app
from recorder.media import mp3_chunks
from recorder.openai_translator import TranslationResult


@pytest.fixture
def api(tmp_path):
    app = create_local_app(Settings(_env_file=None, local_data_dir=tmp_path,
        database_url=None, supabase_url="", supabase_service_role_key=None))
    with TestClient(app, base_url="http://127.0.0.1:8010", client=("127.0.0.1", 12345)) as client:
        yield client, app.state.local_store


def recording(client, *, seconds=1, audio_only=True):
    created = client.post("/api/v1/conversations", headers={"Idempotency-Key": str(uuid4())}, json={
        "customer_name": "Synthetic recording", "phone": "9000000000", "consent_confirmed": True,
        "audio_only": audio_only})
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    lease = client.post(f"/api/v1/conversations/{cid}/lease", json={"tab_id": str(uuid4())}).json()
    samples = seconds * 16000
    data = pcm_to_wav(b"\x01\x00" * samples)
    meta = {"part_id": lease["part_id"], "lease_token": lease["lease_token"], "sequence": 0,
            "sample_count": samples, "sample_rate": 16000, "start_sample": 0, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "mime": "audio/wav"}
    permit = client.post(f"/api/v1/conversations/{cid}/chunks/authorize", json=meta)
    assert permit.status_code == 200, permit.text
    assert client.put(permit.json()["upload_url"], content=data).status_code == 200
    assert client.post(f"/api/v1/conversations/{cid}/chunks/confirm", json=meta).status_code == 200
    final = client.post(f"/api/v1/conversations/{cid}/finalize", json={
        "part_id": lease["part_id"], "lease_token": lease["lease_token"], "expected_chunks": 1,
        "total_samples": samples})
    assert final.status_code == 200, final.text
    return cid, data


def eventually(check, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    assert check(), "Background processing did not complete within the synthetic test timeout"


def test_current_local_player_streams_verified_saved_audio_and_sample_offsets(api):
    client, store = api
    cid, data = recording(client, seconds=11)
    response = client.get(f"/api/v1/conversations/{cid}/playback/stream")
    assert response.status_code == 200
    assert response.content == data
    tail = client.get(f"/api/v1/conversations/{cid}/playback/stream?start_sample=16000")
    with wave.open(io.BytesIO(tail.content)) as wav:
        assert wav.getnframes() == 10 * 16000
    assert client.get(f"/api/v1/conversations/{cid}/playback/stream?start_sample=176000").status_code == 416
    assert not store.audio_path(cid).exists()  # Streaming requires no assembly job.


def test_local_upload_accepts_legacy_180_second_recovery_chunks(api):
    client, _ = api
    cid, data = recording(client, seconds=180)
    response = client.get(f"/api/v1/conversations/{cid}/playback/stream")
    assert response.status_code == 200 and response.content == data


def test_recording_history_preserves_equal_timestamp_rows_and_searches_email(api):
    client, store = api
    ids = [recording(client)[0] for _ in range(3)]
    for cid in ids:
        store.patch("conversations", cid, {"created_at": "2026-10-27T00:00:00+00:00",
                                           "email": "history-fixture@example.test"})
    found, cursor = [], None
    for _ in range(3):
        params = {"q": "history-fixture@example.test", "limit": 1}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/api/v1/conversations", params=params)
        assert response.status_code == 200
        found.extend(row["id"] for row in response.json()["items"])
        cursor = response.json()["next_cursor"]
    assert found == sorted(ids, reverse=True)
    assert cursor is None
    assert client.get("/api/v1/conversations", params={"cursor": "bad|cursor"}).status_code == 400


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is required for MP3 delivery")
def test_local_mp3_download_handles_binary_backpressure(api):
    client, _ = api
    cid, _ = recording(client, seconds=30)
    response = client.get(f"/api/v1/conversations/{cid}/playback/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert len(response.content) > 100000
    assert response.content.startswith((b"ID3", b"\xff"))


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is required for MP3 delivery")
async def test_long_mp3_encoding_is_bounded_and_cancellable():
    async def source():
        for _ in range(18):
            yield b"\x01\x00" * 160000
    async with asyncio.timeout(10):
        encoded = b"".join([chunk async for chunk in mp3_chunks(source(), 180 * 16000)])
    assert len(encoded) > 1000000
    closed = asyncio.Event()
    async def slow_source():
        try:
            yield b"\x01\x00" * 160000
            await asyncio.Event().wait()
        finally:
            closed.set()
    async def encode():
        return [chunk async for chunk in mp3_chunks(slow_source(), 320000)]
    task = asyncio.create_task(encode())
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert closed.is_set()


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is required for MP3 delivery")
async def test_client_disconnect_with_full_encoder_stdout_reaps_process():
    closed = asyncio.Event()
    async def source():
        try:
            for _ in range(40):
                yield b"\x01\x00" * 160000
        finally:
            closed.set()
    stream = mp3_chunks(source(), 400 * 16000)
    assert await anext(stream)
    await asyncio.sleep(0.3)  # Fill stdout after the browser stops reading.
    await asyncio.wait_for(stream.aclose(), 2)
    assert closed.is_set()


def test_api_service_runs_env_managed_speech_translation_and_durable_deletion(tmp_path, monkeypatch):
    calls = []
    class Speech:
        def __init__(self, key):
            assert key == "synthetic-elevenlabs-server-key"
        async def process(self, audio, language, scope):
            assert audio.read(4) == b"RIFF"
            calls.append("speech")
            return {"language": "hi", "language_probability": .99, "source_segments": [
                {"start": 0, "end": .9, "text": "नमस्ते 42", "speaker": "speaker_0",
                 "words": [{"text": "नमस्ते 42", "start": 0, "end": .9}]}],
                "provenance": {"engine": "elevenlabs", "model": "scribe_v2"}}
    class Translation:
        def __init__(self, key, usage_callback=None, model=None):
            assert key == "synthetic-openai-server-key"
            assert model == "gpt-4o-mini"
            self.account = usage_callback
        async def translate(self, text, language):
            assert text == "नमस्ते 42" and language == "hi"
            calls.append("translation")
            result = TranslationResult("Hello 42", 10, 4, "gpt-4o-mini", "synthetic-request")
            await self.account(result)
            return result
    monkeypatch.setattr("recorder.local_worker.ElevenLabsAdapter", Speech)
    monkeypatch.setattr("recorder.local_worker.OpenAITranslator", Translation)
    settings = Settings(_env_file=None, local_data_dir=tmp_path, embedded_processing_enabled=True,
        database_url=None, supabase_url="", supabase_service_role_key=None,
        elevenlabs_api_key="synthetic-elevenlabs-server-key", openai_api_key="synthetic-openai-server-key",
        OPENAI_MODEL="gpt-4o-mini")
    app = create_local_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8010", client=("127.0.0.1", 12345)) as client:
        store = app.state.local_store
        cid, _ = recording(client, audio_only=False)
        eventually(lambda: store.get("conversations", cid)["final_state"] == "complete")
        transcript = client.get(f"/api/v1/conversations/{cid}/transcript").json()
        assert transcript["turns"][0]["english_text"] == "Hello 42"
        assert calls == ["speech", "translation"]
        with store.transaction() as db:
            assert db.execute("select count(*) from secrets").fetchone()[0] == 0
        metadata = client.get("/api/v1/connections")
        assert "synthetic-" not in metadata.text
        for row in metadata.json()["items"]:
            assert row["safe_metadata"]["platform_managed"]
            for action in ("test", "disable", "disconnect"):
                result = client.post(f"/api/v1/connections/{row['id']}/{action}", json={"functional": False} if action == "test" else {"finish_in_flight": False})
                assert result.status_code == 409
        assert client.delete(f"/api/v1/conversations/{cid}").json()["deleted"]
        assert client.get(f"/api/v1/conversations/{cid}/playback/stream").status_code == 404
        eventually(lambda: not store.audio_path(cid).parent.exists())
        assert store.get_transcript(cid)["version"] is None
    assert app.state.processing.task.done()
