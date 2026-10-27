"""Storage failures use synthetic upstreams; no customer audio or provider calls."""
from contextlib import asynccontextmanager
import gzip
import hashlib
import logging
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from recorder.audio import pcm_to_wav
from recorder.config import Settings
from recorder.middleware import RequestBoundary
from recorder.security import workspace
from recorder.storage import Storage
from recorder import transcript_routes


@pytest.fixture
def storage():
    return Storage(Settings(_env_file=None, supabase_url="https://fixture.supabase.co",
                            supabase_service_role_key="synthetic-service-credential"))


def object_path(kind="session-audio"):
    return f"{uuid4()}/{uuid4()}/{uuid4()}/{kind}/fixture.json"


@pytest.mark.asyncio
async def test_compressed_storage_write_response_is_decoded_once(storage, respx_mock):
    path = object_path("results")
    respx_mock.post(f"{storage.base}/object/{storage.path(path)}").respond(
        200, headers={"Content-Encoding": "gzip", "Content-Type": "application/json"},
        content=gzip.compress(b'{"Key":"synthetic"}'))
    await storage.put(path, b'{"transcript":"synthetic"}')


@pytest.mark.asyncio
async def test_decoded_storage_response_limit_still_bounds_compressed_body(storage, respx_mock):
    path = object_path()
    respx_mock.post(f"{storage.base}/object/sign/{storage.path(path)}").respond(
        200, headers={"Content-Encoding": "gzip"}, content=gzip.compress(b"x" * 262145))
    with pytest.raises(HTTPException) as error:
        await storage.signed_read(path)
    assert error.value.detail["code"] == "invalid_storage_response"


@pytest.mark.asyncio
async def test_storage_read_retries_transient_failure_and_logs_no_private_body(storage, respx_mock, caplog):
    path = object_path()
    route = respx_mock.get(f"{storage.base}/object/authenticated/{storage.path(path)}")
    route.side_effect = [httpx.Response(503, text="customer-secret"), httpx.Response(200, content=b"audio")]
    with caplog.at_level(logging.WARNING, logger="recorder.storage"):
        assert await storage.read(path, 5) == b"audio"
    assert route.call_count == 2
    assert "http_status=503" in caplog.text and "operation=read" in caplog.text
    assert "customer-secret" not in caplog.text
    assert "synthetic-service-credential" not in caplog.text
    assert path not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_permanent_storage_read_failure_is_not_retried(storage, respx_mock, status):
    path = object_path()
    route = respx_mock.get(f"{storage.base}/object/authenticated/{storage.path(path)}").respond(status)
    with pytest.raises(HTTPException) as error:
        await storage.read(path, 5)
    assert route.call_count == 1
    assert error.value.detail["code"] == ("audio_object_missing" if status == 404 else "storage_unavailable")


@pytest.mark.asyncio
async def test_storage_diagnostic_code_is_allowlisted_and_never_logs_message(storage, respx_mock, caplog):
    path = object_path()
    route = respx_mock.get(f"{storage.base}/object/authenticated/{storage.path(path)}")
    for code in ("NoSuchBucket", "private-customer-name"):
        caplog.clear()
        route.respond(400, json={"code": code, "message": "private-customer-content"})
        with caplog.at_level(logging.WARNING, logger="recorder.storage"), pytest.raises(HTTPException):
            await storage.read(path, 5)
        assert ("storage_code=NoSuchBucket" if code == "NoSuchBucket" else "storage_code=unknown") in caplog.text
        assert "private-customer" not in caplog.text


def playback_app(monkeypatch, *, failed_chunk):
    app = FastAPI()
    app.include_router(transcript_routes.router)
    app.add_middleware(RequestBoundary, settings=Settings(_env_file=None))
    app.dependency_overrides[workspace] = lambda: object()
    data = pcm_to_wav(b"\x01\x00" * 16000)
    reads = []
    chunks = [{"object_path": f"chunk-{index}", "sample_start": index * 16000,
               "sample_count": 16000, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
               "state": "validated"} for index in range(2)]

    class Database:
        @asynccontextmanager
        async def connection(self):
            yield object()

    async def conversation(*args):
        return {"total_samples": 32000}

    async def many(*args):
        return chunks

    async def read(path, maximum):
        reads.append(path)
        if path == f"chunk-{failed_chunk}":
            raise HTTPException(503, {"code": "storage_unavailable"})
        return data

    monkeypatch.setattr(transcript_routes, "conversation", conversation)
    monkeypatch.setattr(transcript_routes, "many", many)
    app.state.db = Database()
    app.state.storage = SimpleNamespace(read=read)
    return app, reads


def test_first_playback_chunk_failure_returns_json_before_audio_headers(monkeypatch):
    app, reads = playback_app(monkeypatch, failed_chunk=0)
    with TestClient(app) as client:
        response = client.get(f"/api/v1/conversations/{uuid4()}/playback/stream")
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/json"
    assert response.json()["detail"]["code"] == "storage_unavailable"
    assert reads == ["chunk-0"]


def test_later_playback_failure_aborts_instead_of_completing_partial_audio(monkeypatch):
    app, reads = playback_app(monkeypatch, failed_chunk=1)
    with TestClient(app) as client, pytest.raises(RuntimeError, match="stream_response_incomplete"):
        client.get(f"/api/v1/conversations/{uuid4()}/playback/stream")
    assert reads == ["chunk-0", "chunk-1"]


@pytest.mark.asyncio
async def test_partial_content_length_response_is_not_falsely_completed():
    async def endpoint(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"5")]})
        await send({"type": "http.response.body", "body": b"a", "more_body": True})
        raise RuntimeError("synthetic interruption")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    messages = []

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "method": "GET", "path": "/audio", "query_string": b"",
             "headers": [(b"host", b"testserver")], "client": ("192.0.2.1", 1234)}
    boundary = RequestBoundary(endpoint, Settings(_env_file=None))
    with pytest.raises(RuntimeError, match="stream_response_incomplete"):
        await boundary(scope, receive, send)
    assert len(messages) == 2 and messages[-1]["more_body"] is True
    assert boundary.active == 0
