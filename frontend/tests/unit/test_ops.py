"""The operator probe is aggregate, read-only, bounded, and secret-safe."""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import recorder.ops as ops


def metrics(**kwargs):
    queue = kwargs.get("queue", [])
    retention = kwargs.get("retention", {"overdue": 0, "cleanup_pending": 0, "oldest_backlog_seconds": 0})
    heartbeat = kwargs.get("heartbeat", {"registered": 2, "fresh": 1, "latest_age_seconds": 15})
    return ops.evaluate(queue, retention, heartbeat, kwargs.get("thresholds", ops.Thresholds()))


def test_idle_worker_with_fresh_heartbeat_is_healthy_and_metrics_are_aggregate():
    result = metrics()
    assert result["healthy"] and not result["issues"]
    assert result["queue"]["counts"]["main"]["queued"] == 0
    assert result["main_worker"]["fresh_heartbeats"] == 1
    assert result["queue"]["uncertain_jobs"] == 0


def test_database_schema_drift_cannot_hide_unrecognized_pending_work_or_emit_untrusted_keys():
    result = metrics(queue=[{"worker_type": "private-untrusted-value", "state": "unexpected", "count": 1,
                             "oldest_queued_seconds": 0, "expired_running": 0}])
    assert not result["healthy"] and result["issues"] == ["unrecognized_queue_state"]
    assert "private-untrusted-value" not in str(result)


@pytest.mark.parametrize("state,count,age,expired,expected", [
    ("queued", 1, 301, 0, "queue_age_exceeded"),
    ("uncertain", 1, 0, 0, "uncertain_jobs_require_review"),
    ("dead", 1, 0, 0, "dead_jobs_require_review"),
    ("running", 1, 0, 1, "expired_running_leases"),
])
def test_stalled_and_charge_uncertain_work_triggers_operator_alert(state, count, age, expired, expected):
    result = metrics(queue=[{"worker_type": "main", "state": state, "count": count,
                             "oldest_queued_seconds": age, "expired_running": expired,
                             "secret_customer_text": "must never be returned"}])
    assert not result["healthy"] and expected in result["issues"]
    assert "must never be returned" not in str(result)


@pytest.mark.parametrize("retention,expected", [
    ({"overdue": 100, "cleanup_pending": 1, "oldest_backlog_seconds": 1}, "retention_backlog_exceeded"),
    ({"overdue": 0, "cleanup_pending": 1, "oldest_backlog_seconds": 3601}, "retention_age_exceeded"),
])
def test_retention_delay_and_backlog_are_visible(retention, expected):
    result = metrics(retention=retention)
    assert not result["healthy"] and expected in result["issues"]


@pytest.mark.parametrize("registered,age", [(0, None), (5, 361)])
def test_absent_or_stale_worker_is_unhealthy_even_when_queue_is_empty(registered, age):
    result = metrics(heartbeat={"registered": registered, "fresh": 0, "latest_age_seconds": age})
    assert result["issues"] == ["main_worker_heartbeat_missing_or_stale"]


def test_operator_threshold_can_acknowledge_a_known_hold_without_changing_jobs():
    result = metrics(queue=[{"worker_type": "inference", "state": "uncertain", "count": 1,
                             "oldest_queued_seconds": 0, "expired_running": 0}],
                     thresholds=ops.Thresholds(max_uncertain_jobs=1))
    assert result["healthy"] and result["queue"]["uncertain_jobs"] == 1


@pytest.mark.asyncio
async def test_probe_uses_read_only_transaction_bounded_statements_and_closes_pool(monkeypatch):
    statements = []
    class Connection:
        async def execute(self, statement, params=()):
            statements.append((statement, params))
    class Database:
        opened = closed = False
        async def open(self): self.opened = True
        async def close(self): self.closed = True
        @asynccontextmanager
        async def connection(self): yield Connection()
    async def many(connection, statement, params=()):
        await connection.execute(statement, params)
        return []
    async def one(connection, statement, params=()):
        await connection.execute(statement, params)
        if statement == ops.RETENTION_SQL:
            return {"overdue": 0, "cleanup_pending": 0, "oldest_backlog_seconds": 0}
        assert statement == ops.HEARTBEAT_SQL and params == (360,)
        return {"registered": 1, "fresh": 1, "latest_age_seconds": 1}
    monkeypatch.setattr(ops, "many", many)
    monkeypatch.setattr(ops, "one", one)
    database = Database()
    result = await ops.status(SimpleNamespace(database_url=True), ops.Thresholds(), database)
    assert result["healthy"] and database.opened and database.closed
    assert statements[0][0] == "set transaction read only"
    assert "statement_timeout" in statements[1][0] and "5s" in statements[1][0]
    assert not any(statement.strip().lower().startswith(("insert", "update", "delete")) for statement, _ in statements)


def test_cli_dependency_errors_are_sanitized_json_and_exit_two(monkeypatch, capsys):
    def settings(): raise RuntimeError("postgres://username:private-password@host/database")
    monkeypatch.setattr(ops, "get_settings", settings)
    assert ops.main(["status"]) == 2
    output = capsys.readouterr()
    assert '"issues":["ops_dependency_or_configuration_unavailable"]' in output.out
    assert "private-password" not in output.out + output.err


@pytest.mark.parametrize("healthy,exit_code", [(True, 0), (False, 1)])
def test_cli_health_controls_exit_code_and_uses_lease_aware_heartbeat_default(monkeypatch, capsys, healthy, exit_code):
    monkeypatch.setattr(ops, "get_settings", lambda: SimpleNamespace(worker_lease_seconds=900))
    async def status(settings, thresholds):
        assert thresholds.max_heartbeat_age_seconds == 1800
        return {"healthy": healthy, "issues": [] if healthy else ["dead_jobs_require_review"]}
    monkeypatch.setattr(ops, "status", status)
    assert ops.main(["status"]) == exit_code
    assert '"healthy"' in capsys.readouterr().out
