"""Adversarial boundaries use synthetic inputs and mocked upstreams only."""

import asyncio
import json
import logging
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError
from recorder import internal_routes
from recorder.config import Settings
from recorder.main import create_app
from recorder.middleware import RequestBoundary
from recorder.schemas import ConnectionCreate, WorkerHeartbeat, WorkerResult
from recorder.security import JWTVerifier, User, Workspace, administrator
from recorder.storage import Storage


def deployed_settings(**overrides):
    values = {
        "environment": "production",
        "database_url": "postgresql://fixture:synthetic-password@db.example.test/app?sslmode=verify-full",
        "supabase_url": "https://fixture.supabase.co",
        "supabase_service_role_key": "synthetic-service-credential",
        "credential_encryption_key": Fernet.generate_key().decode(),
        "cors_origins": ["https://app.example.test"],
        "trusted_hosts": ["api.example.test"],
    }
    return Settings(_env_file=None, **{**values, **overrides})


@pytest.mark.parametrize("values", [
    {"environment": "prodution"},
    {"supabase_url": "https://user:secret@project.supabase.co"},
    {"supabase_url": "https://project.supabase.co/path"},
    {"supabase_url": "https://project.supabase.co#fragment"},
    {"jwt_issuer": "https://attacker.example/auth/v1"},
    {"cors_origins": ["*"]}, {"cors_origins": ["null"]},
    {"cors_origins": ["https://app.example.test/path"]},
    {"cors_origins": ["https://user:secret@app.example.test"]},
    {"trusted_hosts": ["*"]}, {"trusted_hosts": ["api.example.test:443"]},
    {"trusted_hosts": []}, {"credential_encryption_key": "invalid"},
    {"pool_min_size": 5, "pool_max_size": 1},
])
def test_invalid_security_configuration_is_rejected(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize("values", [
    {"database_url": None}, {"supabase_service_role_key": None}, {"credential_encryption_key": None},
    {"database_url": "postgresql://fixture@db.example.test/app?sslmode=require"},
    {"cors_origins": ["http://app.example.test"]}, {"cors_origins": ["https://localhost"]},
    {"cors_origins": []}, {"trusted_hosts": ["testserver"]}, {"trusted_hosts": ["127.0.0.1"]},
])
def test_production_configuration_fails_closed(values):
    with pytest.raises(ValidationError):
        deployed_settings(**values)


def test_valid_production_disables_documentation_and_has_transport_headers(monkeypatch):
    app = create_app(deployed_settings())
    async def noop():
        pass
    monkeypatch.setattr(app.state.db, "open", noop)
    monkeypatch.setattr(app.state.db, "close", noop)
    with TestClient(app, base_url="https://api.example.test") as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert "max-age=31536000" in response.headers["strict-transport-security"]
        assert response.headers["x-frame-options"] == "DENY"
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400
        assert client.get("/health", headers={"Origin": "https://evil.example"}).status_code == 403
        response = client.post("/api/v1/conversations", content=b"x" * 1_000_001, headers={"Origin": "https://app.example.test"})
        assert response.status_code == 413
        assert response.headers["access-control-allow-origin"] == "https://app.example.test"


def test_partial_cloud_configuration_never_selects_local_no_login(tmp_path):
    app = create_app(Settings(_env_file=None, supabase_url="https://fixture.supabase.co", local_data_dir=tmp_path), allow_local=True)
    assert not hasattr(app.state, "local_store")
    with TestClient(app) as client:
        assert client.get("/api/v1/workspaces").status_code == 401
        assert client.get("/ready").status_code == 503
    assert not (tmp_path / "guruconnect.sqlite3").exists()


async def invoke(boundary, *, method="POST", path="/api/v1/test", headers=(), body=b"", receive=None):
    outgoing = []
    scope = {"type": "http", "method": method, "path": path, "query_string": b"", "scheme": "http",
             "headers": [(b"host", b"testserver"), *headers], "client": ("192.0.2.1", 1234)}
    async def default_receive():
        return {"type": "http.request", "body": body, "more_body": False}
    async def send(message):
        outgoing.append(message)
    await boundary(scope, receive or default_receive, send)
    return outgoing


def boundary(**settings):
    async def endpoint(scope, receive, send):
        incoming = await receive()
        await JSONResponse({"bytes": len(incoming["body"])})(scope, receive, send)
    return RequestBoundary(endpoint, Settings(_env_file=None, **settings))


@pytest.mark.parametrize("method", ["GET", "DELETE", "POST", "PUT", "PATCH"])
@pytest.mark.asyncio
async def test_every_method_body_is_bounded_and_error_headers_are_secure(method):
    messages = await invoke(boundary(), method=method, body=b"x" * 1_000_001)
    assert messages[0]["status"] == 413
    headers = dict(messages[0]["headers"])
    assert headers[b"cache-control"] == b"no-store"
    assert headers[b"x-frame-options"] == b"DENY"
    assert len(headers[b"x-request-id"]) == 36


@pytest.mark.parametrize("headers, body, code", [
    ([(b"content-length", b"-1")], b"", "invalid_content_length"),
    ([(b"content-length", b"abc")], b"", "invalid_content_length"),
    ([(b"content-length", b"0"), (b"content-length", b"1")], b"", "ambiguous_request_headers"),
    ([(b"content-length", b"1"), (b"transfer-encoding", b"chunked")], b"a", "invalid_content_length"),
    ([(b"content-length", b"1")], b"ab", "content_length_mismatch"),
    ([(b"transfer-encoding", b"gzip")], b"", "unsupported_transfer_encoding"),
    ([(b"authorization", b"Bearer a"), (b"authorization", b"Bearer b")], b"", "ambiguous_request_headers"),
])
@pytest.mark.asyncio
async def test_ambiguous_or_invalid_request_framing_is_rejected(headers, body, code):
    messages = await invoke(boundary(), headers=headers, body=body)
    assert messages[0]["status"] == 400
    assert code.encode() in messages[1]["body"]


@pytest.mark.asyncio
async def test_stalled_body_has_absolute_deadline_and_releases_admission():
    limiter = boundary(request_body_timeout_seconds=0.02, max_concurrent_requests=1)
    async def stalled():
        await asyncio.sleep(1)
    messages = await invoke(limiter, receive=stalled)
    assert messages[0]["status"] == 408
    assert limiter.active == 0
    assert (await invoke(limiter))[0]["status"] == 200


@pytest.mark.asyncio
async def test_concurrency_and_preauth_rate_do_not_trust_forwarded_headers():
    limiter = boundary(max_concurrent_requests=1, preauth_rate_limit_per_minute=2)
    entered, release = asyncio.Event(), asyncio.Event()
    async def blocked_receive():
        entered.set()
        await release.wait()
        return {"type": "http.request", "body": b"", "more_body": False}
    running = asyncio.create_task(invoke(limiter, receive=blocked_receive))
    await entered.wait()
    assert (await invoke(limiter))[0]["status"] == 503
    release.set()
    assert (await running)[0]["status"] == 200
    assert (await invoke(limiter, headers=[(b"x-forwarded-for", b"198.51.100.2")]))[0]["status"] == 429
    assert (await invoke(limiter, path="/health"))[0]["status"] == 200


def test_validation_and_unexpected_errors_do_not_expose_customer_or_secret_values(caplog):
    app = create_app(Settings(_env_file=None))
    @app.post("/validation")
    async def validation(body: ConnectionCreate):
        return body
    app.router.routes.insert(0, app.router.routes.pop())  # Place probe before the sales fallback mount.
    @app.get("/unexpected")
    async def unexpected():
        raise RuntimeError("synthetic-private-credential synthetic-private-customer")
    app.router.routes.insert(0, app.router.routes.pop())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/validation", json={"engine_id": "synthetic-private-customer", "credentials": {"synthetic-private-credential": 123}, "idempotency_key": "valid-idem-key"})
        assert response.status_code == 422
        with caplog.at_level(logging.ERROR, logger="recorder.api"):
            unexpected_response = client.get("/unexpected")
        assert unexpected_response.status_code == 500
        assert unexpected_response.headers["cache-control"] == "no-store"
        assert unexpected_response.json()["detail"]["request_id"] == unexpected_response.headers["x-request-id"]
    assert "synthetic-private" not in response.text + unexpected_response.text + caplog.text


def signed_session(settings, kid="test-key", **claims):
    key = ec.generate_private_key(ec.SECP256R1())
    public = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key()))
    public.update(kid=kid, alg="ES256")
    payload = {"sub": str(uuid4()), "aud": "authenticated", "iss": settings.issuer, "iat": int(time.time()),
               "exp": int(time.time()) + 60, "role": "authenticated", **claims}
    return jwt.encode(payload, key, algorithm="ES256", headers={"kid": kid}), public


@pytest.mark.asyncio
async def test_failed_jwks_fetches_have_backoff(respx_mock):
    settings = Settings(_env_file=None, supabase_url="https://fixture.supabase.co")
    token, _ = signed_session(settings)
    verifier = JWTVerifier(settings)
    upstream = respx_mock.get(f"{settings.issuer}/.well-known/jwks.json").mock(return_value=httpx.Response(503))
    for _ in range(3):
        with pytest.raises(HTTPException) as error:
            await verifier.verify(token)
        assert error.value.status_code == 503
    assert upstream.call_count == 1


@pytest.mark.asyncio
async def test_streaming_jwks_has_absolute_deadline(respx_mock, monkeypatch):
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"keys":['
            await asyncio.sleep(1)
            yield b"]}"
    settings = Settings(_env_file=None, supabase_url="https://fixture.supabase.co")
    token, _ = signed_session(settings)
    verifier = JWTVerifier(settings)
    monkeypatch.setattr(verifier, "JWKS_TIMEOUT_SECONDS", 0.02)
    respx_mock.get(f"{settings.issuer}/.well-known/jwks.json").mock(return_value=httpx.Response(200, stream=SlowStream()))
    with pytest.raises(HTTPException) as error:
        await verifier.verify(token)
    assert error.value.status_code == 503


@pytest.mark.parametrize("payload", [{"keys": []}, {"keys": "invalid"}, {"keys": [None]}, {"keys": [{"kid": "same"}, {"kid": "same"}]}])
@pytest.mark.asyncio
async def test_malformed_jwks_responses_fail_safely(respx_mock, payload):
    settings = Settings(_env_file=None, supabase_url="https://fixture.supabase.co")
    token, _ = signed_session(settings)
    respx_mock.get(f"{settings.issuer}/.well-known/jwks.json").mock(return_value=httpx.Response(200, json=payload))
    with pytest.raises(HTTPException) as error:
        await JWTVerifier(settings).verify(token)
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_oversized_jwks_and_tokens_are_rejected(respx_mock):
    settings = Settings(_env_file=None, supabase_url="https://fixture.supabase.co")
    token, _ = signed_session(settings)
    respx_mock.get(f"{settings.issuer}/.well-known/jwks.json").mock(return_value=httpx.Response(200, content=b"x" * 262145))
    with pytest.raises(HTTPException) as error:
        await JWTVerifier(settings).verify(token)
    assert error.value.status_code == 503
    with pytest.raises(HTTPException) as error:
        await JWTVerifier(settings).verify("x" * 16385)
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_nonobject_signed_user_metadata_is_rejected():
    settings = Settings(_env_file=None, supabase_url="https://fixture.supabase.co")
    token, public = signed_session(settings, user_metadata=["private"])
    verifier = JWTVerifier(settings)
    verifier.keys, verifier.cached_at = {"test-key": public}, time.monotonic()
    with pytest.raises(HTTPException) as error:
        await verifier.verify(token)
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_admin_mfa_uses_only_verified_claim_and_rejects_password_only_sessions():
    settings = Settings(_env_file=None, supabase_url="https://fixture.supabase.co", require_admin_mfa=True)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    token, public = signed_session(settings, user_metadata={"aal": "aal2"})
    verifier = JWTVerifier(settings)
    verifier.keys, verifier.cached_at = {"test-key": public}, time.monotonic()
    user = await verifier.verify(token)
    assert user.aal == "aal1"
    with pytest.raises(HTTPException) as error:
        await administrator(request, Workspace(uuid4(), user, "owner", user.id, "owner"))
    assert error.value.status_code == 403 and error.value.detail["code"] == "mfa_required"
    token, public = signed_session(settings, aal="aal2")
    verifier.keys = {"test-key": public}
    user = await verifier.verify(token)
    context = Workspace(uuid4(), user, "owner", user.id, "owner")
    assert await administrator(request, context) == context
    with pytest.raises(HTTPException) as error:
        salesperson = User(uuid4(), "fixture@example.test", "Fixture", "aal2")
        await administrator(request, Workspace(uuid4(), salesperson, "salesperson", salesperson.id, "salesperson"))
    assert error.value.detail["code"] == "administrator_required"


def test_configuration_validation_does_not_echo_secret_inputs():
    with pytest.raises(ValidationError) as error:
        deployed_settings(database_url="synthetic-secret-connection-not-valid")
    assert "synthetic-secret-connection" not in str(error.value)
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, supabase_url="https://fixture.supabase.co:synthetic-secret")
    assert "synthetic-secret" not in str(error.value)


@pytest.fixture
def storage():
    return Storage(Settings(_env_file=None, supabase_url="https://fixture.supabase.co", supabase_service_role_key="synthetic-service-credential"))


@pytest.mark.parametrize("suffix", ["/../other", "/./other", "//other", "/%2e%2e/other", "/other\\secret", "/other?token=secret"])
def test_privileged_storage_rejects_ambiguous_paths(storage, suffix):
    with pytest.raises(HTTPException):
        storage.path(f"{uuid4()}/{uuid4()}/{uuid4()}" + suffix)


@pytest.mark.asyncio
async def test_signed_upload_urls_are_same_origin_and_object_bound(storage, respx_mock):
    path = f"{uuid4()}/{uuid4()}/{uuid4()}/{uuid4()}/0.wav"
    endpoint = f"{storage.base}/object/upload/sign/{storage.path(path)}"
    route = respx_mock.post(endpoint)
    for url in ("https://attacker.example/object?token=secret", "//attacker.example/object?token=secret", "https://[invalid",
                f"/object/upload/sign/{storage.path(path)}-other?token=secret"):
        route.mock(return_value=httpx.Response(200, json={"url": url}))
        with pytest.raises(HTTPException) as error:
            await storage.authorize_upload(path)
        assert error.value.status_code == 503
    route.mock(return_value=httpx.Response(200, json={"url": f"/object/upload/sign/{storage.path(path)}?token=synthetic"}))
    result = await storage.authorize_upload(path)
    assert result["upload_url"] == f"{storage.base}/object/upload/sign/{storage.path(path)}?token=synthetic"


@pytest.mark.asyncio
async def test_storage_response_size_redirect_and_bad_json_fail_safely(storage, respx_mock):
    path = f"{uuid4()}/{uuid4()}/{uuid4()}/playback/audio.wav"
    route = respx_mock.post(f"{storage.base}/object/sign/{storage.path(path)}")
    for response in (httpx.Response(200, content=b"x" * 262145), httpx.Response(200, content=b"invalid"),
                     httpx.Response(302, headers={"Location": "https://attacker.example/"})):
        route.mock(return_value=response)
        with pytest.raises(HTTPException) as error:
            await storage.signed_read(path)
        assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_nonfinite_worker_metrics_and_provenance_are_rejected_before_database():
    for metric in (float("nan"), float("inf")):
        with pytest.raises(HTTPException) as error:
            await internal_routes.heartbeat(WorkerHeartbeat(ready=True, model_version="fixture", metrics={"memory_mb": metric}), None, {})
        assert error.value.detail["code"] == "invalid_worker_metric"
    for metadata in ({"duration": float("nan")}, {"additional_source_pass": "true"}, {"details": "x" * 8193}):
        payload = WorkerResult(lease_token=uuid4(), fencing_token=1, english_segments=[], diarization_status="unavailable", provenance=metadata)
        with pytest.raises(HTTPException) as error:
            await internal_routes.result(uuid4(), payload, None, {})
        assert error.value.detail["code"] == "invalid_worker_provenance"
