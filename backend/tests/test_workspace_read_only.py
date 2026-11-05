import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from recorder import security
from recorder.security import User


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("role", ["salesperson", "receptionist"])
def test_owner_cannot_write_in_member_workspace(monkeypatch, method, role):
    results = iter([
        {"role": "owner"},
        {"role": role, "status": "active"},
    ])

    @asynccontextmanager
    async def connection():
        yield object()

    class Database:
        def connection(self):
            return connection()

    async def one(_connection, _query, _params):
        return next(results)

    monkeypatch.setattr(security, "one", one)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=Database())), method=method)
    user = User(UUID("00000000-0000-0000-0000-000000000001"), "owner@example.test", "Owner")

    with pytest.raises(HTTPException) as error:
        asyncio.run(security.workspace(
            request,
            user,
            UUID("00000000-0000-0000-0000-000000000002"),
            UUID("00000000-0000-0000-0000-000000000003"),
        ))

    assert error.value.status_code == 403
    assert error.value.detail["code"] == "workspace_read_only"


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_owner_can_read_member_workspace(monkeypatch, method):
    results = iter([
        {"role": "owner"},
        {"role": "receptionist", "status": "active"},
    ])

    @asynccontextmanager
    async def connection():
        yield object()

    class Database:
        def connection(self):
            return connection()

    async def one(_connection, _query, _params):
        return next(results)

    monkeypatch.setattr(security, "one", one)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=Database())), method=method)
    user = User(UUID("00000000-0000-0000-0000-000000000001"), "owner@example.test", "Owner")

    context = asyncio.run(security.workspace(
        request,
        user,
        UUID("00000000-0000-0000-0000-000000000002"),
        UUID("00000000-0000-0000-0000-000000000003"),
    ))

    assert context.role == "receptionist"
    assert context.member_id == UUID("00000000-0000-0000-0000-000000000003")


def test_owner_can_write_in_own_workspace(monkeypatch):
    results = iter([
        {"role": "owner"},
        {"role": "owner", "status": "active"},
    ])

    @asynccontextmanager
    async def connection():
        yield object()

    class Database:
        def connection(self):
            return connection()

    async def one(_connection, _query, _params):
        return next(results)

    monkeypatch.setattr(security, "one", one)
    user_id = UUID("00000000-0000-0000-0000-000000000001")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=Database())), method="POST")

    context = asyncio.run(security.workspace(
        request,
        User(user_id, "owner@example.test", "Owner"),
        UUID("00000000-0000-0000-0000-000000000002"),
        user_id,
    ))

    assert context.role == "owner"
    assert context.member_id == user_id
