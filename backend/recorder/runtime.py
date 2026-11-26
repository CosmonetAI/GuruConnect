"""Supervise durable processing within the API service's lifecycle.

Only execution lives in this process. PostgreSQL/SQLite remain the source of
truth for jobs, submission fences and retries across process replacements.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable
from uuid import uuid4

log = logging.getLogger("recorder.runtime")


class ProcessingRuntime:
    def __init__(self, settings, *, database=None, store=None, factory: Callable | None = None):
        self.settings = settings
        self.database = database
        self.store = store
        self.factory = factory
        self.task: asyncio.Task | None = None
        self.worker = None
        self.stopping = asyncio.Event()
        self.state = "disabled" if not settings.embedded_processing_enabled else "starting"

    @property
    def healthy(self) -> bool:
        return self.state == "running" and self.task is not None and not self.task.done()

    def status(self) -> dict:
        return {"enabled": self.settings.embedded_processing_enabled,
                "state": self.state, "healthy": self.healthy}

    async def start(self):
        if not self.settings.embedded_processing_enabled:
            return
        if self.task is not None:
            raise RuntimeError("Processing runtime is already started")
        self.task = asyncio.create_task(self._supervise(), name="guruconnect-processing")
        # Let the supervisor start; database/provider setup remains asynchronous.
        await asyncio.sleep(0)

    def _create_worker(self):
        if self.factory is not None:
            return self.factory()
        if self.store is not None:
            from .local_worker import LocalWorker
            worker = LocalWorker(self.settings, self.store)
        else:
            from .worker import MainWorker
            worker = MainWorker(self.settings)
        # Every API process/replica must own a distinct database lease identity.
        worker.id = f"api-main-{uuid4()}"
        return worker

    async def _bootstrap(self):
        from .provider_bootstrap import bootstrap_local_managed_providers, bootstrap_managed_providers
        if self.store is not None:
            bootstrap_local_managed_providers(self.settings, self.store)
        elif self.database is not None:
            await bootstrap_managed_providers(self.settings, self.database)

    async def _supervise(self):
        backoff = 0.25
        while not self.stopping.is_set():
            self.state = "starting"
            try:
                await self._bootstrap()
                if self.stopping.is_set():
                    break
                self.worker = self._create_worker()
                self.state = "running"
                log.info("processing_started location=api")
                await self.worker.run()
                if not self.stopping.is_set():
                    raise RuntimeError("Processing loop exited unexpectedly")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.state = "restarting"
                # Never print exception text: it may contain provider/database secrets.
                log.error("processing_restart exception_type=%s", type(error).__name__)
            finally:
                if self.worker is not None:
                    self.worker.stopping.set()
                self.worker = None
            if not self.stopping.is_set():
                try:
                    await asyncio.wait_for(self.stopping.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                backoff = min(5.0, backoff * 2)
        self.state = "stopped"

    async def stop(self):
        self.stopping.set()
        if self.worker is not None:
            self.worker.stopping.set()
        if self.task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self.task), timeout=self.settings.processing_shutdown_seconds)
            except TimeoutError:
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
            except asyncio.CancelledError:
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
                raise
        self.state = "stopped"
