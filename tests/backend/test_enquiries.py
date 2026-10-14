"""GuruConnect enquiry durability, ownership and retry regressions.

Native PostgreSQL tests use disposable database state and emulated private
Storage. They never invoke transcription/translation providers or hosted data.
"""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from recorder.config import Settings
from recorder.enquiry_routes import EnquiryCreate
from recorder.main import create_app

# Reuse the existing isolated Auth/Storage fixture rather than introducing a
# second application database or credentials into enquiry tests.
from test_api_native import new, reserve

pytest_plugins = ["test_api_native"]


def enquiry(**changes):
    fields = {
        "customerName": "Audit Customer", "phone": "+91 9876543210", "email": "audit@example.test",
        "channel": "Incoming Call", "branch": "Mumbai", "assignedExecutive": "Test Salesperson",
        "customerType": "Project", "company": "Example Company", "projectName": "Site One",
        "preferredContactMethod": "Phone", "notes": "Combined note", "customerNotes": "Customer detail",
        "salespersonNotes": "Sales detail", "requirementsSummary": "Requirement detail",
        "followUpDate": "2026-11-30", "followUpOwner": "Test Salesperson", "priority": "High",
        "productRequirements": [{"product": "", "sku": "SKU-123", "quantity": 3, "unit": "nos",
                                 "customerSegment": "Project", "budget": "10000", "paymentPreference": "Card",
                                 "timeline": "Next week", "stockCheck": True, "notes": "Keep SKU-only row"}],
    }
    return {**fields, **changes}


def save(client, headers, fields=None, key=None):
    return client.post("/api/v1/enquiries", json=fields or enquiry(),
                       headers={**headers, "Idempotency-Key": key or str(uuid4())})


@pytest.mark.parametrize("changes", [
    {"owner_id": str(uuid4())}, {"id": str(uuid4())}, {"createdAt": "2026-01-01"},
    {"customerName": "A"}, {"customerName": "x" * 201}, {"phone": "123"}, {"phone": "letters123456789"},
    {"email": "bad-email"}, {"followUpDate": "not-a-date"}, {"followUpDate": "2026-02-30"},
    {"quantity": True}, {"quantity": 0}, {"quantity": "1"}, {"notes": "x" * 10001},
    {"productRequirements": [{"product": "", "sku": "", "notes": "Must not vanish silently"}]},
    {"productRequirements": [{"sku": "SKU", "quantity": -1}]},
    {"productRequirements": [{"sku": "SKU", "stockCheck": "true"}]},
    {"productRequirements": [{"sku": "SKU"}] * 26},
])
def test_enquiry_validation_bounds_and_rejects_client_owned_fields(changes):
    with pytest.raises(ValidationError):
        EnquiryCreate.model_validate(enquiry(**changes))


def test_sku_only_requirements_are_kept_and_optional_empty_email_date_are_valid():
    parsed = EnquiryCreate.model_validate(enquiry(email="", followUpDate=""))
    assert parsed.productRequirements[0].sku == "SKU-123"
    assert parsed.productRequirements[0].quantity == 3
    assert parsed.productRequirements[0].notes == "Keep SKU-only row"


def test_enquiry_endpoints_require_authentication():
    with TestClient(create_app(Settings(_env_file=None))) as client:
        assert client.get("/api/v1/enquiries").status_code == 401
        assert client.get(f"/api/v1/enquiries/{uuid4()}").status_code == 401
        assert client.post("/api/v1/enquiries", json=enquiry(), headers={"Idempotency-Key": str(uuid4())}).status_code == 401


def test_save_is_durable_idempotent_and_preserves_entire_form(api):
    client, headers, _, _, database_url = api
    key = str(uuid4())
    first = save(client, headers, key=key)
    assert first.status_code == 201, first.text
    item = first.json()
    assert item["enquiryNumber"].startswith("ENQ-")
    assert item["conversationId"] is None and item["recording"] is None
    assert item["productRequirements"][0]["sku"] == "SKU-123"
    assert item["customerNotes"] == "Customer detail"
    assert item["salespersonNotes"] == "Sales detail"
    assert item["requirementsSummary"] == "Requirement detail"
    assert save(client, headers, key=key).status_code == 200
    conflict = save(client, headers, enquiry(customerName="Changed customer"), key=key)
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "idempotency_conflict"
    assert client.post("/api/v1/enquiries", headers=headers, json=enquiry()).status_code == 400
    reread = client.get(f"/api/v1/enquiries/{item['id']}", headers=headers)
    assert reread.status_code == 200 and reread.json() == item
    with psycopg.connect(database_url) as connection:
        rows = connection.execute("select payload from guruconnect_enquiries where organisation_id=%s", (headers["X-Workspace-ID"],)).fetchall()
        assert len(rows) == 1 and rows[0][0]["projectName"] == "Site One"
        audit = connection.execute("select metadata from audit_events where organisation_id=%s and action='enquiry.created'", (headers["X-Workspace-ID"],)).fetchone()[0]
        assert audit == {"enquiry_id": item["id"], "has_recording": False}


def test_pagination_and_literal_search_filters(api):
    client, headers, _, _, _ = api
    a = save(client, headers, enquiry(customerName="Literal 50%_ customer")).json()
    b = save(client, headers, enquiry(customerName="Different", branch="Delhi", channel="Email")).json()
    page = client.get("/api/v1/enquiries", headers=headers, params={"pageSize": 1}).json()
    assert page["total"] == 2 and page["totalPages"] == 2 and page["items"][0]["id"] == b["id"]
    last = client.get("/api/v1/enquiries", headers=headers, params={"pageSize": 1, "page": 3}).json()
    assert last["items"] == [] and last["total"] == 2
    found = client.get("/api/v1/enquiries", headers=headers, params={"query": "50%_"}).json()
    assert [item["id"] for item in found["items"]] == [a["id"]]
    assert client.get("/api/v1/enquiries", headers=headers, params={"query": a["enquiryNumber"]}).json()["total"] == 1
    assert client.get("/api/v1/enquiries", headers=headers, params={"branch": "Delhi", "channel": "Email"}).json()["total"] == 1
    assert client.get("/api/v1/enquiries", headers=headers, params={"query": "' OR true --"}).json()["total"] == 0
    assert client.get("/api/v1/enquiries", headers=headers, params={"pageSize": 101}).status_code == 422


def test_salesperson_workspace_isolation_admin_read_and_revocation(api):
    client, headers, ids, _, _ = api
    item = save(client, headers).json()
    other = {**headers, "X-Test-User": "2"}
    assert client.get(f"/api/v1/enquiries/{item['id']}", headers=other).status_code == 404
    assert client.get("/api/v1/enquiries", headers=other).json()["items"] == []
    admin = {**headers, "X-Test-User": "0"}
    assert client.get(f"/api/v1/enquiries/{item['id']}", headers=admin).status_code == 200
    foreign_org = client.post("/api/v1/workspaces", headers=other, json={"name": "Different workspace"}).json()["id"]
    foreign_headers = {**other, "X-Workspace-ID": foreign_org}
    assert client.get(f"/api/v1/enquiries/{item['id']}", headers=foreign_headers).status_code == 404
    assert client.delete(f"/api/v1/team/{ids[1]}", headers=admin).status_code == 200
    assert client.get(f"/api/v1/enquiries/{item['id']}", headers=headers).status_code == 403
    assert save(client, headers).status_code == 403
    assert client.get(f"/api/v1/enquiries/{item['id']}", headers=admin).status_code == 200


def finalized_recording(client, headers, blobs):
    conv = new(client, headers)
    prefix = f"/api/v1/conversations/{conv['id']}"
    lease = client.post(prefix + "/lease", headers=headers, json={"tab_id": "enquiry-attachment-test"}).json()
    chunk, url = reserve(client, headers, conv, lease, blobs)
    assert client.post(url + "/confirm", headers=headers, json=chunk).status_code == 200
    assert client.post(prefix + "/events", headers=headers, json={"part_id": lease["part_id"], "lease_token": lease["lease_token"], "kind": "stopped", "sample": 80000}).status_code == 200
    response = client.post(prefix + "/finalize", headers=headers,
                           json={"part_id": lease["part_id"], "lease_token": lease["lease_token"], "expected_chunks": 1, "total_samples": 80000})
    assert response.status_code == 200, response.text
    return conv


def test_recording_links_require_owner_saved_audio_and_do_not_create_paid_jobs(api):
    client, headers, _, blobs, database_url = api
    incomplete = new(client, headers)
    assert save(client, headers, enquiry(conversationId=incomplete["id"])).json()["detail"]["code"] == "recording_not_saved"
    conv = finalized_recording(client, headers, blobs)
    with psycopg.connect(database_url) as connection:
        job_count = connection.execute("select count(*) from processing_jobs where conversation_id=%s", (conv["id"],)).fetchone()[0]
    other = {**headers, "X-Test-User": "2"}
    assert save(client, other, enquiry(conversationId=conv["id"])).status_code == 404
    assert save(client, {**headers, "X-Test-User": "0"}, enquiry(conversationId=conv["id"])).status_code == 403
    key = str(uuid4())
    response = save(client, headers, enquiry(conversationId=conv["id"]), key=key)
    assert response.status_code == 201, response.text
    attached = response.json()
    assert attached["conversationId"] == conv["id"] and attached["recording"]["uploadState"] == "saved"
    assert attached["recording"]["captureState"] == "stopped"
    assert save(client, headers, enquiry(conversationId=conv["id"]), key=key).status_code == 200
    duplicate = save(client, headers, enquiry(conversationId=conv["id"]))
    assert duplicate.status_code == 409 and duplicate.json()["detail"]["code"] == "recording_already_linked"
    with psycopg.connect(database_url) as connection:
        assert connection.execute("select count(*) from processing_jobs where conversation_id=%s", (conv["id"],)).fetchone()[0] == job_count


def test_concurrent_same_save_and_same_recording_link_are_unique(api):
    client, headers, _, blobs, database_url = api
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as executor:
        replies = list(executor.map(lambda _: save(client, headers, key=key), range(4)))
    assert sorted(reply.status_code for reply in replies) == [200, 200, 200, 201]
    assert len({reply.json()["id"] for reply in replies}) == 1
    conv = finalized_recording(client, headers, blobs)
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(lambda _: save(client, headers, enquiry(conversationId=conv["id"])), range(2)))
    assert sorted(reply.status_code for reply in replies) == [201, 409]
    with psycopg.connect(database_url) as connection:
        assert connection.execute("select count(*) from guruconnect_enquiries where conversation_id=%s", (conv["id"],)).fetchone()[0] == 1


def test_table_rest_access_denied_and_recording_foreign_key_enforces_owner(api):
    client, headers, ids, blobs, database_url = api
    conv = finalized_recording(client, headers, blobs)
    saved = save(client, headers, enquiry(conversationId=conv["id"])).json()
    with psycopg.connect(database_url) as connection, pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
        connection.execute("set local role authenticated")
        connection.execute("select * from guruconnect_enquiries")
    with psycopg.connect(database_url) as connection, pytest.raises(psycopg.errors.ForeignKeyViolation):
        connection.execute("update guruconnect_enquiries set owner_id=%s where id=%s", (ids[2], saved["id"]))


def test_missing_enquiry_migration_is_actionable_and_never_demo_fallback(api):
    client, headers, _, _, database_url = api
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("alter table guruconnect_enquiries rename to audit_missing_enquiries")
    try:
        response = client.get("/api/v1/enquiries", headers=headers)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "database_schema_missing"
        assert "migration" in response.json()["detail"]["message"].lower()
        assert client.get("/ready").status_code == 503
    finally:
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute("alter table audit_missing_enquiries rename to guruconnect_enquiries")


def test_connection_generation_prevents_stale_credential_overwrite(api):
    client, headers, _, _, database_url = api
    admin = {**headers, "X-Test-User": "0"}
    old = next(item for item in client.get("/api/v1/connections", headers=admin).json()["items"] if item["engine_id"] == "openai-translator")
    fields = {"engine_id": "openai-translator", "credentials": {"api_key": "fixture-provider-key"},
              "config": {"model": "gpt-5.6-luna"}, "expected_generation": old["generation"], "idempotency_key": str(uuid4())}
    first = client.post("/api/v1/connections", headers=admin, json=fields)
    assert first.status_code == 201, first.text
    assert first.json()["generation"] > old["generation"]
    # Same-key replay succeeds even after generation changes during first save.
    assert client.post("/api/v1/connections", headers=admin, json=fields).json()["generation"] == first.json()["generation"]
    stale = client.post("/api/v1/connections", headers=admin,
                        json={**fields, "credentials": {"api_key": "would-overwrite"}, "idempotency_key": str(uuid4())})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_connection_generation"
    assert "would-overwrite" not in stale.text and "fixture-provider-key" not in stale.text
    with psycopg.connect(database_url) as connection:
        assert connection.execute("select generation from engine_connections where id=%s", (old["id"],)).fetchone()[0] == first.json()["generation"]


def test_conversation_create_replay_keeps_same_customer_run_and_does_not_require_live_provider(api):
    client, headers, _, _, database_url = api
    key = str(uuid4())
    fields = {"customer_name": "Synthetic recording customer", "consent_confirmed": True}
    first = client.post("/api/v1/conversations", headers={**headers, "Idempotency-Key": key}, json=fields)
    assert first.status_code == 201, first.text
    created = first.json()
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("update engine_connections set state='disconnected',tested_at=null where organisation_id=%s and engine_id='elevenlabs'", (headers["X-Workspace-ID"],))
    replay = client.post("/api/v1/conversations", headers={**headers, "Idempotency-Key": key}, json=fields)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == created["id"]
    assert replay.json()["customer_id"] == created["customer_id"]
    assert replay.json()["runs"][0]["id"] == created["runs"][0]["id"]
    assert "request_hash" not in replay.json() and "idempotency_key" not in {field for field in replay.json() if field != "runs"}
    changed = client.post("/api/v1/conversations", headers={**headers, "Idempotency-Key": key}, json={**fields, "title": "Changed recording intent"})
    assert changed.status_code == 409 and changed.json()["detail"]["code"] == "idempotency_conflict"
    with psycopg.connect(database_url) as connection:
        assert connection.execute("select count(*) from conversations where organisation_id=%s", (headers["X-Workspace-ID"],)).fetchone()[0] == 1
        assert connection.execute("select count(*) from customers where organisation_id=%s", (headers["X-Workspace-ID"],)).fetchone()[0] == 1
        assert connection.execute("select count(*) from processing_runs where conversation_id=%s", (created["id"],)).fetchone()[0] == 1


def test_conversation_create_concurrent_retries_are_scoped_by_user_and_workspace(api):
    client, headers, _, _, database_url = api
    key = str(uuid4())
    fields = {"customer_name": "Synthetic recording customer", "consent_confirmed": True}
    def send(_):
        return client.post("/api/v1/conversations", headers={**headers, "Idempotency-Key": key}, json=fields)
    with ThreadPoolExecutor(max_workers=4) as executor:
        replies = list(executor.map(send, range(4)))
    assert sorted(reply.status_code for reply in replies) == [200, 200, 200, 201]
    assert len({reply.json()["id"] for reply in replies}) == 1
    other = client.post("/api/v1/conversations", headers={**headers, "X-Test-User": "2", "Idempotency-Key": key}, json=fields)
    assert other.status_code == 201 and other.json()["id"] != replies[0].json()["id"]
    foreign_workspace = client.post("/api/v1/workspaces", headers={**headers, "X-Test-User": "2"}, json={"name": "Other recording workspace"}).json()["id"]
    foreign = client.post("/api/v1/conversations", headers={**headers, "X-Test-User": "2", "X-Workspace-ID": foreign_workspace, "Idempotency-Key": key}, json={**fields, "audio_only": True})
    assert foreign.status_code == 201 and foreign.json()["id"] != other.json()["id"]
    with psycopg.connect(database_url) as connection:
        assert connection.execute("select count(*) from recorder_private.conversation_requests where idempotency_key=%s", (key,)).fetchone()[0] == 3
    with psycopg.connect(database_url) as connection, pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
        connection.execute("set local role authenticated")
        connection.execute("select * from recorder_private.conversation_requests")


@pytest.mark.parametrize("key", ["short", "fixture secret", "x" * 129])
def test_conversation_create_rejects_malformed_opaque_key_without_echoing_it(api, key):
    client, headers, _, _, _ = api
    response = client.post("/api/v1/conversations", headers={**headers, "Idempotency-Key": key}, json={"customer_name": "Synthetic customer", "consent_confirmed": True})
    assert response.status_code == 400 and response.json()["detail"]["code"] == "invalid_idempotency_key"
    assert key not in response.text


def test_conversation_create_replay_cannot_bypass_membership_revocation(api):
    client, headers, ids, _, _ = api
    key = str(uuid4())
    fields = {"customer_name": "Synthetic recording customer", "consent_confirmed": True}
    assert client.post("/api/v1/conversations", headers={**headers, "Idempotency-Key": key}, json=fields).status_code == 201
    assert client.delete(f"/api/v1/team/{ids[1]}", headers={**headers, "X-Test-User": "0"}).status_code == 200
    assert client.post("/api/v1/conversations", headers={**headers, "Idempotency-Key": key}, json=fields).status_code == 403
