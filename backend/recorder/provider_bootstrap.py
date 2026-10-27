"""Provision tenant metadata for provider credentials held only by the backend.

No network checks run here: readiness means the server has configuration, not
that a paid request has been tested. Provider errors remain ordinary job errors.
"""

from __future__ import annotations

import hashlib

from psycopg.types.json import Jsonb

from .db import many, one
from .openai_translator import OPENAI_TRANSLATION_MODEL


def server_credentials(settings, engine: str) -> dict:
    name = {"elevenlabs": "elevenlabs_api_key", "openai-translator": "openai_api_key"}.get(engine)
    value = getattr(settings, name, None) if name else None
    key = value.get_secret_value() if hasattr(value, "get_secret_value") else value
    return {"api_key": key} if key else {}


def managed_provider_specs(settings):
    for engine in ("elevenlabs", "openai-translator"):
        credentials = server_credentials(settings, engine)
        model = getattr(settings, "openai_translation_model", OPENAI_TRANSLATION_MODEL) if engine == "openai-translator" else "scribe_v2"
        yield engine, credentials, {"platform_managed": True, "model": model}


async def ensure_managed_providers(connection, organisation_id, settings):
    """Run inside the caller's transaction, before selecting workspace engines.

    Each workspace keeps separate connection/run/job identities. A shared
    credential_scope enforces one distributed provider concurrency limit.
    """
    translator_id = None
    maximum = getattr(settings, "managed_provider_concurrency", 4)
    for engine, credentials, config in managed_provider_specs(settings):
        await connection.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", (f"managed-provider:{organisation_id}:{engine}",))
        old = await one(
            connection, "select * from engine_connections where organisation_id=%s and engine_id=%s for update", (organisation_id, engine)
        )
        if not credentials:
            code = "engine_unconfigured" if engine == "elevenlabs" else "translator_unconfigured"
            if old and old.get("config", {}).get("platform_managed") and (
                old.get("credential_scope") or old.get("tested_at") or old.get("last_error_code") != code
            ):
                await connection.execute(
                    """update processing_jobs set
                    state=case when state='running' and submission_state in ('submitting','accepted','uncertain') then 'uncertain' else 'blocked' end,
                    error_code=%s,lease_token=null,lease_until=null,updated_at=now()
                    where connection_id=%s and state in ('queued','retry','running')""", (code, old["id"])
                )
                await connection.execute(
                    """update engine_connections set state='configured',tested_at=null,credential_scope=null,
                    generation=generation+1,last_error_code=%s,updated_at=now() where id=%s""", (code, old["id"])
                )
            continue
        scope = engine + ":" + hashlib.sha256(credentials["api_key"].encode()).hexdigest()
        metadata = {"platform_managed": True, "readiness_source": "server_environment", "provider_access_verified": False}
        changed = bool(old and (old.get("credential_scope") != scope or old.get("config") != config))
        if changed:
            # Existing accepted calls are never resubmitted. Unsubmitted work
            # remains eligible for an explicit safe retry with the new generation.
            await connection.execute(
                """update processing_jobs set
                state=case when state='running' and submission_state in ('submitting','accepted','uncertain') then 'uncertain' else 'blocked' end,
                error_code='connection_rotated',lease_token=null,lease_until=null,updated_at=now()
                where connection_id=%s and state in ('queued','retry','running')""",
                (old["id"],),
            )
        if not old:
            row = await one(
                connection,
                """insert into engine_connections
                (organisation_id,engine_id,state,config,safe_metadata,credential_scope,tested_at,max_concurrency)
                values(%s,%s,'connected',%s,%s,%s,now(),%s) returning *""",
                (organisation_id, engine, Jsonb(config), Jsonb(metadata), scope, maximum),
            )
        elif changed:
            row = await one(
                connection,
                """update engine_connections set state='connected',config=%s,safe_metadata=%s,
                credential_scope=%s,generation=generation+%s,tested_at=now(),last_error_code=null,max_concurrency=%s,updated_at=now()
                where id=%s returning *""",
                (Jsonb(config), Jsonb(metadata), scope, int(changed), maximum, old["id"]),
            )
        else:
            row = old
            if old.get("max_concurrency") != maximum:
                await connection.execute("update engine_connections set max_concurrency=%s,updated_at=now() where id=%s", (maximum, old["id"]))
        if old and old.get("max_concurrency") != maximum:
            await connection.execute("delete from recorder_private.provider_limits where scope=%s", (scope,))
        if engine == "openai-translator":
            translator_id = row["id"]
    if translator_id:
        await connection.execute(
            "update workspace_engine_settings set translator_connection_id=%s,default_engine_id='elevenlabs',allowed_engine_ids=array['elevenlabs'] where organisation_id=%s",
            (translator_id, organisation_id),
        )


async def bootstrap_managed_providers(settings, db):
    """Optional startup sweep; lazy per-workspace provisioning is authoritative."""
    async with db.connection() as connection:
        organisations = await many(connection, "select id from organisations order by id")
    for organisation in organisations:
        async with db.connection() as connection:
            await ensure_managed_providers(connection, organisation["id"], settings)


def bootstrap_local_managed_providers(settings, store):
    """Local equivalent: metadata only; shared keys never enter SQLite secrets."""
    from .local_store import now

    for engine, credentials, config in managed_provider_specs(settings):
        old = store.connection(engine)
        if not credentials:
            code = "engine_unconfigured" if engine == "elevenlabs" else "translator_unconfigured"
            if old and old.get("config", {}).get("platform_managed") and (
                old.get("credential_scope") or old.get("tested_at") or old.get("last_error_code") != code
            ):
                with store.transaction() as db:
                    store._block_connection_jobs(db, engine, code)
                store.patch("connections", engine, {"state": "configured", "tested_at": None,
                    "credential_scope": None, "generation": old.get("generation", 1) + 1,
                    "last_error_code": code, "updated_at": now()})
            continue
        scope = engine + ":" + hashlib.sha256(credentials["api_key"].encode()).hexdigest()
        changed = bool(old and (old.get("credential_scope") != scope or old.get("config") != config))
        if old and not changed:
            continue  # Reading configuration must not erase a provider failure.
        if changed:
            with store.transaction() as db:
                store._block_connection_jobs(db, engine, "connection_rotated")
        from uuid import uuid4

        store.put(
            "connections",
            engine,
            {
                "id": old["id"] if old else str(uuid4()),
                "engine_id": engine,
                "state": "connected",
                "generation": (old.get("generation", 1) if old else 1) + int(changed),
                "config": config,
                "credential_scope": scope,
                "tested_at": now(),
                "safe_metadata": {"platform_managed": True, "readiness_source": "server_environment", "provider_access_verified": False},
                "last_error_code": None,
                "updated_at": now(),
            },
        )
