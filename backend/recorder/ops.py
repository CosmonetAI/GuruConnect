"""Read-only aggregate production probe: python -m recorder.ops status.

Exit 0 means the configured thresholds passed, 1 signals an operational alert,
and 2 signals unavailable configuration/dependencies. Output contains no
customer text, object paths, credentials, or worker/job identifiers.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging

from .config import get_settings
from .db import Database, many, one

STATES = ("queued", "running", "retry", "blocked", "uncertain", "succeeded", "failed", "cancelled", "dead")
WORKER_TYPES = ("main", "inference")


@dataclass(frozen=True)
class Thresholds:
    max_queue_age_seconds: int = 300
    max_uncertain_jobs: int = 0
    max_dead_jobs: int = 0
    max_retention_backlog: int = 100
    max_retention_age_seconds: int = 3600
    max_heartbeat_age_seconds: int = 360


QUEUE_SQL = """
select worker_type,state,count(*) as count,
       coalesce(max(greatest(0,extract(epoch from (now()-created_at))))
           filter(where state in ('queued','retry')),0) as oldest_queued_seconds,
       count(*) filter(where state='running' and lease_until<now()) as expired_running
from processing_jobs group by worker_type,state
"""
RETENTION_SQL = """
select count(*) filter(where c.deleted_at is null) as overdue,
       count(*) filter(where c.deleted_at is not null) as cleanup_pending,
       coalesce(max(greatest(0,extract(epoch from
           (now()-coalesce(c.deleted_at,c.created_at+make_interval(days=>o.retention_days)))))),0)
           as oldest_backlog_seconds
from conversations c join organisations o on o.id=c.organisation_id
where (c.deleted_at is null and c.created_at<now()-make_interval(days=>o.retention_days))
   or (c.deleted_at is not null and c.upload_state<>'saved')
"""
HEARTBEAT_SQL = """
select count(*) as registered,
       count(*) filter(where last_seen_at>=now()-make_interval(secs=>%s)) as fresh,
       min(greatest(0,extract(epoch from (now()-last_seen_at)))) as latest_age_seconds
from worker_heartbeats where worker_type='main'
"""


def evaluate(queue_rows: list[dict], retention: dict, heartbeat: dict, thresholds: Thresholds) -> dict:
    counts = {worker: {state: 0 for state in STATES} for worker in WORKER_TYPES}
    oldest = expired = 0
    issues = []
    for row in queue_rows:
        # Output only the protocol's fixed keys, including when DB data drifts.
        if row["worker_type"] not in counts or row["state"] not in STATES:
            if "unrecognized_queue_state" not in issues:
                issues.append("unrecognized_queue_state")
            continue
        counts[row["worker_type"]][row["state"]] = int(row["count"])
        oldest = max(oldest, float(row["oldest_queued_seconds"]))
        expired += int(row["expired_running"])
    uncertain = sum(worker["uncertain"] for worker in counts.values())
    dead = sum(worker["dead"] for worker in counts.values())
    overdue = int(retention["overdue"])
    pending = int(retention["cleanup_pending"])
    retention_age = float(retention["oldest_backlog_seconds"])
    fresh = int(heartbeat["fresh"])
    if oldest > thresholds.max_queue_age_seconds:
        issues.append("queue_age_exceeded")
    if uncertain > thresholds.max_uncertain_jobs:
        issues.append("uncertain_jobs_require_review")
    if dead > thresholds.max_dead_jobs:
        issues.append("dead_jobs_require_review")
    if expired:
        issues.append("expired_running_leases")
    if overdue + pending > thresholds.max_retention_backlog:
        issues.append("retention_backlog_exceeded")
    if retention_age > thresholds.max_retention_age_seconds:
        issues.append("retention_age_exceeded")
    if not fresh:
        issues.append("main_worker_heartbeat_missing_or_stale")
    return {
        "schema_version": 1,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "healthy": not issues,
        "issues": issues,
        "thresholds": asdict(thresholds),
        "queue": {"counts": counts, "oldest_queued_age_seconds": round(oldest, 3),
                  "uncertain_jobs": uncertain, "dead_jobs": dead, "expired_running_jobs": expired},
        "retention": {"overdue_conversations": overdue, "cleanup_pending_conversations": pending,
                      "backlog": overdue + pending, "oldest_backlog_age_seconds": round(retention_age, 3)},
        "main_worker": {"registered_heartbeats": int(heartbeat["registered"]), "fresh_heartbeats": fresh,
                        "latest_heartbeat_age_seconds": None if heartbeat["latest_age_seconds"] is None
                        else round(float(heartbeat["latest_age_seconds"]), 3)},
    }


async def status(settings, thresholds: Thresholds, database=None) -> dict:
    if not settings.database_url:
        raise ValueError("database_not_configured")
    database = database or Database(settings)
    try:
        async with asyncio.timeout(15):
            await database.open()
            async with database.connection() as connection:
                await connection.execute("set transaction read only")
                await connection.execute("select set_config('statement_timeout','5s',true)")
                queue = await many(connection, QUEUE_SQL)
                retention = await one(connection, RETENTION_SQL)
                heartbeat = await one(connection, HEARTBEAT_SQL, (thresholds.max_heartbeat_age_seconds,))
            return evaluate(queue, retention, heartbeat, thresholds)
    finally:
        await asyncio.wait_for(database.close(), timeout=2)


def nonnegative(value: str) -> int:
    parsed = int(value)
    if not 0 <= parsed <= 1_000_000_000:
        raise argparse.ArgumentTypeError("threshold must be an integer from 0 to 1000000000")
    return parsed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    probe = subparsers.add_parser("status", help="read bounded aggregate production health metrics")
    for field, default in asdict(Thresholds()).items():
        probe.add_argument(f"--{field.replace('_', '-')}", type=nonnegative,
                           default=None if field == "max_heartbeat_age_seconds" else default)
    options = parser.parse_args(argv)
    # Pool connection exceptions must not print connection strings or tracebacks.
    logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)
    try:
        settings = get_settings()
        configured = {name: getattr(options, name) for name in asdict(Thresholds())}
        if configured["max_heartbeat_age_seconds"] is None:
            configured["max_heartbeat_age_seconds"] = max(120, settings.worker_lease_seconds * 2)
        result = asyncio.run(status(settings, Thresholds(**configured)))
    except Exception:
        result = {"schema_version": 1, "healthy": False, "issues": ["ops_dependency_or_configuration_unavailable"]}
        print(json.dumps(result, separators=(",", ":"), allow_nan=False))
        return 2
    print(json.dumps(result, separators=(",", ":"), allow_nan=False))
    return 0 if result["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
