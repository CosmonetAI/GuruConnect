"""Independent localhost persistence and exact recording API contracts."""
import hashlib
import json
import os
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from recorder.audio import pcm_to_wav
from recorder.config import Settings
from recorder.local_api import create_local_app
from recorder.local_store import LOCAL_WORKSPACE_ID, LocalStore


@pytest.fixture
def api(tmp_path):
    settings = Settings(_env_file=None, environment="development", local_data_dir=tmp_path, cors_origins=["http://127.0.0.1:5176"], database_url=None, supabase_url="", supabase_service_role_key=None)
    app = create_local_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8010", client=("127.0.0.1", 12345), headers={"Origin": "http://127.0.0.1:5176", "X-Workspace-ID": LOCAL_WORKSPACE_ID}) as client:
        yield client, app.state.local_store, settings


def create_recording(client):
    response = client.post("/api/v1/conversations", headers={"Idempotency-Key": str(uuid4())}, json={"customer_name": "Synthetic customer", "consent_confirmed": True, "language_hint": "auto", "audio_only": False})
    assert response.status_code == 201, response.text
    cid = response.json()["id"]
    response = client.post(f"/api/v1/conversations/{cid}/lease", json={"tab_id": "synthetic-tab-id-00001"})
    assert response.status_code == 200, response.text
    return cid, response.json()


def save_audio(client, cid, lease, sequence=0, start=0, samples=160):
    data = pcm_to_wav(b"\0\0" * samples)
    meta = {"part_id": lease["part_id"], "lease_token": lease["lease_token"], "sequence": sequence, "sample_count": samples, "sample_rate": 16000, "start_sample": start, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "mime": "audio/wav"}
    permitted = client.post(f"/api/v1/conversations/{cid}/chunks/authorize", json=meta)
    assert permitted.status_code == 200, permitted.text
    upload = client.put(permitted.json()["upload_url"], content=data, headers={"Content-Type": "audio/wav"})
    assert upload.status_code == 200, upload.text
    confirmed = client.post(f"/api/v1/conversations/{cid}/chunks/confirm", json=meta)
    assert confirmed.status_code == 200, confirmed.text
    return meta, data


def enquiry(conversation_id=None):
    return {"customerName": "Synthetic customer", "phone": "9876543210", "channel": "Walk-in", "branch": "Local branch", "assignedExecutive": "Local user", "productRequirements": [{"product": "", "sku": "SKU-42", "quantity": 3}], "conversationId": conversation_id}


def test_startup_needs_no_supabase_and_reports_local_mode(api):
    client, _, _ = api
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["mode"] == "local"
    assert response.headers["X-GuruConnect-Mode"] == "local"
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5176"
    assert "X-GuruConnect-Mode" in response.headers["access-control-expose-headers"]
    assert client.get("/api/v1/workspaces").json()["items"][0]["role"] == "owner"
    assert client.get("/api/v1/engine-settings").json()["default_engine_id"] == "elevenlabs"


def test_enquiries_persist_across_app_restarts_and_save_replays_are_stable(api):
    client, _, settings = api
    key = str(uuid4())
    first = client.post("/api/v1/enquiries", json=enquiry(), headers={"Idempotency-Key": key})
    again = client.post("/api/v1/enquiries", json=enquiry(), headers={"Idempotency-Key": key})
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    assert first.json()["productRequirements"][0]["sku"] == "SKU-42"
    with TestClient(create_local_app(settings), base_url="http://127.0.0.1:8010", client=("127.0.0.1", 12345)) as restarted:
        assert restarted.get("/api/v1/enquiries").json()["total"] == 1
        assert restarted.get(f"/api/v1/enquiries/{first.json()['id']}").status_code == 200
    changed = client.post("/api/v1/enquiries", json={**enquiry(), "customerName": "Changed customer"}, headers={"Idempotency-Key": key})
    assert changed.status_code == 409


def test_consent_idempotency_and_enquiry_audio_completion_are_enforced(api):
    client, _, _ = api
    bad = client.post("/api/v1/conversations", headers={"Idempotency-Key": str(uuid4())}, json={"customer_name": "Synthetic customer", "consent_confirmed": False})
    assert bad.status_code == 422
    cid, lease = create_recording(client)
    assert client.post("/api/v1/enquiries", json=enquiry(cid), headers={"Idempotency-Key": str(uuid4())}).status_code == 409
    meta, _ = save_audio(client, cid, lease)
    final = client.post(f"/api/v1/conversations/{cid}/finalize", json={"part_id": lease["part_id"], "lease_token": lease["lease_token"], "expected_chunks": 1, "total_samples": meta["sample_count"]})
    assert final.status_code == 200
    assert client.post("/api/v1/enquiries", json=enquiry(cid), headers={"Idempotency-Key": str(uuid4())}).status_code == 201
    assert client.post("/api/v1/enquiries", json=enquiry(cid), headers={"Idempotency-Key": str(uuid4())}).status_code == 409


def test_signed_upload_is_bound_to_chunk_hash_and_rejects_changed_audio(api):
    client, _, _ = api
    cid, lease = create_recording(client)
    meta, data = save_audio(client, cid, lease)
    authorized = client.post(f"/api/v1/conversations/{cid}/chunks/authorize", json=meta).json()
    assert authorized["confirmed"] is True
    changed = client.post(f"/api/v1/conversations/{cid}/chunks/authorize", json={**meta, "sha256": "f" * 64})
    assert changed.status_code == 409
    unsigned = client.put(f"/api/v1/local-audio/{cid}/{lease['part_id']}/0?token=invalid", content=data)
    assert unsigned.status_code == 403


def test_finalization_requires_contiguous_confirmed_audio_and_schedules_once(api):
    client, store, _ = api
    cid, lease = create_recording(client)
    save_audio(client, cid, lease, sequence=1, start=160)
    manifest = {"part_id": lease["part_id"], "lease_token": lease["lease_token"], "expected_chunks": 2, "total_samples": 320}
    assert client.post(f"/api/v1/conversations/{cid}/finalize", json=manifest).status_code == 409
    save_audio(client, cid, lease)
    first = client.post(f"/api/v1/conversations/{cid}/finalize", json=manifest)
    again = client.post(f"/api/v1/conversations/{cid}/finalize", json=manifest)
    assert first.status_code == again.status_code == 200
    assert len(store.list("jobs", {"conversation_id": cid})) == 1
    progress = client.get(f"/api/v1/conversations/{cid}/progress").json()
    assert progress["conversation"]["upload_state"] == "saved"
    assert progress["jobs"][0]["kind"] == "assemble"
    assert progress["jobs"][0]["state"] == "queued"
    assert progress["chunks"]["saved_samples"] == 320
    assert client.get(f"/api/v1/conversations/{cid}/transcript").json()["version"] is None


def test_provider_keys_are_encrypted_private_and_omitted_from_all_responses(api):
    client, store, settings = api
    secret = "dummy-provider-key-for-unit-tests"
    body = {"engine_id": "openai-translator", "credentials": {"api_key": secret}, "config": {"model": "gpt-5.6-luna"}, "idempotency_key": str(uuid4())}
    first = client.post("/api/v1/connections", json=body)
    again = client.post("/api/v1/connections", json=body)
    assert first.status_code == again.status_code == 201
    assert first.json()["generation"] == again.json()["generation"] == 1
    assert secret not in first.text + client.get("/api/v1/connections").text
    assert secret.encode() not in store.database.read_bytes()
    assert store.secrets("openai-translator")["api_key"] == secret
    assert os.stat(settings.local_data_dir / "credentials.key").st_mode & 0o777 == 0o600
    assert os.stat(settings.local_data_dir).st_mode & 0o777 == 0o700


def test_connection_rotations_reject_stale_generation_without_replacing_key(api):
    client, store, _ = api
    body = {"engine_id": "elevenlabs", "credentials": {"api_key": "dummy-first-provider-key"}, "config": {}, "idempotency_key": str(uuid4())}
    assert client.post("/api/v1/connections", json=body).status_code == 201
    changed = {**body, "credentials": {"api_key": "dummy-second-provider-key"}, "idempotency_key": str(uuid4()), "expected_generation": 1}
    assert client.post("/api/v1/connections", json=changed).status_code == 201
    stale = {**changed, "credentials": {"api_key": "dummy-stale-provider-key"}, "idempotency_key": str(uuid4())}
    assert client.post("/api/v1/connections", json=stale).status_code == 409
    assert store.secrets("elevenlabs")["api_key"] == "dummy-second-provider-key"


def test_translation_readiness_requires_the_explicit_functional_test(api, monkeypatch):
    client, _, _ = api
    async def fake_test(_self, functional=False): return {"connected": True, "functional": functional}
    monkeypatch.setattr("recorder.local_api.OpenAITranslator.test", fake_test)
    row = client.post("/api/v1/connections", json={"engine_id": "openai-translator", "credentials": {"api_key": "dummy-key-for-tests"}, "config": {"model": "gpt-5.6-luna"}, "idempotency_key": str(uuid4())}).json()
    base = f"/api/v1/connections/{row['id']}/test"
    metadata = client.post(base, json={"functional": False}).json()
    assert metadata["passed"] is True and metadata["state"] == "configured"
    assert metadata["tested_at"] is None
    smoke = client.post(base, json={"functional": True}).json()
    assert smoke["state"] == "connected" and smoke["tested_at"]


def test_failed_translation_retry_uses_retained_source_and_never_requeues_speech(api):
    client, store, _ = api
    cid, _ = create_recording(client)
    original = store.enqueue_job(cid, "transcribe", {"run_id": "run-a"})
    spoken = store.claim_job("worker-test"); store.complete_job(spoken["id"], lease_token=spoken["lease_token"])
    translated = store.enqueue_job(cid, "translate", {"run_id": "run-a"})
    job = store.claim_job("worker-test"); store.fail_job(job["id"], "blocked", "translator_unconfigured", lease_token=job["lease_token"])
    response = client.post(f"/api/v1/conversations/{cid}/retry", json={"stage": "translation"}).json()
    assert response["resumed_job_ids"] == [translated["id"]]
    assert store.get("jobs", original["id"])["state"] == "succeeded"


def test_accepted_expired_provider_job_is_uncertain_and_cannot_be_retried(api):
    client, store, _ = api
    cid, _ = create_recording(client)
    created = store.enqueue_job(cid, "translate", {"run_id": "run-a"})
    job = store.claim_job("worker-a")
    store.mark_job_accepted(job["id"], lease_token=job["lease_token"])
    store.patch("jobs", job["id"], {"lease_until": time.time() - 1})
    assert store.claim_job("worker-b") is None
    assert store.get("jobs", created["id"])["state"] == "uncertain"
    assert store.get("conversations", cid)["english_state"] == "uncertain"
    response = client.post(f"/api/v1/conversations/{cid}/retry", json={"stage": "translation"}).json()
    assert response["resumed_job_ids"] == [] and response["uncertain_job_ids"] == [job["id"]]
    with pytest.raises(ValueError): store.complete_job(job["id"], lease_token=job["lease_token"])


def test_playback_has_short_lived_permission_and_preserves_source_only_export(api):
    client, store, _ = api
    cid, _ = create_recording(client)
    assert client.get(f"/api/v1/conversations/{cid}/playback").status_code == 409
    path = store.audio_path(cid); path.parent.mkdir(parents=True); path.write_bytes(pcm_to_wav(b"\0\0" * 160))
    result = client.get(f"/api/v1/conversations/{cid}/playback").json()
    assert client.get(result["url"]).status_code == 200
    assert client.get(f"/api/v1/local-playback/{cid}?token=invalid").status_code == 403
    version = {"id": str(uuid4()), "run_id": str(uuid4()), "english_state": "pending", "speaker_state": "complete"}
    turn = {"id": str(uuid4()), "speaker_ref": "voice-a", "source_text": "मॉडल SKU-42 भेजें।", "english_text": None, "start_seconds": 0, "end_seconds": 1}
    store.replace_turns(cid, [turn], version)
    response = client.get(f"/api/v1/conversations/{cid}/export?format=txt")
    assert "[Original; English pending]" in response.text
    assert "SKU-42" in response.text


def test_manual_corrections_preserve_original_and_are_blocked_during_processing(api):
    client, store, _ = api
    cid, _ = create_recording(client)
    version = {"id": str(uuid4()), "run_id": str(uuid4()), "english_state": "complete", "speaker_state": "complete"}
    turn = {"id": str(uuid4()), "speaker_ref": "voice-a", "source_text": "नमस्ते", "english_text": "Hello", "start_seconds": 0, "end_seconds": 1}
    store.replace_turns(cid, [turn], version)
    body = {"version_id": version["id"], "turn_id": turn["id"], "english_text": "Hello there"}
    assert client.post(f"/api/v1/conversations/{cid}/revisions", json=body).status_code == 201
    saved = store.get_transcript(cid)
    assert saved["turns"][0]["original_english_text"] == "Hello"
    assert saved["turns"][0]["english_text"] == "Hello there"
    assert len(saved["revisions"]) == 1
    store.enqueue_job(cid, "translate", {"run_id": version["run_id"]})
    assert client.post(f"/api/v1/conversations/{cid}/revisions", json=body).status_code == 409


def test_private_loopback_origins_and_validation_redaction(api):
    client, _, _ = api
    assert client.get("/health/ready", headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.get("/health/ready", headers={"Host": "attacker.example"}).status_code == 403
    response = client.post("/api/v1/connections", json={"engine_id": "elevenlabs", "credentials": {"api_key": ["dummy-sensitive-key"]}, "config": {}, "idempotency_key": str(uuid4())})
    assert response.status_code == 422
    assert "dummy-sensitive-key" not in response.text
    assert "input" not in response.text
