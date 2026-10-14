"""Pool recovery happens before a transaction, never by replaying its work."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import shutil
import subprocess
import tempfile

import psycopg
from psycopg.pq import TransactionStatus
from psycopg_pool import PoolTimeout
import pytest

from recorder.config import Settings
from recorder.db import Database, one


@pytest.fixture(scope="module")
def database_url():
    if not all(shutil.which(command) for command in ("initdb", "pg_ctl")):
        pytest.skip("Native PostgreSQL binaries are required for pool recovery checks")
    with tempfile.TemporaryDirectory(prefix="recorder-pool-test-") as temporary:
        path = Path(temporary)
        subprocess.run(
            ["initdb", "-D", str(path / "data"), "-A", "trust", "--no-locale", "-E", "UTF8"],
            check=True, capture_output=True,
        )
        # A private Unix socket only: no hosted project or existing database.
        subprocess.run(
            ["pg_ctl", "-D", str(path / "data"), "-l", str(path / "postgres.log"),
             "-o", f"-p 55443 -h '' -k {path}", "-w", "start"],
            check=True, capture_output=True,
        )
        try:
            yield f"postgresql:///postgres?host={path}&port=55443"
        finally:
            subprocess.run(
                ["pg_ctl", "-D", str(path / "data"), "-m", "fast", "-w", "stop"],
                check=True, capture_output=True,
            )


@asynccontextmanager
async def database(database_url):
    db = Database(Settings(
        _env_file=None, database_url=database_url, pool_min_size=1, pool_max_size=1,
    ))
    await db.open()
    try:
        yield db
    finally:
        await db.close()


async def timeout_settings(connection):
    row = await one(connection, "select current_setting('statement_timeout') as statement, current_setting('idle_in_transaction_session_timeout') as idle")
    return row["statement"], row["idle"]


@pytest.mark.asyncio
@pytest.mark.parametrize("rollback", [False, True])
async def test_transaction_timeouts_override_session_defaults_and_restore_them_on_return(database_url, rollback):
    async with database(database_url) as db:
        connection = await db.pool.getconn()
        try:
            original_pid = (await one(connection, "select pg_backend_pid() as pid"))["pid"]
            await connection.execute("select set_config('statement_timeout','3s',false), set_config('idle_in_transaction_session_timeout','4s',false)")
            assert await timeout_settings(connection) == ("3s", "4s")
        finally:
            await db.pool.putconn(connection)

        async def business_transaction():
            async with db.connection() as connection:
                assert (await one(connection, "select pg_backend_pid() as pid"))["pid"] == original_pid
                assert await timeout_settings(connection) == ("20s", "20s")
                if rollback:
                    raise RuntimeError("Rollback this transaction")

        if rollback:
            with pytest.raises(RuntimeError, match="Rollback this transaction"):
                await business_transaction()
        else:
            await business_transaction()

        connection = await db.pool.getconn()
        try:
            assert (await one(connection, "select pg_backend_pid() as pid"))["pid"] == original_pid
            assert await timeout_settings(connection) == ("3s", "4s")
        finally:
            await db.pool.putconn(connection)


@pytest.mark.asyncio
async def test_timeout_overrides_follow_savepoints_and_do_not_leak_into_session_defaults(database_url):
    async with database(database_url) as db:
        connection = await db.pool.getconn()
        try:
            await connection.execute("select set_config('statement_timeout','3s',false), set_config('idle_in_transaction_session_timeout','4s',false)")
        finally:
            await db.pool.putconn(connection)

        async with db.connection() as connection:
            assert await timeout_settings(connection) == ("20s", "20s")
            await connection.execute("select set_config('statement_timeout','11s',true), set_config('idle_in_transaction_session_timeout','13s',true)")
            with pytest.raises(RuntimeError, match="savepoint failure"):
                async with connection.transaction():
                    assert await timeout_settings(connection) == ("11s", "13s")
                    await connection.execute("select set_config('statement_timeout','5s',true), set_config('idle_in_transaction_session_timeout','6s',true)")
                    assert await timeout_settings(connection) == ("5s", "6s")
                    raise RuntimeError("savepoint failure")
            assert await timeout_settings(connection) == ("11s", "13s")
            async with connection.transaction():
                await connection.execute("select set_config('statement_timeout','7s',true), set_config('idle_in_transaction_session_timeout','9s',true)")
            assert await timeout_settings(connection) == ("7s", "9s")

        connection = await db.pool.getconn()
        try:
            assert await timeout_settings(connection) == ("3s", "4s")
        finally:
            await db.pool.putconn(connection)


@pytest.mark.asyncio
async def test_server_closed_idle_connection_is_replaced_before_caller(database_url):
    async with database(database_url) as db:
        async with db.connection() as connection:
            first_pid = (await one(connection, "select pg_backend_pid() as pid"))["pid"]
        with psycopg.connect(database_url, autocommit=True) as admin:
            assert admin.execute("select pg_terminate_backend(%s)", (first_pid,)).fetchone()[0]

        calls = 0
        async with db.connection() as connection:
            calls += 1
            second_pid = (await one(connection, "select pg_backend_pid() as pid"))["pid"]
            assert second_pid != first_pid
            assert (await one(connection, "select 1 as healthy"))["healthy"] == 1
        assert calls == 1
        assert db.pool.get_stats().get("connections_lost", 0) == 1


@pytest.mark.asyncio
async def test_explicit_transaction_commits_rolls_back_and_clears_local_claims(database_url):
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute("create table pool_atomic_records (value int primary key)")
    async with database(database_url) as db:
        async with db.connection() as connection:
            assert connection.autocommit
            assert connection.pgconn.transaction_status == TransactionStatus.INTRANS
            await connection.execute("select set_config('request.jwt.claim.sub', 'user-a', true)")
            await connection.execute("insert into pool_atomic_records values (1)")
        with pytest.raises(RuntimeError, match="caller failure"):
            async with db.connection() as connection:
                assert not (await one(connection, "select current_setting('request.jwt.claim.sub', true) as sub"))["sub"]
                await connection.execute("insert into pool_atomic_records values (2)")
                raise RuntimeError("caller failure")
        async with db.connection() as connection:
            row = await one(connection, "select array_agg(value order by value) as values from pool_atomic_records")
            assert row["values"] == [1]


@pytest.mark.asyncio
async def test_connection_loss_during_write_is_not_replayed(database_url):
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute("create table pool_no_replay (value int primary key)")
    calls = 0
    async with database(database_url) as db:
        with pytest.raises(psycopg.OperationalError):
            async with db.connection() as connection:
                calls += 1
                pid = (await one(connection, "select pg_backend_pid() as pid"))["pid"]
                await connection.execute("insert into pool_no_replay values (1)")
                with psycopg.connect(database_url, autocommit=True) as admin:
                    assert admin.execute("select pg_terminate_backend(%s)", (pid,)).fetchone()[0]
                await connection.execute("select 1")
        assert calls == 1
        async with db.connection() as connection:
            assert (await one(connection, "select count(*) as count from pool_no_replay"))["count"] == 0
            await connection.execute("insert into pool_no_replay values (2)")
        with psycopg.connect(database_url) as admin:
            assert admin.execute("select value from pool_no_replay").fetchall() == [(2,)]


@pytest.mark.asyncio
async def test_nested_transaction_retains_savepoint_semantics(database_url):
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute("create table pool_savepoints (value int primary key)")
    async with database(database_url) as db:
        async with db.connection() as connection:
            await connection.execute("insert into pool_savepoints values (1)")
            with pytest.raises(RuntimeError, match="savepoint failure"):
                async with connection.transaction():
                    await connection.execute("insert into pool_savepoints values (2)")
                    raise RuntimeError("savepoint failure")
            await connection.execute("insert into pool_savepoints values (3)")
        with psycopg.connect(database_url) as admin:
            assert admin.execute("select value from pool_savepoints order by value").fetchall() == [(1,), (3,)]


class FakeConnection:
    def __init__(self, setup_failure=None):
        self.transaction_outcomes = []
        self.executed = []
        self.setup_failure = setup_failure

    async def execute(self, sql, params=()):
        self.executed.append((sql, params))
        if self.setup_failure:
            raise self.setup_failure

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    @asynccontextmanager
    async def transaction(self):
        try:
            yield self
        except BaseException:
            self.transaction_outcomes.append("rollback")
            raise
        else:
            self.transaction_outcomes.append("commit")


class FakePool:
    def __init__(self, *, hanging=False, setup_failure=None):
        self.hanging = hanging
        self.connection = FakeConnection(setup_failure)
        self.acquisitions = 0
        self.returns = 0
        self.cancelled = False

    async def getconn(self, timeout):
        self.acquisitions += 1
        if self.hanging:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return self.connection

    async def putconn(self, connection):
        assert connection is self.connection
        self.returns += 1


@pytest.mark.asyncio
async def test_timeout_setup_failure_rolls_back_and_returns_connection_before_business_work():
    db = Database(Settings(_env_file=None))
    db.pool = FakePool(setup_failure=RuntimeError("Transaction timeout setup failed"))
    with pytest.raises(RuntimeError, match="Transaction timeout setup failed"):
        async with db.connection():
            pytest.fail("Business work must not run without transaction timeouts")
    assert db.pool.acquisitions == 1
    assert db.pool.returns == 1
    assert db.pool.connection.transaction_outcomes == ["rollback"]
    assert len(db.pool.connection.executed) == 1
    setup_sql = db.pool.connection.executed[0][0]
    assert "set_config('statement_timeout'" in setup_sql
    assert "set_config('idle_in_transaction_session_timeout'" in setup_sql


@pytest.mark.asyncio
async def test_hanging_checkout_is_bounded_before_business_work(monkeypatch):
    db = Database(Settings(_env_file=None))
    db.pool = FakePool(hanging=True)
    monkeypatch.setattr(db, "ACQUIRE_TIMEOUT_SECONDS", 0.02)
    with pytest.raises(PoolTimeout, match="healthy database connection"):
        async with db.connection():
            pytest.fail("A timed-out connection must never reach business work")
    assert db.pool.acquisitions == 1
    assert db.pool.cancelled
    assert db.pool.returns == 0


@pytest.mark.asyncio
async def test_caller_cancellation_during_checkout_remains_cancelled():
    db = Database(Settings(_env_file=None))
    db.pool = FakePool(hanging=True)

    async def operation():
        async with db.connection():
            pytest.fail("A cancelled checkout must never reach business work")

    task = asyncio.create_task(operation())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert db.pool.cancelled
    assert db.pool.acquisitions == 1
    assert db.pool.returns == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, RuntimeError("caller failure"), asyncio.CancelledError()])
async def test_checkout_deadline_does_not_cover_business_work_and_always_returns_connection(monkeypatch, failure):
    db = Database(Settings(_env_file=None))
    db.pool = FakePool()
    monkeypatch.setattr(db, "ACQUIRE_TIMEOUT_SECONDS", 0.02)

    async def operation():
        async with db.connection():
            await asyncio.sleep(0.04)
            if failure is not None:
                raise failure

    if failure is None:
        await operation()
    else:
        with pytest.raises(type(failure)):
            await operation()
    assert db.pool.acquisitions == 1
    assert db.pool.returns == 1
    assert db.pool.connection.transaction_outcomes == ["commit" if failure is None else "rollback"]
