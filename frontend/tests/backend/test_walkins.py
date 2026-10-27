"""Native workspace isolation and transactional walk-in regressions.

Disposable PostgreSQL/Auth/Storage fixtures; no hosted or paid provider access.
"""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from pydantic import ValidationError
from recorder.walkin_routes import Requirement, WalkinCreate
from test_api_native import new, reserve
from test_enquiries import enquiry, finalized_recording, save

pytest_plugins = ["test_api_native"]


@pytest.fixture(autouse=True)
def deterministic_assignee(request):
    """Set fixture availability explicitly instead of relying on random ties."""
    if 'api' not in request.fixturenames:
        return
    _, _, ids, _, database_url = request.getfixturevalue('api')
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("update profiles set availability_status='be_right_back' where id=any(%s)", ([ids[0], ids[2]],))


def register(client, headers, *, key=None, **changes):
    return client.post("/api/v1/walkins", headers={**headers, "Idempotency-Key": key or str(uuid4())}, json={"customerName": "Walkin Customer", "mobileNumber": "+91 9876543210", **changes})


def change(client, headers, visit, status):
    response = client.patch(f"/api/v1/walkins/{visit['id']}/status", headers=headers, json={"status": status, "expectedUpdatedAt": visit["updatedAt"]})
    assert response.status_code == 200, response.text
    return response.json()


def reviewed(client, headers):
    visit = register(client, headers).json()
    visit = change(client, headers, visit, "In Discussion")
    response = client.put(f"/api/v1/walkins/{visit['id']}/requirements", headers=headers, json={"expectedUpdatedAt": visit["updatedAt"], "requirement": {"detailedRequirement": "Reviewed fixtures", "productOrService": "Fixture product"}})
    assert response.status_code == 200, response.text
    visit = client.get(f"/api/v1/walkins/{visit['id']}", headers=headers).json()
    assert visit["status"] == "Requirements Captured"
    return visit


@pytest.mark.parametrize("changes", [{"organisation_id": str(uuid4())}, {"registeredBy": str(uuid4())}, {"assignedAgentId": str(uuid4())}, {"status": "Completed"}, {"customerName": "x"}, {"mobileNumber": "bad-number"}, {"email": "invalid"}, {"internalNotes": "x" * 4001}, {"customerName": "Null\x00customer"}, {"internalNotes": "\ud800"}])
def test_walkin_server_owned_fields_and_bounds(changes):
    with pytest.raises(ValidationError):
        WalkinCreate.model_validate({"customerName": "Fixture", "mobileNumber": "+919876543210", **changes})


@pytest.mark.parametrize("changes", [{"followUpAt": "2026-02-30"}, {"followUpAt": "30/11/2026"}, {"detailedRequirement": "x" * 10001}, {"updatedAt": "2026-01-01"}, {"agentNotes": "\ud800"}, {"detailedRequirement": "\U0001f600" * 10000, "agentNotes": "\U0001f600" * 3000}])
def test_requirement_bounds_and_dates(changes):
    with pytest.raises(ValidationError):
        Requirement.model_validate(changes)


def test_registration_retry_is_scoped_and_audited_without_pii(api):
    client, headers, ids, _, database_url = api
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as executor:
        replies = list(executor.map(lambda _: register(client, headers, key=key), range(4)))
    assert sorted(reply.status_code for reply in replies) == [200, 200, 200, 201]
    assert len({reply.json()["id"] for reply in replies}) == 1
    item = replies[0].json()
    assert item["assignedAgentId"] == str(ids[1]) and item["registeredBy"] == str(ids[1])
    assert item["status"] == "Assigned" and item["visitNumber"].startswith("WI-")
    assert "idempotency_key" not in item and "payload_hash" not in item
    assert register(client, headers, key=key, customerName="Changed").status_code == 409
    other = register(client, {**headers, "X-Test-User": "2"}, key=key).json()
    assert other["id"] != item["id"]
    with psycopg.connect(database_url) as connection:
        events = connection.execute("select metadata from audit_events where organisation_id=%s and action='walkin.registered'", (headers["X-Workspace-ID"],)).fetchall()
        assert len(events) == 2
        assert events[0][0].keys() == {"visit_id"}


def test_workspace_and_salesperson_isolation_admin_assignment_and_revocation(api):
    client, headers, ids, _, _ = api
    key = str(uuid4())
    visit = register(client, headers, key=key).json()
    url = f"/api/v1/walkins/{visit['id']}"
    other = {**headers, "X-Test-User": "2"}
    admin = {**headers, "X-Test-User": "0"}
    for path in (url, url + "/requirements", url + "/recordings"):
        assert client.get(path, headers=other).status_code == 404
    assert client.get("/api/v1/walkins", headers=other).json()["items"] == []
    assert client.get("/api/v1/walkins/history", headers=other).json()["items"] == []
    assert client.get(url, headers=admin).status_code == 200
    fields = {"agentId": str(ids[2]), "note": "Handoff", "expectedUpdatedAt": visit["updatedAt"]}
    assert client.patch(url + "/assignment", headers=headers, json=fields).status_code == 403
    reassigned = client.patch(url + "/assignment", headers=admin, json=fields)
    assert reassigned.status_code == 200, reassigned.text
    # The original registrar retains history access after assignment changes.
    assert client.get(url, headers=headers).status_code == 200
    assert register(client, headers, key=key).status_code == 200
    assert client.patch(url + '/status', headers=headers, json={'status': 'In Discussion', 'expectedUpdatedAt': reassigned.json()['updatedAt']}).status_code == 403
    assert client.get(url, headers=other).status_code == 200
    foreign = client.post("/api/v1/workspaces", headers=other, json={"name": "Other visit workspace"}).json()["id"]
    assert client.get(url, headers={**other, "X-Workspace-ID": foreign}).status_code == 404
    assert client.delete(f"/api/v1/team/{ids[2]}", headers=admin).status_code == 200
    assert client.get(url, headers=other).status_code == 403
    assert register(client, other).status_code == 403
    assert client.get(url, headers=admin).status_code == 200


def test_agents_derive_roles_from_workspace_and_do_not_leak_other_sales_counts(api):
    client, headers, ids, _, database_url = api
    register(client, headers)
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("update profiles set availability_status='active' where id=%s", (ids[2],))
    register(client, {**headers, "X-Test-User": "2"})
    agents = client.get("/api/v1/walkins/agents", headers=headers).json()
    assert not agents["canManage"] and agents["currentAgentId"] == str(ids[1])
    assert next(agent for agent in agents["items"] if agent["id"] == str(ids[1]))["openVisits"] == 1
    assert next(agent for agent in agents["items"] if agent["id"] == str(ids[2]))["openVisits"] is None
    url = f"/api/v1/walkins/agents/{ids[1]}"
    assert client.patch(url, headers=headers, json={"availability": "Busy"}).status_code == 403
    admin = {**headers, "X-Test-User": "0"}
    assert client.patch(url, headers=admin, json={"availability": "Busy"}).status_code == 200
    assert client.patch(f"/api/v1/walkins/agents/{uuid4()}", headers=admin, json={"availability": "Busy"}).status_code == 404
    assert client.patch(url, headers=admin, json={"availability": "Unrecognized"}).status_code == 422


def test_owner_is_an_eligible_walkin_assignee_when_salespeople_are_unavailable(api):
    client, headers, ids, _, database_url = api
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("update profiles set availability_status='active' where id=%s", (ids[0],))
    admin = {**headers, "X-Test-User": "0"}
    for agent_id in ids[1:]:
        assert client.patch(f"/api/v1/walkins/agents/{agent_id}", headers=admin, json={"availability": "Busy"}).status_code == 200

    visit = register(client, headers).json()

    assert visit["assignedAgentId"] == str(ids[0])
    assert visit["status"] == "Assigned"


def test_pagination_literal_search_no_client_side_tenant_filters(api):
    client, headers, _, _, _ = api
    a = register(client, headers, customerName="Literal 50%_ customer").json()
    register(client, headers, customerName="Other fixture")
    result = client.get("/api/v1/walkins", headers=headers, params={"pageSize": 1}).json()
    assert result["total"] == 2 and len(result["items"]) == 1
    assert result["summary"]["assigned"] == 2
    last = client.get("/api/v1/walkins", headers=headers, params={"pageSize": 1, "page": 3}).json()
    assert last["items"] == [] and last["total"] == 2
    found = client.get("/api/v1/walkins", headers=headers, params={"query": "50%_"}).json()
    assert [item["id"] for item in found["items"]] == [a["id"]]
    assert client.get("/api/v1/walkins", headers=headers, params={"query": a["visitNumber"]}).json()["total"] == 1
    assert client.get("/api/v1/walkins", headers=headers, params={"query": "' OR true --"}).json()["total"] == 0
    assert client.get("/api/v1/walkins", headers=headers, params={"pageSize": 101}).status_code == 422
    assert client.get("/api/v1/walkins", headers=headers, params={"fromDate": "2026-11-30", "toDate": "2026-11-01"}).status_code == 422
    history = client.get("/api/v1/walkins/history", headers=headers, params={"pageSize": 1, "page": 3}).json()
    assert history["total"] == 2 and history["items"] == []


def test_status_and_requirements_are_transactional_versioned_and_closed(api):
    client, headers, _, _, _ = api
    visit = register(client, headers).json()
    url = f"/api/v1/walkins/{visit['id']}"
    # Conversion and assignment cannot be forged via an ordinary status patch.
    for status in ("Converted to Enquiry", "Requirements Captured", "Completed"):
        assert client.patch(url + "/status", headers=headers, json={"status": status, "expectedUpdatedAt": visit["updatedAt"]}).status_code == 409
    discussion = change(client, headers, visit, "In Discussion")
    stale = client.patch(url + "/status", headers=headers, json={"status": "Cancelled", "expectedUpdatedAt": visit["updatedAt"]})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_walkin"
    assert client.patch(url + "/status", headers=headers, json={"status": "Requirements Captured", "expectedUpdatedAt": discussion["updatedAt"]}).status_code == 409
    assert client.put(url + "/requirements", headers=headers, json={"expectedUpdatedAt": discussion["updatedAt"], "requirement": {}}).status_code == 422
    response = client.put(url + "/requirements", headers=headers, json={"expectedUpdatedAt": discussion["updatedAt"], "requirement": {"detailedRequirement": "Reviewed fixture"}})
    assert response.status_code == 200, response.text
    assert response.json()["updatedAt"]
    captured = client.get(url, headers=headers).json()
    assert captured["status"] == "Requirements Captured"
    assert client.put(url + "/requirements", headers=headers, json={"expectedUpdatedAt": discussion["updatedAt"], "requirement": {"detailedRequirement": "Stale write"}}).status_code == 409
    completed = change(client, headers, captured, "Completed")
    assert client.put(url + "/requirements", headers=headers, json={"expectedUpdatedAt": completed["updatedAt"], "requirement": {"detailedRequirement": "Closed write"}}).status_code == 409
    assert client.get(url + "/requirements", headers=headers).json()["detailedRequirement"] == "Reviewed fixture"


def test_atomic_conversion_is_unique_replayable_and_rollback_safe(api):
    client, headers, _, _, database_url = api
    visit = reviewed(client, headers)
    source = f"walkin:{visit['id']}"
    with psycopg.connect(database_url) as connection:
        issued_ticket = connection.execute("select id,ticket_day,daily_sequence,public_token_hash from guruconnect_enquiries where id=%s", (visit["convertedEnquiryReference"],)).fetchone()
    assert save(client, {**headers, "X-Test-User": "2"}, enquiry(sourceEventId=source)).status_code == 404
    assert save(client, {**headers, "X-Test-User": "0"}, enquiry(sourceEventId=source)).status_code == 403
    key = str(uuid4())
    response = save(client, headers, enquiry(sourceEventId=source), key=key)
    assert response.status_code == 201, response.text
    item = response.json()
    converted = client.get(f"/api/v1/walkins/{visit['id']}", headers=headers).json()
    assert converted["status"] == "Converted to Enquiry" and converted["convertedEnquiryReference"] == item["id"]
    assert save(client, headers, enquiry(sourceEventId=source), key=key).status_code == 200
    assert save(client, headers, enquiry(sourceEventId=source)).status_code == 409
    assert save(client, headers, enquiry(sourceEventId="walkin:not-a-uuid")).status_code == 422
    with psycopg.connect(database_url) as connection:
        assert connection.execute("select id,ticket_day,daily_sequence,public_token_hash from guruconnect_enquiries where id=%s", (item["id"],)).fetchone() == issued_ticket
        assert connection.execute("select count(*) from guruconnect_enquiries where organisation_id=%s and payload->>'sourceEventId'=%s", (headers["X-Workspace-ID"], source)).fetchone()[0] == 1
        assert connection.execute("select count(*) from guruconnect_walkin_history where visit_id=%s and event_type='Converted to enquiry'", (visit["id"],)).fetchone()[0] == 1
    # Failed recording attachment leaves the visit open and no enquiry saved.
    second = reviewed(client, headers)
    unfinalized = new(client, headers)
    response = save(client, headers, enquiry(sourceEventId=f"walkin:{second['id']}", conversationId=unfinalized["id"]))
    assert response.status_code == 409
    assert client.get(f"/api/v1/walkins/{second['id']}", headers=headers).json()["status"] == "Requirements Captured"


def test_concurrent_different_conversion_keys_create_one_enquiry(api):
    client, headers, _, _, _ = api
    visit = reviewed(client, headers)
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(lambda _: save(client, headers, enquiry(sourceEventId=f"walkin:{visit['id']}")), range(2)))
    assert sorted(reply.status_code for reply in replies) == [201, 409]


def test_durable_recording_links_enforce_visit_owner_workspace_and_exact_conversion(api):
    client, headers, _, blobs, _ = api
    visit, other_visit = reviewed(client, headers), reviewed(client, headers)
    conv = finalized_recording(client, headers, blobs)
    path = f"/api/v1/walkins/{visit['id']}/recordings"
    fields = {"conversationId": conv["id"]}
    assert client.post(path, headers={**headers, "X-Test-User": "2"}, json=fields).status_code == 404
    assert client.post(path, headers={**headers, "X-Test-User": "0"}, json=fields).status_code == 404
    assert client.post(path, headers=headers, json=fields).status_code == 200
    assert client.post(path, headers=headers, json=fields).status_code == 200
    listed = client.get(path, headers=headers).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == conv["id"]
    assert "config_snapshot" not in listed["items"][0]
    bypass = save(client, headers, enquiry(conversationId=conv["id"]))
    assert bypass.status_code == 409 and bypass.json()["detail"]["code"] == "walkin_conversion_required"
    assert client.post(f"/api/v1/walkins/{other_visit['id']}/recordings", headers=headers, json=fields).status_code == 409
    mismatch = save(client, headers, enquiry(sourceEventId=f"walkin:{other_visit['id']}", conversationId=conv["id"]))
    assert mismatch.status_code == 409 and mismatch.json()["detail"]["code"] == "walkin_recording_mismatch"
    converted = save(client, headers, enquiry(sourceEventId=f"walkin:{visit['id']}", conversationId=conv["id"]))
    assert converted.status_code == 201, converted.text
    assert client.post(path, headers=headers, json=fields).status_code == 200


def test_reception_registration_attaches_own_saved_audio_after_sales_assignment(api):
    client, headers, ids, blobs, database_url = api
    reception = {**headers, "X-Test-User": "2"}
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("update members set role='receptionist' where organisation_id=%s and user_id=%s", (headers["X-Workspace-ID"], ids[2]))
    registered = register(client, reception).json()
    assert registered["assignedAgentId"] == str(ids[1])
    conv = new(client, reception, audio_bucket="walkin-audio", audio_only=True)
    prefix = f"/api/v1/conversations/{conv['id']}"
    path = f"/api/v1/walkins/{registered['id']}/recordings"
    fields = {"conversationId": conv["id"]}
    assert client.post(path, headers=reception, json=fields).status_code == 409
    lease = client.post(prefix + "/lease", headers=reception, json={"tab_id": str(uuid4())}).json()
    chunk, chunk_url = reserve(client, reception, conv, lease, blobs)
    assert client.post(chunk_url + "/confirm", headers=reception, json=chunk).status_code == 200
    assert client.post(prefix + "/finalize", headers=reception, json={"part_id": lease["part_id"], "lease_token": lease["lease_token"], "expected_chunks": 1, "total_samples": chunk["sample_count"]}).status_code == 200
    assert client.post(path, headers=reception, json=fields).status_code == 200
    assert client.post(path, headers=reception, json=fields).status_code == 200
    assert client.get(path, headers=reception).json()["total"] == 1
    assert client.get(prefix + "/playback/stream", headers=reception).status_code == 200
    assert client.get(prefix + "/playback/stream", headers=headers).status_code == 404
    assert client.post(path, headers=headers, json=fields).status_code == 404


def test_database_permissions_and_cross_workspace_foreign_keys(api):
    client, headers, _, _, database_url = api
    visit = register(client, headers).json()
    for role in ("authenticated", "anon"):
        for table in ("guruconnect_walkin_visits", "guruconnect_walkin_history", "guruconnect_walkin_agents", "guruconnect_walkin_recordings"):
            with psycopg.connect(database_url) as connection, pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
                connection.execute("set local role " + role)
                connection.execute("select * from " + table)
    with psycopg.connect(database_url) as connection, pytest.raises(psycopg.errors.ForeignKeyViolation):
        connection.execute("update guruconnect_walkin_visits set organisation_id=%s where id=%s", (uuid4(), visit["id"]))
    with psycopg.connect(database_url) as connection, pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
        connection.execute("set local role service_role")
        connection.execute("delete from guruconnect_walkin_history")
