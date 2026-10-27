"""Environment provider ownership and independent queue execution; no network IO."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from recorder.config import Settings
from recorder.local_store import LocalStore
from recorder.local_worker import LocalWorker
from recorder.openai_translator import MODEL, OpenAITranslator
from recorder import provider_bootstrap as bootstrap
from recorder import worker as worker_module
from recorder.worker import MainWorker


def configured(**values):
    return Settings(
        _env_file=None,
        elevenlabs_api_key="server-speech-test",
        openai_api_key="server-openai-test",
        **values,
    )


def test_existing_openai_model_is_used_unless_translation_model_is_explicit(
    monkeypatch,
):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.delenv("OPENAI_TRANSLATION_MODEL", raising=False)
    assert Settings(_env_file=None).openai_translation_model == "gpt-4o-mini"
    monkeypatch.setenv("OPENAI_TRANSLATION_MODEL", MODEL)
    assert Settings(_env_file=None).openai_translation_model == MODEL
    with pytest.raises(ValidationError):
        Settings(_env_file=None, openai_translation_model="unverified-future-model")


def test_local_managed_keys_stay_only_in_environment_and_rotate_with_fences(tmp_path):
    store = LocalStore(tmp_path)
    settings = configured()
    bootstrap.bootstrap_local_managed_providers(settings, store)
    first = store.connection("elevenlabs")
    bootstrap.bootstrap_local_managed_providers(settings, store)
    assert store.connection("elevenlabs")["generation"] == first["generation"]
    with store.transaction() as db:
        assert db.execute("select count(*) from secrets").fetchone()[0] == 0
        serialized = " ".join(
            row["data"] for row in db.execute("select data from records")
        )
    assert (
        "server-speech-test" not in serialized
        and "server-openai-test" not in serialized
    )
    assert LocalWorker(settings, store).credentials("elevenlabs") == {
        "api_key": "server-speech-test"
    }
    cid = str(uuid4())
    store.put("conversations", cid, {"id": cid, "capture_state": "stopped"})
    queued = store.enqueue_job(cid, "transcribe", {"run_id": str(uuid4())})
    active = store.claim_job("before-rotation")
    assert active["id"] == queued["id"]
    store.mark_job_accepted(active["id"], lease_token=active["lease_token"])
    rotated = configured().model_copy(
        update={"elevenlabs_api_key": SecretStr("server-speech-rotated")}
    )
    bootstrap.bootstrap_local_managed_providers(rotated, store)
    assert store.connection("elevenlabs")["generation"] == first["generation"] + 1
    assert store.get("jobs", active["id"])["state"] == "uncertain"
    with pytest.raises(ValueError, match="local_job_lease_lost"):
        store.complete_job(active["id"], lease_token=active["lease_token"])
    assert (
        LocalWorker(rotated, store).credentials("elevenlabs")["api_key"]
        == "server-speech-rotated"
    )


def test_local_server_key_removal_is_visible_and_reads_preserve_provider_failures(tmp_path):
    store = LocalStore(tmp_path)
    settings = configured()
    bootstrap.bootstrap_local_managed_providers(settings, store)
    store.patch("connections", "elevenlabs", {"state": "invalid_credentials", "last_error_code": "invalid_credentials"})
    bootstrap.bootstrap_local_managed_providers(settings, store)
    assert store.connection("elevenlabs")["state"] == "invalid_credentials"
    generation = store.connection("elevenlabs")["generation"]
    missing = settings.model_copy(update={"elevenlabs_api_key": None, "openai_api_key": None})
    bootstrap.bootstrap_local_managed_providers(missing, store)
    bootstrap.bootstrap_local_managed_providers(missing, store)
    for engine, code in (("elevenlabs", "engine_unconfigured"), ("openai-translator", "translator_unconfigured")):
        row = store.connection(engine)
        assert row["state"] == "configured" and row["tested_at"] is None
        assert row["last_error_code"] == code and row["credential_scope"] is None
        assert row["generation"] == generation + 1
    bootstrap.bootstrap_local_managed_providers(settings, store)
    assert store.connection("elevenlabs")["state"] == "connected"
    assert store.connection("elevenlabs")["generation"] == generation + 2


@pytest.mark.asyncio
async def test_hosted_bootstrap_keeps_connections_tenant_scoped_and_credential_admission_shared(
    monkeypatch,
):
    rows, statements = {}, []

    class Connection:
        async def execute(self, sql, params=()):
            statements.append((sql, params))
            if sql.startswith("update engine_connections set state='configured'"):
                code, identity = params
                row = next(value for value in rows.values() if value["id"] == identity)
                row.update(state="configured", tested_at=None, credential_scope=None,
                           generation=row["generation"] + 1, last_error_code=code)

    async def one(connection, sql, params=()):
        if sql.startswith("select * from engine_connections"):
            return rows.get((params[0], params[1]))
        if sql.startswith("insert into engine_connections"):
            org, engine, config, metadata, scope, maximum = params
            row = {
                "id": uuid4(),
                "organisation_id": org,
                "engine_id": engine,
                "config": config.obj,
                "safe_metadata": metadata.obj,
                "credential_scope": scope,
                "max_concurrency": maximum,
                "generation": 1,
                "state": "connected",
                "tested_at": datetime.now(timezone.utc),
            }
            rows[(org, engine)] = row
            return row
        if sql.startswith("update engine_connections"):
            config, metadata, scope, increment, maximum, identifier = params
            row = next(value for value in rows.values() if value["id"] == identifier)
            row.update(
                config=config.obj,
                safe_metadata=metadata.obj,
                credential_scope=scope,
                max_concurrency=maximum,
                generation=row["generation"] + increment,
            )
            return row
        raise AssertionError(sql)

    monkeypatch.setattr(bootstrap, "one", one)
    orgs = [uuid4(), uuid4()]
    for org in orgs:
        await bootstrap.ensure_managed_providers(Connection(), org, configured())
    a, b = rows[(orgs[0], "elevenlabs")], rows[(orgs[1], "elevenlabs")]
    assert a["id"] != b["id"] and a["organisation_id"] != b["organisation_id"]
    assert a["credential_scope"] == b["credential_scope"] and a["max_concurrency"] == 4
    assert a["safe_metadata"] == {
        "platform_managed": True,
        "readiness_source": "server_environment",
        "provider_access_verified": False,
    }
    before = [row["generation"] for row in rows.values()]
    await bootstrap.ensure_managed_providers(Connection(), orgs[0], configured())
    assert before == [row["generation"] for row in rows.values()]
    rotated = configured().model_copy(
        update={"elevenlabs_api_key": SecretStr("changed-server-test")}
    )
    await bootstrap.ensure_managed_providers(Connection(), orgs[0], rotated)
    assert a["generation"] == 2 and b["generation"] == 1
    assert any(
        "submission_state in ('submitting','accepted','uncertain')" in sql
        for sql, _ in statements
    )
    # Neither the SQL parameter stream nor connection metadata contains a key.
    assert "server-speech-test" not in str(statements) + str(rows)
    assert "server-openai-test" not in str(statements) + str(rows)
    a["state"], a["last_error_code"] = "invalid_credentials", "invalid_credentials"
    await bootstrap.ensure_managed_providers(Connection(), orgs[0], rotated)
    assert a["state"] == "invalid_credentials"
    missing = rotated.model_copy(update={"elevenlabs_api_key": None, "openai_api_key": None})
    await bootstrap.ensure_managed_providers(Connection(), orgs[0], missing)
    await bootstrap.ensure_managed_providers(Connection(), orgs[0], missing)
    assert a["generation"] == 3 and a["state"] == "configured"
    assert a["tested_at"] is None and a["last_error_code"] == "engine_unconfigured"
    assert b["state"] == "connected"  # Another workspace has not been touched.


@pytest.mark.asyncio
async def test_worker_uses_platform_environment_before_old_encrypted_credentials(
    monkeypatch,
):
    class DB:
        @asynccontextmanager
        async def connection(self):
            yield object()

    async def one(*args):
        return {
            "encrypted_value": "obsolete-encrypted-secret",
            "engine_id": "openai-translator",
            "config": {"platform_managed": True},
        }

    monkeypatch.setattr(worker_module, "one", one)
    instance = MainWorker(configured())
    instance.db = DB()
    assert await instance.credentials(uuid4()) == {"api_key": "server-openai-test"}


@pytest.mark.asyncio
async def test_gpt4o_responses_preserves_snapshot_model_and_omits_reasoning(
    monkeypatch,
):
    original = httpx.AsyncClient

    def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == "gpt-4o-mini"
        assert "reasoning" not in payload
        assert payload["store"] is False and payload["text"]["format"]["strict"] is True
        return httpx.Response(
            200,
            json={
                "id": "resp_model_test",
                "model": "gpt-4o-mini-2024-07-18",
                "status": "completed",
                "error": None,
                "usage": {"input_tokens": 10, "output_tokens": 3},
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": '{"text":"Hello 42"}'}
                        ],
                    }
                ],
            },
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    usage = []

    async def account(value):
        usage.append(value)

    result = await OpenAITranslator(
        "fixture-only", model="gpt-4o-mini", usage_callback=account
    ).translate("नमस्ते 42")
    assert result.text == "Hello 42" and usage[0].model == "gpt-4o-mini-2024-07-18"


@pytest.mark.asyncio
async def test_twenty_queued_jobs_use_bounded_slots_while_retention_and_heartbeats_continue(
    monkeypatch,
):
    jobs = [
        {"id": str(i), "kind": "transcribe", "conversation_id": str(i), "attempt": 1}
        for i in range(20)
    ]
    state = SimpleNamespace(
        active=0, peak=0, completed=[], heartbeats=0, cleanup=0, closed=False
    )
    release, saturated = asyncio.Event(), asyncio.Event()

    class DB:
        async def open(self):
            pass

        async def close(self):
            state.closed = True

        @asynccontextmanager
        async def connection(self):
            yield self

        async def execute(self, sql, params=()):
            if "worker_heartbeats" in sql:
                state.heartbeats += 1

    async def one(*args):
        return {"queued": len(jobs), "running": state.active}

    async def many(*args):
        return [jobs.pop(0)] if jobs else []

    monkeypatch.setattr(worker_module, "one", one)
    monkeypatch.setattr(worker_module, "many", many)
    worker = MainWorker(configured())
    worker.settings = SimpleNamespace(
        configured=True,
        processing_concurrency=4,
        processing_shutdown_seconds=0.2,
        worker_lease_seconds=180,
    )
    worker.db = DB()

    async def execute(job):
        state.active += 1
        state.peak = max(state.peak, state.active)
        if state.active == 4:
            saturated.set()
        try:
            await release.wait()
            state.completed.append(job["id"])
            if len(state.completed) == 20:
                worker.stopping.set()
        finally:
            state.active -= 1

    async def cleanup():
        state.cleanup += 1

    worker.execute = execute
    worker.cleanup_deleted = cleanup
    running = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(saturated.wait(), 2)
        assert len(jobs) == 16 and state.peak == 4
        assert state.heartbeats >= 1 and state.cleanup >= 1
        release.set()
        await asyncio.wait_for(running, 5)
        assert len(set(state.completed)) == 20 and state.peak <= 4 and state.closed
    finally:
        worker.stopping.set()
        release.set()
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)
