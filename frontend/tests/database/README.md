# Database verification

The versioned migrations create the complete recorder schema, private buckets,
RLS, server-only credentials, immutable audio/results, recording leases, pgmq
queues, and transactional job RPCs. Apply files in lexical order using the
project's remote migration script. Never apply `native_fixture.sql` to Supabase.

## Hosted development verification

Use an explicitly designated **development** project, apply the migrations, and
stop coordinating workers for this test. The SQL suite refuses an active queue.
It creates disposable Auth/database fixture rows inside one transaction, tests
roles and policies, and rolls all data changes back. It does not send verification
emails, upload physical Storage objects, exercise HTTP JWT verification, or test
Realtime delivery. Those need the API/browser integration suites and real
authorised test accounts.

```bash
export TEST_DATABASE_URL='postgresql://...designated-development.../postgres'
export GURU_TEST_DATABASE_CONFIRM=development
# Configure libpq PGSSLROOTCERT when the hosting provider needs a custom CA.
python3 tests/database/run_remote.py
```

The runner requires TLS verification and never falls back to the application
`DATABASE_URL`. Without the two explicit test variables it prints **SKIPPED**.
The current workspace has no designated hosted development credentials, so
hosted migrations/RLS and real Storage/Auth/Realtime checks remain unexecuted.

## Independent native PostgreSQL verification

The native runner creates an entirely new temporary PostgreSQL cluster, installs
minimal Auth/Storage/pgmq **emulation**, applies the actual migrations, runs the
SQL checks, and shuts the server down. It does not use Docker or existing local
clusters. Native pgmq is emulated because stock PostgreSQL does not ship the
Supabase extension. This verifies PostgreSQL syntax, foreign keys, real RLS
roles, transactional updates, and contention logic; it cannot prove hosted
Supabase extension/API compatibility.

```bash
brew install postgresql
bash tests/database/run_native.sh
```

Set `GURU_NATIVE_TEST_PORT` if localhost port 55439 is occupied. A custom
`GURU_NATIVE_TEST_DIR` must not exist; the script refuses to reuse a directory.
The printed evidence directory retains fixture, migration, assertion, and
concurrency logs. The process allocates native PostgreSQL shared memory and
requires permission to bind a localhost socket in restricted environments.

Actual checks passed on 2026-10-12:

- All five migrations compiled on native PostgreSQL.
- 102 SQL assertions: two salespeople and same-name customers, cross-owner and
  workspace FKs, owner team access, active/revoked membership, secrets/queue/role
  escalation denial, immutable upload paths/hashes/sample ranges, 120-minute
  overflow, lease takeover and stopped-outbox recovery, input artifact readiness,
  worker revocation, stale/duplicate/late results, accepted-in-flight policy,
  deletion fencing, durable queue cleanup, rate limiting, and hardened functions.
- 22 concurrent recording metadata clients in one workspace persisted 44
  out-of-order chunk reservations and completed 22 jobs without duplicate result
  publication (1.873 seconds in this run).
- 100 clients across 20 workspaces persisted 200 reservations and completed 100
  jobs without duplicate publication (4.859 seconds in this run).

These timings are an emulated database test, not hosted ingestion latency,
speech accuracy, provider quota, or inference CPU/GPU capacity measurements.
Each client checks authorised visibility using a separate PostgreSQL session.
The stress script cannot run remotely and requires an emulation marker.

## Service contract

All mutation RPCs below are executable only by `service_role` (or the database
administrator), and the FastAPI service independently authorises each user and
resource. Browser users can read only authorised rows and update their own
profile preferences; they cannot claim jobs, write machine output, manage
memberships, or read credential material/fingerprints.

| RPC | Durable action |
| --- | --- |
| `schedule_job` | Dedupe ledger insert and pgmq send in one transaction; conflicts reject differing inputs |
| `claim_jobs` | Real queue visibility + ledger locks; workspace/credential admission; scoped native input readiness |
| `begin_job_submission` | Validate current cancellation, lease and connection generation before a paid external call |
| `renew_job` | Extend a current lease and queue visibility; reject revoked workers/cancelled/deleted content |
| `finish_job` | Fence stale results, persist an immutable result and archive the message transactionally |
| `retry_job` | Safe error classification, bounded backoff/jitter, uncertain submission hold and dead-letter path |
| `resume_job` | Explicit authorised recovery with audited generation transition; uncertain requests require reconciliation |
| `disconnect_connection` | Increment generation, block queued work, detach workspace secrets and optionally allow only accepted job IDs |
| `acquire_recording_lease` | One current recording per global account, explicit expired-lease takeover and new token/fence |
| `heartbeat_recording_lease` | Check current owner/membership and renew capture or unfinished upload-only recovery |
| `allow_api_request` | Distributed bounded rate window with private counters |

Keep provider calls outside database transactions. Call `finish_job` and publish
the transcript version in the **same** subsequent transaction; a version trigger
requires a successful job, and unique `transcript_versions.job_id` prevents
duplicate publication. `processing_runs` engine/configuration snapshots cannot
be rewritten. Source-linked translations must remain inside the same run.

Queue dispatch reserves capacity for progressive production windows while final
and comparison work exists, applies priority aging and balances active workspace
counts. Limits use a shared credential fingerprint across connections, so API
replicas cannot independently exceed the configured concurrency. Paid audio and
translation budgets are configurable and block inference independently of
recording/upload/playback assembly. External execution is at least once; the
system does not claim exactly-once vendor billing.

Private Storage responsibilities:

- `recorder-audio`: immutable mono 16 kHz PCM WAV chunks, maximum 320044 bytes;
  a server-created reservation and active owner membership are required.
- `recorder-playback`: authorised assembled WAV playback, maximum 230400044 bytes.
- `recorder-artifacts`: server-only input/provider/result/export artifacts, maximum
  230400044 bytes; WAV, JSON and documented export MIME types are allowed.

No browser update/delete policy permits upserts or overwriting a recording. API
confirmation still validates actual bytes/hash/WAV structure; database metadata
does not itself establish that the physical object exists or has matching bytes.
