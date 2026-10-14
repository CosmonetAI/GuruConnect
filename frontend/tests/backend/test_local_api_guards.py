"""Local no-login mode remains loopback-only and never becomes production auth.

Only isolated temporary SQLite/audio data is used; no hosted or provider calls.
"""

import hashlib
import io
import os
import time
import wave
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from recorder.config import Settings
from recorder.local_store import LOCAL_WORKSPACE_ID, LocalStore
from recorder.main import create_app


def local_app(tmp_path, **overrides):
    return create_app(Settings(_env_file=None, environment="development", local_data_dir=tmp_path, **overrides), allow_local=True)


def client(app, peer="127.0.0.1"):
    return TestClient(app, base_url="http://127.0.0.1:8010", client=(peer, 45678))


def test_production_missing_configuration_never_enables_local_no_login(tmp_path):
    with pytest.raises(ValidationError, match="Staging/production requires"):
        create_app(Settings(_env_file=None, environment="production", local_data_dir=tmp_path), allow_local=True)
    assert not (tmp_path / "guruconnect.sqlite3").exists()


def test_development_factory_still_requires_explicit_local_opt_in(tmp_path):
    app = create_app(Settings(_env_file=None, environment="development", local_data_dir=tmp_path))
    assert not hasattr(app.state, "local_store")
    with TestClient(app) as http:
        assert http.get("/api/v1/enquiries").status_code == 401
        assert http.get("/ready").status_code == 503


def test_loopback_cli_and_allowed_local_origin_work_with_safe_response_headers(tmp_path):
    with client(local_app(tmp_path)) as http:
        assert http.get("/api/v1/workspaces").status_code == 200
        response = http.get("/api/v1/workspaces", headers={"Origin": "http://127.0.0.1:5176"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5176"
        assert response.json()["items"][0]["id"] == LOCAL_WORKSPACE_ID


@pytest.mark.parametrize("headers", [
    {"Host": "public.example.test:8010"}, {"Host": "127.0.0.1.public.example.test:8010"},
    {"Origin": "https://public.example.test"}, {"Origin": "null"}, {"Origin": "http://127.0.0.1:9999"},
])
def test_external_or_unexpected_browser_origins_and_rebinding_hosts_are_rejected(tmp_path, headers):
    with client(local_app(tmp_path)) as http:
        assert http.get("/api/v1/workspaces", headers=headers).status_code == 403


def test_even_configured_cloud_cors_origins_cannot_access_local_no_login_data(tmp_path):
    with client(local_app(tmp_path, cors_origins=["https://public.example.test"])) as http:
        assert http.get("/api/v1/workspaces", headers={"Origin": "https://public.example.test"}).status_code == 403


def test_remote_client_peer_is_rejected_even_with_loopback_host(tmp_path):
    with client(local_app(tmp_path), peer="192.0.2.17") as http:
        assert http.get("/api/v1/workspaces").status_code == 403


def test_json_and_streamed_request_bodies_are_bounded_before_validation(tmp_path):
    with client(local_app(tmp_path)) as http:
        assert http.post("/api/v1/enquiries", content=b"x" * 100001).status_code == 413
        def chunks():
            yield b"x" * 60000
            yield b"x" * 40001
        assert http.post("/api/v1/enquiries", content=chunks(), headers={"Transfer-Encoding": "chunked"}).status_code == 413
        assert http.put(f"/api/v1/local-audio/{uuid4()}/{uuid4()}/0", content=b"x" * 400001).status_code == 413


def test_validation_errors_do_not_echo_provider_credentials_or_submitted_values(tmp_path):
    with client(local_app(tmp_path)) as http:
        response = http.post("/api/v1/connections", json={"engine_id": "fixture-secret-invalid-engine", "credentials": {"api_key": "fixture-private-provider-key"}, "idempotency_key": "fixture-idem-key"})
        assert response.status_code == 422
        assert "fixture-secret-invalid-engine" not in response.text
        assert "fixture-private-provider-key" not in response.text


def wav(samples=1600):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x01\x00" * samples)
    return output.getvalue()


def test_signed_chunk_upload_is_reserved_scoped_and_immutable(tmp_path):
    app = local_app(tmp_path)
    with client(app) as http:
        created = http.post("/api/v1/conversations", headers={"Idempotency-Key": str(uuid4())}, json={"customer_name": "Synthetic customer", "consent_confirmed": True, "audio_only": True})
        assert created.status_code == 201, created.text
        conversation_id = created.json()["id"]
        prefix = f"/api/v1/conversations/{conversation_id}"
        leased = http.post(prefix + "/lease", json={"tab_id": "local-guard-test-tab"})
        assert leased.status_code == 200, leased.text
        lease = leased.json()
        data = wav()
        chunk = {"conversation_id": conversation_id, "part_id": lease["part_id"], "lease_token": lease["lease_token"], "sequence": 0,
                 "sample_count": 1600, "sample_rate": 16000, "start_sample": 0, "bytes": len(data), "mime": "audio/wav", "sha256": hashlib.sha256(data).hexdigest()}
        permit = http.post(prefix + "/chunks/authorize", json=chunk)
        assert permit.status_code == 200, permit.text
        url = permit.json()["upload_url"]
        assert http.put(url, content=data).status_code in {200, 201}
        assert http.put(url, content=data).status_code in {200, 201}
        altered = data[:-2] + b"\x02\x00"
        assert http.put(url, content=altered).status_code >= 400
        assert http.put(url.replace(conversation_id, str(uuid4())), content=data).status_code >= 400
        confirmed = http.post(prefix + "/chunks/confirm", json=chunk)
        assert confirmed.status_code == 200, confirmed.text
        assert app.state.local_store.chunk_path(conversation_id, lease["part_id"], 0).read_bytes() == data


def test_local_secrets_are_encrypted_with_private_files_and_generation_fences(tmp_path):
    store = LocalStore(tmp_path)
    first = store.configure_connection("elevenlabs", {"api_key": "fixture-first-private-key"}, {}, "first-idempotency", None)
    second = store.configure_connection("elevenlabs", {"api_key": "fixture-second-private-key"}, {}, "second-idempotency", first["generation"])
    with pytest.raises(ValueError, match="stale_connection_generation"):
        store.configure_connection("elevenlabs", {"api_key": "would-overwrite"}, {}, "third-idempotency", first["generation"])
    assert store.connection("elevenlabs")["generation"] == second["generation"]
    assert store.secrets("elevenlabs")["api_key"] == "fixture-second-private-key"
    assert store.configure_connection("elevenlabs", {"api_key": "fixture-second-private-key"}, {}, "second-idempotency", first["generation"])["generation"] == second["generation"]
    with store.transaction() as db:
        ciphertext = db.execute("select ciphertext from secrets where engine='elevenlabs'").fetchone()[0]
    assert "fixture-second-private-key" not in ciphertext
    if os.name == "posix":
        assert os.stat(store.root).st_mode & 0o777 == 0o700
        assert os.stat(store.database).st_mode & 0o777 == 0o600
        assert os.stat(store.root / "credentials.key").st_mode & 0o777 == 0o600


def test_local_signed_tokens_are_expiring_purpose_and_object_bound(tmp_path):
    store = LocalStore(tmp_path)
    token = store.token("upload", "reserved-object-and-sha", int(time.time()) + 60)
    assert store.verify_token(token, "upload", "reserved-object-and-sha")
    assert not store.verify_token(token, "playback", "reserved-object-and-sha")
    assert not store.verify_token(token, "upload", "another-object")
    assert not store.verify_token(store.token("upload", "reserved-object-and-sha", int(time.time()) - 1), "upload", "reserved-object-and-sha")
    with pytest.raises(ValueError):
        store.chunk_path("../outside-private-store", str(uuid4()), 0)


def test_local_recording_lease_cannot_take_over_another_active_tab(tmp_path):
    app = local_app(tmp_path)
    with client(app) as http:
        created = http.post("/api/v1/conversations", headers={"Idempotency-Key": str(uuid4())}, json={"customer_name": "Synthetic customer", "consent_confirmed": True, "audio_only": True}).json()
        path = f"/api/v1/conversations/{created['id']}/lease"
        first = http.post(path, json={"tab_id": "first-recording-tab-unique"})
        assert first.status_code == 200, first.text
        stolen = http.post(path, json={"tab_id": "second-recording-tab-unique", "takeover": True})
        assert stolen.status_code == 409
        app.state.local_store.patch("conversations", created["id"], {"lease_until": time.time() - 1})
        recovered = http.post(path, json={"tab_id": "second-recording-tab-unique", "takeover": True, "purpose": "recover"})
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["lease_token"] != first.json()["lease_token"]
        stale = http.post(f"/api/v1/conversations/{created['id']}/lease/heartbeat", json={"tab_id": "first-recording-tab-unique", "lease_token": first.json()["lease_token"]})
        assert stale.status_code == 409
