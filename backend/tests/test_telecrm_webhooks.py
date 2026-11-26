from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from recorder.config import Settings
from recorder.telecrm_routes import router


class Cursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class Connection:
    def __init__(self):
        self.rows = {}

    async def execute(self, query, params=()):
        query = str(query)
        if query.lstrip().startswith("insert into public.telecrm_events"):
            dedupe_key, event_id = params[2], str(uuid4())
            if dedupe_key in self.rows:
                return Cursor(None)
            self.rows[dedupe_key] = {"id": event_id}
            return Cursor(self.rows[dedupe_key])
        if query.lstrip().startswith("select id from public.telecrm_events"):
            return Cursor(self.rows.get(params[0]))
        raise AssertionError(query)


class Database:
    def __init__(self):
        self.connection_value = Connection()

    @asynccontextmanager
    async def connection(self):
        yield self.connection_value


def client():
    app = FastAPI()
    app.state.settings = Settings(_env_file=None, telecrm_key=SecretStr("test-telecrm-key"))
    app.state.db = Database()
    app.include_router(router)
    return TestClient(app)


def test_call_recording_requires_recording_url_and_phone():
    with client() as http:
        payload = {"event": "call_recording_available", "recording_url": "https://example.test/call.wav", "customer_phone": "+91 9876543210", "call_id": "call-1", "call_started_at": "2026-11-26T10:00:00Z", "call_ended_at": "2026-11-26T10:05:00Z", "duration_seconds": "300"}
        response = http.post("/api/webhooks/telecrm", json=payload, headers={"Authorization": "Bearer test-telecrm-key"})
        assert response.status_code == 200
        assert response.json()["accepted"] is True
        assert response.json()["duplicate"] is False
        assert http.post("/api/webhooks/telecrm", json=payload, headers={"X-TeleCRM-Key": "test-telecrm-key"}).json()["duplicate"] is True

        missing = {"event": "call_recording_available", "recording_url": "https://example.test/call.wav", "customer_phone": "+919876543210", "call_started_at": "2026-11-26T10:00:00Z", "call_ended_at": "2026-11-26T10:05:00Z"}
        assert http.post("/api/webhooks/telecrm", json=missing, headers={"X-TeleCRM-Key": "test-telecrm-key"}).status_code == 422


def test_whatsapp_message_requires_phone_and_message_but_keeps_optional_fields_optional():
    with client() as http:
        payload = {"event": "whatsapp_message_received", "message_id": "incoming-test-1", "customer_phone": "+919876543210", "message_text": "I am interested", "received_at": "2026-11-26T10:00:00Z"}
        response = http.post("/api/webhooks/telecrm/whatsapp-incoming", json=payload, headers={"X-TeleCRM-Key": "test-telecrm-key"})
        assert response.status_code == 200
        assert response.json()["event"] == "whatsapp_message_received"

        missing = {"event": "whatsapp_message_received", "message_id": "incoming-test-2", "customer_phone": "+919876543210", "message_text": "I am interested"}
        assert http.post("/api/webhooks/telecrm", json=missing, headers={"X-TeleCRM-Key": "test-telecrm-key"}).status_code == 422

        missing_message_id = {"event": "whatsapp_message_received", "customer_phone": "+919876543210", "message_text": "I am interested", "received_at": "2026-11-26T10:00:00Z"}
        assert http.post("/api/webhooks/telecrm", json=missing_message_id, headers={"X-TeleCRM-Key": "test-telecrm-key"}).status_code == 422

        sent = {"event": "whatsapp_message_sent", "customer_phone": "+919876543210", "message_text": "Later API work"}
        assert http.post("/api/webhooks/telecrm", json=sent, headers={"X-TeleCRM-Key": "test-telecrm-key"}).status_code == 422


def test_wrong_telecrm_key_is_rejected_before_database_write():
    with client() as http:
        response = http.post("/api/webhooks/telecrm", json={"event": "whatsapp_message_status"}, headers={"X-TeleCRM-Key": "wrong"})
        assert response.status_code == 401
        assert http.app.state.db.connection_value.rows == {}


def test_status_requires_phone_and_status():
    with client() as http:
        missing_status = {"event": "whatsapp_message_status", "message_id": "status-test-1", "customer_phone": "+919876543210"}
        response = http.post("/api/webhooks/telecrm/whatsapp-status", json=missing_status, headers={"X-TeleCRM-Key": "test-telecrm-key"})
        assert response.status_code == 422

        valid = {**missing_status, "status": "delivered"}
        response = http.post("/api/webhooks/telecrm/whatsapp-status", json=valid, headers={"X-TeleCRM-Key": "test-telecrm-key"})
        assert response.status_code == 200

        missing_message_id = {"event": "whatsapp_message_status", "customer_phone": "+919876543210", "status": "read"}
        response = http.post("/api/webhooks/telecrm/whatsapp-status", json=missing_message_id, headers={"X-TeleCRM-Key": "test-telecrm-key"})
        assert response.status_code == 422
