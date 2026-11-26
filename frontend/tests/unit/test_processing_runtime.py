import asyncio
from types import SimpleNamespace

import pytest

from recorder.runtime import ProcessingRuntime


def settings(enabled=True):
    return SimpleNamespace(embedded_processing_enabled=enabled, processing_shutdown_seconds=0.1)


class Runner:
    def __init__(self, fail=False):
        self.stopping = asyncio.Event()
        self.entered = asyncio.Event()
        self.cancelled = False
        self.fail = fail

    async def run(self):
        self.entered.set()
        if self.fail:
            raise RuntimeError("synthetic database credential must not be logged")
        try:
            await self.stopping.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@pytest.mark.asyncio
async def test_runtime_starts_executor_and_drains_on_api_shutdown(monkeypatch):
    runner = Runner()
    runtime = ProcessingRuntime(settings(), factory=lambda: runner)
    async def bootstrap():
        pass
    monkeypatch.setattr(runtime, "_bootstrap", bootstrap)
    await runtime.start()
    await asyncio.wait_for(runner.entered.wait(), 1)
    assert runtime.healthy
    await runtime.stop()
    assert runner.stopping.is_set()
    assert runtime.task.done()
    assert not runner.cancelled
    assert runtime.state == "stopped"


@pytest.mark.asyncio
async def test_runtime_restarts_failed_executor_without_logging_secret(monkeypatch, caplog):
    first, second = Runner(fail=True), Runner()
    runners = iter([first, second])
    runtime = ProcessingRuntime(settings(), factory=lambda: next(runners))
    async def bootstrap():
        pass
    monkeypatch.setattr(runtime, "_bootstrap", bootstrap)
    try:
        await runtime.start()
        await asyncio.wait_for(second.entered.wait(), 2)
        assert runtime.healthy
        assert "synthetic database credential" not in caplog.text
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_disabled_does_not_start_or_bootstrap(monkeypatch):
    runtime = ProcessingRuntime(settings(False), factory=lambda: pytest.fail("unexpected processor"))
    async def unexpected():
        pytest.fail("unexpected bootstrap")
    monkeypatch.setattr(runtime, "_bootstrap", unexpected)
    await runtime.start()
    assert runtime.task is None
    assert runtime.status() == {"enabled": False, "state": "disabled", "healthy": False}
    await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_cancels_stuck_executor_with_bounded_shutdown(monkeypatch):
    runner = Runner()
    blocked = asyncio.Event()
    async def stuck():
        runner.entered.set()
        try:
            await blocked.wait()
        except asyncio.CancelledError:
            runner.cancelled = True
            raise
    runner.run = stuck
    runtime = ProcessingRuntime(settings(), factory=lambda: runner)
    async def bootstrap():
        pass
    monkeypatch.setattr(runtime, "_bootstrap", bootstrap)
    await runtime.start()
    await asyncio.wait_for(runner.entered.wait(), 1)
    await asyncio.wait_for(runtime.stop(), 1)
    assert runner.cancelled
    assert runtime.task.done()
