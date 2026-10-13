import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout
from .config import Settings
from .errors import fail


class Database:
    # This deadline applies only to obtaining a healthy connection. Once the
    # transaction starts, its statements retain the PostgreSQL timeout below.
    ACQUIRE_TIMEOUT_SECONDS = 10

    def __init__(self, settings: Settings):
        self.pool = AsyncConnectionPool(
            conninfo=settings.database_url.get_secret_value() if settings.database_url else "",
            min_size=settings.pool_min_size, max_size=settings.pool_max_size,
            kwargs={"row_factory": dict_row, "autocommit": True, "connect_timeout": 10, "options": "-c statement_timeout=20000 -c idle_in_transaction_session_timeout=20000"},
            check=AsyncConnectionPool.check_connection,
            max_idle=60, max_lifetime=1800,
            open=False, timeout=self.ACQUIRE_TIMEOUT_SECONDS,
        ) if settings.database_url else None

    async def open(self) -> None:
        if self.pool:
            await self.pool.open(wait=False)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection[dict[str, Any]]]:
        if not self.pool:
            fail("database_not_configured", 503)
        # The pool's own acquisition deadline does not cover a hanging checkout
        # health check. Bound both here, before any business statement executes.
        try:
            async with asyncio.timeout(self.ACQUIRE_TIMEOUT_SECONDS):
                connection = await self.pool.getconn(timeout=self.ACQUIRE_TIMEOUT_SECONDS)
        except TimeoutError:
            # Preserve the API's existing safe 503 handler for pool exhaustion;
            # genuine caller cancellation still propagates as CancelledError.
            raise PoolTimeout("Timed out obtaining a healthy database connection") from None
        try:
            async with connection:
                async with connection.transaction():
                    # Transaction poolers may ignore startup connection options.
                    # Apply deadlines on the server transaction that owns our work.
                    await connection.execute("select set_config('statement_timeout','20s',true), set_config('idle_in_transaction_session_timeout','20s',true)")
                    yield connection
        finally:
            # Never retry a transaction: only unhealthy idle connections are
            # replaced by the pool before this context yields to the caller.
            await self.pool.putconn(connection)


async def one(connection: AsyncConnection, query: str, params: tuple = ()) -> dict | None:
    cursor = await connection.execute(query, params)
    return await cursor.fetchone()


async def many(connection: AsyncConnection, query: str, params: tuple = ()) -> list[dict]:
    cursor = await connection.execute(query, params)
    return await cursor.fetchall()
