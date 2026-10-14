"""Readiness checks real schema state without mutating application records."""

import shutil
import subprocess
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from recorder.config import Settings
from recorder.main import create_app

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = sorted((ROOT / "supabase/migrations").glob("*.sql"))


@pytest.fixture
def database_url():
    if not all(shutil.which(command) for command in ("initdb", "pg_ctl", "psql")):
        pytest.skip("Native PostgreSQL binaries are required for schema readiness regression")
    with tempfile.TemporaryDirectory(prefix="guru-readiness-test-") as temporary:
        path = Path(temporary)
        subprocess.run(
            ["initdb", "-D", str(path / "data"), "-A", "trust", "--no-locale", "-E", "UTF8"],
            check=True, capture_output=True,
        )
        # Each test has its own socket directory; no hosted project is touched.
        subprocess.run(
            ["pg_ctl", "-D", str(path / "data"), "-l", str(path / "postgres.log"),
             "-o", f"-p 55442 -h '' -k {path} -c wal_level=logical", "-w", "start"],
            check=True, capture_output=True,
        )
        url = f"postgresql:///postgres?host={path}&port=55442"
        try:
            with psycopg.connect(url, autocommit=True) as connection:
                connection.execute((ROOT / "tests/database/native_fixture.sql").read_text(), prepare=False)
            yield url
        finally:
            subprocess.run(
                ["pg_ctl", "-D", str(path / "data"), "-m", "fast", "-w", "stop"],
                check=True, capture_output=True,
            )


def configured_settings(database_url="postgresql:///test"):
    return Settings(
        _env_file=None,
        database_url=database_url,
        supabase_url="https://readiness-fixture.supabase.co",
        supabase_service_role_key="fixture-key",
    )


@pytest.mark.parametrize("migration_count", range(len(MIGRATIONS) + 1))
def test_ready_requires_complete_migrations(database_url, migration_count):
    migrations = sorted((ROOT / "supabase/migrations").glob("*.sql"))
    assert migrations == MIGRATIONS
    with psycopg.connect(database_url, autocommit=True) as connection:
        for migration in migrations[:migration_count]:
            # The fixture emulates pgmq because stock PostgreSQL lacks it.
            sql = migration.read_text().replace("create extension if not exists pgmq;", "")
            connection.execute(sql, prepare=False)

    with TestClient(create_app(configured_settings(database_url))) as client:
        response = client.get("/ready")
        assert client.get("/health").status_code == 200
        assert response.headers["cache-control"] == "no-store"
        if migration_count < len(migrations):
            assert response.status_code == 503
            assert response.json() == {"status": "not_ready", "code": "database_schema_missing"}
        else:
            assert response.status_code == 200
            assert response.json() == {"status": "ready"}
            with psycopg.connect(database_url) as connection:
                assert connection.execute("select count(*) from public.organisations").fetchone()[0] == 0
                assert connection.execute("select count(*) from recorder_private.api_rate_limits").fetchone()[0] == 0


def test_missing_configuration_does_not_probe_database(monkeypatch):
    app = create_app(Settings(_env_file=None))
    def unexpected_connection():
        pytest.fail("Unconfigured readiness must not acquire a database connection")
    monkeypatch.setattr(app.state.db, "connection", unexpected_connection)
    with TestClient(app) as client:
        assert client.get("/ready").json() == {"status": "not_ready", "code": "configuration_missing"}
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize("failure", [psycopg.OperationalError("secret-host password=do-not-leak"), TimeoutError("secret-host")])
def test_database_failures_are_safe_and_distinct(monkeypatch, failure):
    app = create_app(configured_settings())

    async def noop():
        pass

    @asynccontextmanager
    async def unavailable():
        raise failure
        yield  # pragma: no cover

    monkeypatch.setattr(app.state.db, "open", noop)
    monkeypatch.setattr(app.state.db, "close", noop)
    monkeypatch.setattr(app.state.db, "connection", unavailable)
    with TestClient(app) as client:
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready", "code": "database_unavailable"}
        assert "secret-host" not in response.text
        assert client.get("/health").status_code == 200
