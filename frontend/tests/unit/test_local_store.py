"""Portable regressions for persistent local credential keys."""

import multiprocessing
from pathlib import Path
import time

from cryptography.fernet import Fernet
import pytest

from recorder.local_store import LocalStore


def _start_store(root, barrier, results):
    """Use spawn even on Unix so the test exercises independent processes."""
    generate_key = Fernet.generate_key
    generated = False

    def slow_generate_key():
        nonlocal generated
        generated = True
        # Keep the initially empty file visible while other startups contend.
        time.sleep(0.3)
        return generate_key()

    Fernet.generate_key = staticmethod(slow_generate_key)
    barrier.wait(timeout=30)
    store = LocalStore(root)
    results.put((store._key, generated))


def _start_stores(root: Path, count: int):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(count + 1)
    results = context.Queue()
    processes = [
        context.Process(target=_start_store, args=(root, barrier, results))
        for _ in range(count)
    ]
    try:
        for process in processes:
            process.start()
        barrier.wait(timeout=30)
        deadline = time.monotonic() + 30
        for process in processes:
            process.join(timeout=max(0, deadline - time.monotonic()))
        assert all(not process.is_alive() for process in processes), "Store startup blocked"
        assert all(process.exitcode == 0 for process in processes)
        return [results.get(timeout=5) for _ in processes]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        results.close()
        results.join_thread()


def test_concurrent_startups_generate_one_shared_key(tmp_path):
    results = _start_stores(tmp_path, 4)

    persisted_key = (tmp_path / "credentials.key").read_bytes()
    assert sum(generated for _, generated in results) == 1
    assert all(key == persisted_key for key, _ in results)
    assert LocalStore(tmp_path)._key == persisted_key


def test_existing_key_and_encrypted_credentials_survive_restart(tmp_path, monkeypatch):
    existing_key = Fernet.generate_key()
    (tmp_path / "credentials.key").write_bytes(existing_key)
    credentials = {"api_key": "synthetic-secret"}
    store = LocalStore(tmp_path)
    store.configure_connection("elevenlabs", credentials, {}, "initial", None)

    def unexpected_generate_key():
        raise AssertionError("An existing credential key must never be regenerated")

    monkeypatch.setattr(Fernet, "generate_key", staticmethod(unexpected_generate_key))
    restarted = LocalStore(tmp_path)

    assert (tmp_path / "credentials.key").read_bytes() == existing_key
    assert restarted.secrets("elevenlabs") == credentials


def test_failed_key_generation_releases_lock_for_another_process(tmp_path, monkeypatch):
    def failed_generate_key():
        raise RuntimeError("injected key generation failure")

    with monkeypatch.context() as patch:
        patch.setattr(Fernet, "generate_key", staticmethod(failed_generate_key))
        with pytest.raises(RuntimeError, match="injected key generation failure"):
            LocalStore(tmp_path)

    [(key, generated)] = _start_stores(tmp_path, 1)

    assert generated
    assert (tmp_path / "credentials.key").read_bytes() == key
    assert LocalStore(tmp_path)._key == key
