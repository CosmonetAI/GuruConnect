"""Only a failure accepted by the current job lease may change visible status."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import recorder.worker as module
from recorder.config import Settings
from recorder.providers import ProviderError
from recorder.worker import MainWorker


def failure_case(monkeypatch, kind, *, accepted, error=None):
    state = SimpleNamespace(executed=[], renewal_cancelled=False)
    job = {
        "id": "job-1", "conversation_id": "conversation-1", "run_id": "run-1",
        "kind": kind, "lease_token": "lease-1", "fence": 7, "attempt": 1,
    }

    class Connection:
        async def execute(self, sql, params=()):
            state.executed.append((sql, params))

    class Database:
        @asynccontextmanager
        async def connection(self):
            yield Connection()

    async def one(connection, sql, params=()):
        if sql.startswith("select state from processing_jobs"):
            return {"state": "blocked" if error and error.code == "invalid_credentials" else "failed"}
        assert "retry_job(" in sql
        await connection.execute(sql, params)
        return {"accepted": accepted}

    async def fail_operation(_job):
        raise error or RuntimeError("Database unavailable while processing")

    async def renew(_job):
        try:
            await asyncio.Event().wait()
        finally:
            state.renewal_cancelled = True

    worker = MainWorker(Settings(_env_file=None))
    worker.id = "fixture-worker"
    worker.db = Database()
    worker.transcribe = fail_operation
    worker.translate = fail_operation
    worker.renew = renew
    monkeypatch.setattr(module, "one", one)
    state.worker = worker
    state.job = job
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["transcribe", "translate", "finalise"])
async def test_stale_or_finished_job_cannot_overwrite_conversation_status(monkeypatch, kind):
    state = failure_case(monkeypatch, kind, accepted=False)
    await state.worker.execute(state.job)
    retries = [(sql, params) for sql, params in state.executed if "retry_job(" in sql]
    assert len(retries) == 1
    assert retries[0][1] == (
        "job-1", "fixture-worker", "lease-1", 7, "worker_execution_failed", False,
    )
    assert not any(sql.startswith("update conversations") for sql, _ in state.executed)
    assert state.renewal_cancelled


@pytest.mark.asyncio
@pytest.mark.parametrize("kind, stage", [
    ("transcribe", "transcription_state"),
    ("translate", "english_state"),
    ("finalise", "final_state"),
])
async def test_accepted_failure_updates_only_its_own_stage(monkeypatch, kind, stage):
    state = failure_case(monkeypatch, kind, accepted=True)
    await state.worker.execute(state.job)
    updates = [(sql, params) for sql, params in state.executed if sql.startswith("update conversations")]
    assert len(updates) == 1
    assert f"set {stage}=%s" in updates[0][0]
    assert "v.kind='final' and v.english_state='complete'" in updates[0][0]
    assert updates[0][1] == (("failed", "failed", "conversation-1", "run-1") if stage != "final_state" else ("failed", "conversation-1", "run-1"))
    assert state.renewal_cancelled


@pytest.mark.asyncio
async def test_accepted_translation_access_failure_marks_english_blocked(monkeypatch):
    state = failure_case(monkeypatch, "translate", accepted=True, error=ProviderError("invalid_credentials"))
    await state.worker.execute(state.job)
    updates = [(sql, params) for sql, params in state.executed if sql.startswith("update conversations")]
    assert len(updates) == 1
    assert "set english_state=%s" in updates[0][0]
    assert updates[0][1] == ("blocked", "blocked", "conversation-1", "run-1")


@pytest.mark.asyncio
async def test_renewal_failure_after_operation_finishes_cannot_overwrite_published_status(monkeypatch):
    state = failure_case(monkeypatch, "translate", accepted=False)
    published = []

    async def completed(_job):
        published.append("complete")

    async def renewal_failed(_job):
        raise RuntimeError("Database connection lost while renewing")

    state.worker.translate = completed
    state.worker.renew = renewal_failed
    await state.worker.execute(state.job)
    assert published == ["complete"]
    assert any("retry_job(" in sql for sql, _ in state.executed)
    assert not any(sql.startswith("update conversations") for sql, _ in state.executed)


@pytest.mark.asyncio
async def test_external_cancellation_waits_for_operation_database_cleanup_and_stops_renewal(monkeypatch):
    state = failure_case(monkeypatch, "translate", accepted=True)
    lock = asyncio.Lock()
    entered = asyncio.Event()
    exiting = asyncio.Event()
    allow_cleanup = asyncio.Event()
    cleaned = asyncio.Event()
    operations = []

    class HoldingDatabase:
        @asynccontextmanager
        async def connection(self):
            async with lock:
                entered.set()
                try:
                    yield object()
                finally:
                    exiting.set()
                    await allow_cleanup.wait()
                    cleaned.set()

    async def operation(_job):
        operations.append(asyncio.current_task())
        async with state.worker.db.connection():
            await asyncio.Event().wait()

    state.worker.db = HoldingDatabase()
    state.worker.translate = operation
    executing = asyncio.create_task(state.worker.execute(state.job))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert lock.locked()
        executing.cancel()
        await asyncio.wait_for(exiting.wait(), timeout=1)
        assert not executing.done(), "execute must await the operation's asynchronous cleanup"
        allow_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await executing
        assert cleaned.is_set()
        assert not lock.locked()
        assert len(operations) == 1 and operations[0].cancelled()
        assert state.renewal_cancelled
        assert not state.executed, "Cancellation must not retry or resubmit provider work"
    finally:
        allow_cleanup.set()
        for task in [executing, *operations]:
            if task and not task.done():
                task.cancel()
        await asyncio.gather(executing, *operations, return_exceptions=True)
