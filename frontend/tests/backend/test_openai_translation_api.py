"""Translation contracts and API plumbing; provider calls are mocked explicitly."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from recorder import connection_routes, routes, transcript_routes
from recorder.connection_routes import validate_connection_config, safe_connection
from recorder.schemas import ConnectionCreate, ConversationCreate, EngineSettings, RunCreate, Retry, TestConnection as ConnectionTest
from recorder.providers import ProviderError


def configuration(**overrides):
    data = {"engine_id": "openai-translator", "credentials": {"api_key": "explicit-test-key"}, "idempotency_key": "fixture-config-1"}
    data.update(overrides)
    return ConnectionCreate.model_validate(data)


def test_openai_configuration_snapshots_only_exact_requested_model():
    body = configuration()
    validate_connection_config(body)
    assert body.config == {"model": "gpt-5.6-luna"}
    body = configuration(config={"model": "gpt-5.6-luna", "max_concurrency": 1})
    validate_connection_config(body)
    assert body.config["model"] == "gpt-5.6-luna"


@pytest.mark.parametrize("changes,code", [
    ({"config": {"model": "another-model"}}, "openai_model_not_allowed"),
    ({"config": {"base_url": "https://attacker.invalid"}}, "invalid_engine_configuration"),
    ({"config": {"region": "centralindia"}}, "invalid_engine_configuration"),
    ({"config": {"device": "cpu"}}, "invalid_engine_configuration"),
    ({"config": {"max_concurrency": True}}, "invalid_provider_concurrency"),
    ({"config": {"max_concurrency": 0}}, "invalid_provider_concurrency"),
    ({"credentials": {"api_key": "value", "region": "centralindia"}}, "unsupported_credential_field"),
    ({"credentials": {}}, "provider_api_key_required"),
    ({"credentials": {"api_key": ""}}, "invalid_credential"),
])
def test_openai_configuration_rejects_model_fallbacks_endpoints_and_foreign_fields(changes, code):
    with pytest.raises(HTTPException) as error:
        validate_connection_config(configuration(**changes))
    assert error.value.detail["code"] == code
    assert "explicit-test-key" not in str(error.value.detail)


@pytest.mark.parametrize("schema,data", [
    (ConversationCreate, {"customer_name": "Sam", "phone": "9876543210", "consent_confirmed": True, "engine_id": "openai-translator"}),
    (RunCreate, {"engine_id": "openai-translator", "kind": "comparison", "idempotency_key": "safe-key-123"}),
    (EngineSettings, {"default_engine": "openai-translator"}),
])
def test_openai_translation_cannot_be_selected_as_speech_engine(schema, data):
    with pytest.raises(ValidationError):
        schema.model_validate(data)


def test_openai_safe_connection_never_exposes_credentials_or_fingerprints():
    result = safe_connection({"engine_id": "openai-translator", "config": {"model": "gpt-5.6-luna"},
        "encrypted_value": "must-not-escape", "credential_scope": "secret-fingerprint", "api_key": "secret-key",
        "safe_metadata": {"idempotency_key": "hidden", "request_hash": "hidden", "model": "gpt-5.6-luna"}})
    assert result == {"engine_id": "openai-translator", "config": {"model": "gpt-5.6-luna"}, "safe_metadata": {"model": "gpt-5.6-luna"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("selected,strategy", [("openai-translator", "openai"), ("azure-translator", "azure")])
async def test_run_uses_selected_workspace_translator_and_snapshots_safe_config(monkeypatch, selected, strategy):
    workspace_id, connection_id, translator_id, conversation_id = [uuid4() for _ in range(4)]
    speech = {"id": connection_id, "generation": 3, "state": "connected", "tested_at": datetime.now(timezone.utc), "config": {}}
    translator = {"id": translator_id, "generation": 4, "engine_id": selected, "config": {"model": "gpt-5.6-luna"} if strategy == "openai" else {"region": "global"}}
    captured = {}
    async def one(connection, sql, values):
        if "select * from processing_runs" in sql:
            return None
        if "engine_id=%s" in sql:
            return speech
        if "workspace_engine_settings" in sql:
            assert "c.id=s.translator_connection_id" in sql
            assert "c.organisation_id=s.organisation_id" in sql
            assert values == (workspace_id,)
            return translator
        captured["values"] = values
        return {"id": uuid4()}
    monkeypatch.setattr(routes, "one", one)
    await routes.create_run(object(), SimpleNamespace(id=workspace_id), {"id": conversation_id}, "elevenlabs", "production", "initial")
    values = captured["values"]
    assert values[7:10] == (strategy, translator_id, 4)
    assert values[6].obj["translation"] == {"provider": selected, "config": translator["config"]}


@pytest.mark.asyncio
async def test_translation_retry_uses_explicit_rebind_rpc_and_skips_uncertain(monkeypatch):
    context = SimpleNamespace(id=uuid4(), user=SimpleNamespace(id=uuid4()))
    target, uncertain = uuid4(), uuid4()
    jobs = [{"id": target, "kind": "translate", "state": "blocked", "connection_id": uuid4()},
            {"id": uncertain, "kind": "translate", "state": "uncertain", "connection_id": uuid4()}]
    commands = []
    class Database:
        @asynccontextmanager
        async def connection(self):
            yield object()
    async def one(connection, sql, values):
        commands.append((sql, values))
        return {"resumed": True}
    async def many(*args):
        return jobs
    async def conversation(*args, **kwargs):
        return {}
    async def audit(*args, **kwargs):
        pass
    monkeypatch.setattr(transcript_routes, "one", one)
    monkeypatch.setattr(transcript_routes, "many", many)
    monkeypatch.setattr(transcript_routes, "conversation", conversation)
    monkeypatch.setattr(transcript_routes, "audit", audit)
    result = await transcript_routes.retry(uuid4(), Retry(stage="translation"), SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=Database()))), context)
    assert commands == [("select resume_translation_job(%s,%s) as resumed", (target, context.user.id))]
    assert result["resumed_job_ids"] == [target]
    assert result["uncertain_job_ids"] == [uncertain]


@pytest.mark.asyncio
@pytest.mark.parametrize("functional,previously_ready,expected_state,rejected", [(False, False, "configured", False), (True, False, "connected", False), (False, True, "connected", False), (False, True, "invalid_credentials", True)])
async def test_connection_test_requires_functional_readiness_and_preserves_same_generation_result(monkeypatch, functional, previously_ready, expected_state, rejected):
    connection_id, organisation_id = uuid4(), uuid4()
    row = {"id": connection_id, "organisation_id": organisation_id, "engine_id": "openai-translator", "generation": 2,
           "state": "configured", "config": {"model": "gpt-5.6-luna"}, "safe_metadata": {"functional_translation_ready": previously_ready},
           "tested_at": datetime.now(timezone.utc) if previously_ready else None}
    observed = []
    class Database:
        @asynccontextmanager
        async def connection(self):
            yield self
        async def execute(self, *args):
            return None
    class Adapter:
        def __init__(self, key):
            assert key == "test-key-ephemeral"
        async def test(self, *, functional):
            observed.append(functional)
            if rejected:
                raise ProviderError("invalid_credentials")
            return {"connected": True, "functional": functional, "model": "gpt-5.6-luna"}
    async def get_connection(*args):
        return row if not observed else {**row, "state": "checking"}
    async def one(connection, sql, values):
        if "encrypted_value" in sql:
            return {"encrypted_value": "ciphertext"}
        if "count(*)" in sql:
            return {"ready": 0}
        assert values[:3] == (expected_state, "invalid_credentials" if rejected else None, functional and not rejected)
        assert values[3].obj == {"model_access_verified": not rejected, "functional_translation_ready": (functional or previously_ready) and not rejected}
        return {**row, "state": expected_state, "safe_metadata": values[3].obj}
    class SecretBox:
        def __init__(self, settings):
            pass
        def decrypt(self, ciphertext):
            assert ciphertext == "ciphertext"
            return {"api_key": "test-key-ephemeral"}
    async def audit(*args, **kwargs):
        pass
    monkeypatch.setattr(connection_routes, "OpenAITranslator", Adapter)
    monkeypatch.setattr(connection_routes, "SecretBox", SecretBox)
    monkeypatch.setattr(connection_routes, "get_connection", get_connection)
    monkeypatch.setattr(connection_routes, "one", one)
    monkeypatch.setattr(connection_routes, "audit", audit)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=Database(), settings=object())))
    result = await connection_routes.test(connection_id, ConnectionTest(functional=functional), request, SimpleNamespace(id=organisation_id))
    assert observed == [functional]
    assert result["passed"] is (not rejected)
    if not rejected:
        assert result["test"]["functional"] is functional
    assert result["state"] == expected_state
    assert "test-key-ephemeral" not in str(result)


@pytest.mark.asyncio
async def test_openai_enable_cannot_bypass_functional_readiness(monkeypatch):
    class Database:
        @asynccontextmanager
        async def connection(self):
            yield self
    async def get_connection(*args):
        return {"state": "disabled", "tested_at": datetime.now(timezone.utc), "engine_id": "openai-translator", "safe_metadata": {"functional_translation_ready": False}}
    monkeypatch.setattr(connection_routes, "get_connection", get_connection)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=Database())))
    with pytest.raises(HTTPException) as error:
        await connection_routes.enable(uuid4(), request, SimpleNamespace(id=uuid4()))
    assert error.value.detail["code"] == "connection_test_required"
