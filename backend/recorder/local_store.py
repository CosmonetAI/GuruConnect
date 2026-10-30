"""Private, persistent development storage. Never used by the hosted SaaS API."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from filelock import FileLock

LOCAL_USER_ID = "00000000-0000-0000-0000-000000000001"
LOCAL_WORKSPACE_ID = "00000000-0000-0000-0000-000000000002"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def identifier(value: str) -> str:
    return str(UUID(str(value)))


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class LocalStore:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.database = self.root / "guruconnect.sqlite3"
        key_path = self.root / "credentials.key"
        # Lock a separate file so the native Windows lock cannot block key I/O.
        with FileLock(self.root / "credentials.key.lock", mode=0o600):
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            descriptor = os.open(key_path, flags, 0o600)
            with os.fdopen(descriptor, "r+b") as file:
                self._key = file.read().strip()
                if not self._key:
                    self._key = Fernet.generate_key()
                    file.write(self._key); file.flush(); os.fsync(file.fileno())
            os.chmod(key_path, 0o600)
        self._fernet = Fernet(self._key)
        self._signing_key = base64.urlsafe_b64decode(self._key)
        with self.transaction() as db:
            db.executescript("""
            create table if not exists records (table_name text not null, id text not null, data text not null, primary key(table_name,id));
            create table if not exists secrets (engine text primary key, ciphertext text not null);
            create table if not exists enquiries (number integer primary key autoincrement, id text not null unique, idempotency_key text not null unique, request_hash text not null, conversation_id text unique, data text not null);
            create table if not exists chunks (conversation_id text not null, part_id text not null, sequence integer not null, data text not null, uploaded integer not null default 0, confirmed integer not null default 0, primary key(conversation_id,part_id,sequence));
            create table if not exists jobs (id text primary key, conversation_id text not null, kind text not null, dedupe_key text not null unique, state text not null, payload text not null, result text, error_code text, submission_state text not null default 'not_submitted', lease_token text, lease_until real, worker_id text, created_at text not null, updated_at text not null);
            create index if not exists local_jobs_ready on jobs(state,created_at);
            """)
        os.chmod(self.database, 0o600)
        if not self.get("settings", "engine"):
            self.put("settings", "engine", {"default_engine_id": "elevenlabs", "allowed_engine_ids": ["elevenlabs"], "salesperson_engine_selection": False, "comparison_allowed": False, "budget_policy": {"max_audio_seconds_per_day": 600000}})
        if not self.get("profiles", LOCAL_USER_ID):
            self.put("profiles", LOCAL_USER_ID, {"id": LOCAL_USER_ID, "display_name": "Local user", "preferences": {}})

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("pragma journal_mode=WAL")
            db.execute("pragma busy_timeout=10000")
            db.execute("begin immediate")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, table: str, row_id: str) -> dict | None:
        with self.transaction() as db:
            if table == "jobs":
                row = db.execute("select * from jobs where id=?", (str(row_id),)).fetchone()
                return self._job(row) if row else None
            row = db.execute("select data from records where table_name=? and id=?", (table, str(row_id))).fetchone()
            return json.loads(row["data"]) if row else None

    def put(self, table: str, row_id: str, data: dict) -> dict:
        with self.transaction() as db:
            db.execute("insert into records values(?,?,?) on conflict(table_name,id) do update set data=excluded.data", (table, str(row_id), encoded(data)))
        return data

    def patch(self, table: str, row_id: str, updates: dict, expected: dict | None = None) -> dict:
        if table == "jobs":
            allowed = {"state", "error_code", "submission_state", "lease_until", "lease_token", "worker_id"}
            if set(updates) - allowed:
                raise ValueError("invalid_job_update")
            with self.transaction() as db:
                clauses = ",".join(f"{name}=?" for name in updates)
                db.execute(f"update jobs set {clauses},updated_at=? where id=?", (*updates.values(), now(), str(row_id)))
            return self.get(table, row_id) or {}
        with self.transaction() as db:
            row = db.execute("select data from records where table_name=? and id=?", (table, str(row_id))).fetchone()
            if not row:
                raise KeyError(row_id)
            old = json.loads(row["data"])
            if table == "conversations" and old.get("capture_state") == "deleted" and updates.get("capture_state") != "deleted":
                raise ValueError("conversation_deleted")
            if any(old.get(key) != value for key, value in (expected or {}).items()):
                raise ValueError("local_record_changed")
            data = {**old, **updates, "updated_at": now()}
            db.execute("update records set data=? where table_name=? and id=?", (encoded(data), table, str(row_id)))
        return data

    def list(self, table: str, filters: dict | None = None) -> list[dict]:
        with self.transaction() as db:
            if table == "jobs":
                values = [self._job(row) for row in db.execute("select * from jobs order by created_at,id")]
            else:
                values = [json.loads(row["data"]) for row in db.execute("select data from records where table_name=?", (table,))]
        return [row for row in values if all(row.get(key) == value for key, value in (filters or {}).items())]

    def connection(self, engine: str) -> dict | None:
        return self.get("connections", engine)

    def settings(self) -> dict:
        return self.get("settings", "engine") or {}

    def secrets(self, engine: str) -> dict:
        with self.transaction() as db:
            row = db.execute("select ciphertext from secrets where engine=?", (engine,)).fetchone()
        return json.loads(self._fernet.decrypt(row["ciphertext"].encode())) if row else {}

    def configure_connection(self, engine: str, credentials: dict, config: dict, idem: str, expected: int | None) -> dict:
        digest = hashlib.sha256(encoded({"engine_id": engine, "credentials": credentials, "config": config}).encode()).hexdigest()
        ciphertext = self._fernet.encrypt(encoded(credentials).encode()).decode()
        with self.transaction() as db:
            prior = db.execute("select data from records where table_name='connections' and id=?", (engine,)).fetchone()
            old = json.loads(prior["data"]) if prior else None
            if old and old.get("idempotency_key") == idem:
                if old.get("request_hash") != digest:
                    raise ValueError("idempotency_conflict")
                return old
            if expected is not None and (not old or old["generation"] != expected):
                raise ValueError("stale_connection_generation")
            data = {"id": old["id"] if old else str(uuid4()), "engine_id": engine, "state": "configured", "generation": (old["generation"] if old else 0) + 1, "config": config, "tested_at": None, "last_error_code": None, "safe_metadata": {}, "updated_at": now(), "idempotency_key": idem, "request_hash": digest}
            if old:
                self._block_connection_jobs(db, engine, "connection_rotated")
            db.execute("insert into records values('connections',?,?) on conflict(table_name,id) do update set data=excluded.data", (engine, encoded(data)))
            db.execute("insert into secrets values(?,?) on conflict(engine) do update set ciphertext=excluded.ciphertext", (engine, ciphertext))
        return data

    def _block_connection_jobs(self, db, engine, error_code, finish_in_flight=False):
        kind = "translate" if engine == "openai-translator" else "transcribe"
        live_kind = "live_translate" if kind == "translate" else "live_transcribe"
        for job in db.execute("select * from jobs where kind in (?,?) and state in ('queued','running')", (kind, live_kind)).fetchall():
            if finish_in_flight and job["state"] == "running" and job["submission_state"] == "accepted":
                continue
            state = "uncertain" if job["state"] == "running" and job["submission_state"] == "accepted" else "blocked"
            db.execute("update jobs set state=?,error_code=?,lease_token=null,lease_until=null,updated_at=? where id=?", (state, error_code, now(), job["id"]))
            row = db.execute("select data from records where table_name='conversations' and id=?", (job["conversation_id"],)).fetchone()
            if row:
                data = json.loads(row["data"])
                data.update(final_state=state, processing_error_code=error_code)
                data["english_state" if kind == "translate" else "transcription_state"] = state
                db.execute("update records set data=? where table_name='conversations' and id=?", (encoded(data), job["conversation_id"]))

    def change_connection_state(self, engine, state, finish_in_flight=False):
        with self.transaction() as db:
            row = db.execute("select data from records where table_name='connections' and id=?", (engine,)).fetchone()
            if not row:
                raise ValueError("connection_not_found")
            value = json.loads(row["data"])
            self._block_connection_jobs(db, engine, "connection_disconnected" if state == "disconnected" else "connection_disabled", finish_in_flight)
            value.update(state=state, generation=value["generation"] + 1, updated_at=now())
            if state == "disconnected":
                value.update(tested_at=None, safe_metadata={})
                db.execute("delete from secrets where engine=?", (engine,))
            db.execute("update records set data=? where table_name='connections' and id=?", (encoded(value), engine))
            return value

    def remove_secret(self, engine: str):
        with self.transaction() as db:
            db.execute("delete from secrets where engine=?", (engine,))

    def token(self, purpose: str, value: str, expires: int) -> str:
        content = f"{purpose}:{value}:{expires}"
        return f"{expires}.{hmac.new(self._signing_key, content.encode(), hashlib.sha256).hexdigest()}"

    def verify_token(self, token: str, purpose: str, value: str) -> bool:
        try:
            expiry = int(token.split(".", 1)[0])
            return expiry >= time.time() and hmac.compare_digest(token, self.token(purpose, value, expiry))
        except (ValueError, TypeError):
            return False

    def audio_path(self, conversation_id: str) -> Path:
        return self.root / "audio" / identifier(conversation_id) / "source.wav"

    def chunk_path(self, conversation_id: str, part_id: str, sequence: int) -> Path:
        if not 0 <= sequence <= 2880:
            raise ValueError("invalid_chunk_sequence")
        return self.root / "audio" / identifier(conversation_id) / identifier(part_id) / f"{sequence:04d}.wav"

    def authorize_chunk(self, conversation_id: str, data: dict, lease_token: str | None = None) -> dict:
        identity = (conversation_id, str(data["part_id"]), data["sequence"])
        with self.transaction() as db:
            if lease_token:
                self._capture_fenced(db, conversation_id, lease_token, data["part_id"])
            prior = db.execute("select * from chunks where conversation_id=? and part_id=? and sequence=?", identity).fetchone()
            if prior and json.loads(prior["data"]) != data:
                raise ValueError("chunk_conflict")
            if not prior:
                db.execute("insert into chunks(conversation_id,part_id,sequence,data) values(?,?,?,?)", (*identity, encoded(data)))
            return {**data, "uploaded": bool(prior and prior["uploaded"]), "confirmed": bool(prior and prior["confirmed"])}

    def chunks(self, conversation_id: str, confirmed_only: bool = True) -> list[dict]:
        with self.transaction() as db:
            rows = db.execute("select * from chunks where conversation_id=?" + (" and confirmed=1" if confirmed_only else "") + " order by sequence", (conversation_id,)).fetchall()
        return [{**json.loads(row["data"]), "uploaded": bool(row["uploaded"]), "confirmed": bool(row["confirmed"]), "path": str(self.chunk_path(conversation_id, row["part_id"], row["sequence"]))} for row in rows]

    def mark_chunk(self, conversation_id: str, part_id: str, sequence: int, *, confirmed: bool = False, lease_token: str | None = None):
        with self.transaction() as db:
            if lease_token:
                self._capture_fenced(db, conversation_id, lease_token, part_id)
            db.execute("update chunks set uploaded=1,confirmed=max(confirmed,?) where conversation_id=? and part_id=? and sequence=?", (int(confirmed), conversation_id, str(part_id), sequence))

    def _capture_fenced(self, db, conversation_id, token, part_id=None):
        found = db.execute("select data from records where table_name='conversations' and id=?", (conversation_id,)).fetchone()
        row = json.loads(found["data"]) if found else None
        if not row or row.get("lease_token") != str(token) or row.get("lease_until", 0) < time.time() or (part_id and row.get("part_id") != str(part_id)):
            raise ValueError("recording_lease_lost")
        if row.get("upload_state") == "saved":
            raise ValueError("recording_already_finalized")
        return row

    def acquire_lease(self, conversation_id, tab_id, purpose="capture"):
        with self.transaction() as db:
            found = db.execute("select data from records where table_name='conversations' and id=?", (conversation_id,)).fetchone()
            if not found:
                raise ValueError("conversation_not_found")
            row = json.loads(found["data"])
            if row.get("upload_state") == "saved":
                raise ValueError("recording_already_finalized")
            if row.get("lease_until", 0) >= time.time() and row.get("lease_tab") != tab_id:
                raise ValueError("recording_already_leased")
            row.update(lease_tab=tab_id, lease_token=str(uuid4()), lease_until=time.time() + 90, part_id=row.get("part_id") or str(uuid4()))
            db.execute("update records set data=? where table_name='conversations' and id=?", (encoded(row), conversation_id))
        return {"part_id": row["part_id"], "lease_token": row["lease_token"], "expires_in": 90}

    def finalize_conversation(self, conversation_id, manifest, lease_token):
        digest = hashlib.sha256(encoded(manifest).encode()).hexdigest()
        with self.transaction() as db:
            found = db.execute("select data from records where table_name='conversations' and id=?", (conversation_id,)).fetchone()
            if not found:
                raise ValueError("conversation_not_found")
            row = json.loads(found["data"])
            if row.get("upload_state") == "saved":
                if row.get("manifest_hash") != digest:
                    raise ValueError("manifest_conflict")
            else:
                self._capture_fenced(db, conversation_id, lease_token, manifest["part_id"])
                chunks = db.execute("select * from chunks where conversation_id=? and confirmed=1 order by sequence", (conversation_id,)).fetchall()
                position = 0
                valid = len(chunks) == manifest["expected_chunks"] and 0 < manifest["total_samples"] <= 115200000
                for sequence, chunk in enumerate(chunks):
                    data = json.loads(chunk["data"])
                    valid = valid and chunk["part_id"] == manifest["part_id"] and chunk["sequence"] == sequence and data["start_sample"] == position
                    position += data["sample_count"]
                if not valid or position != manifest["total_samples"]:
                    raise ValueError("manifest_audio_missing")
                row.update(capture_state="stopped", upload_state="saved", total_samples=position, duration_seconds=position / 16000, manifest_hash=digest, final_state="queued", transcription_state="queued", english_state="pending", speaker_state="pending")
                db.execute("update records set data=? where table_name='conversations' and id=?", (encoded(row), conversation_id))
            payload = {"run_id": row["run_id"], "total_samples": row["total_samples"]}
            dedupe = f"{conversation_id}:assemble:{row['run_id']}"
            db.execute("insert into jobs(id,conversation_id,kind,dedupe_key,state,payload,created_at,updated_at) values(?,?,'assemble',?,'queued',?,?,?) on conflict(dedupe_key) do nothing", (str(uuid4()), conversation_id, dedupe, encoded(payload), now(), now()))
            job = self._job(db.execute("select * from jobs where dedupe_key=?", (dedupe,)).fetchone())
        return {"saved": True, "total_samples": row["total_samples"], "job_id": job["id"]}

    @staticmethod
    def _job(row) -> dict:
        data = dict(row)
        data["payload"] = json.loads(data["payload"])
        data["result"] = json.loads(data["result"]) if data["result"] else None
        data["run_id"] = data["payload"].get("run_id")
        return data

    def enqueue_job(self, conversation_id: str, kind: str, payload: dict | None = None) -> dict:
        payload = payload or {}
        dedupe = f"{conversation_id}:{kind}:{payload.get('run_id', 'production')}"
        if payload.get("window_id") is not None:
            dedupe += f":{payload['window_id']}"
        job_id = str(uuid4())
        with self.transaction() as db:
            db.execute("insert into jobs(id,conversation_id,kind,dedupe_key,state,payload,created_at,updated_at) values(?,?,?,?,'queued',?,?,?) on conflict(dedupe_key) do nothing", (job_id, conversation_id, kind, dedupe, encoded(payload), now(), now()))
            row = db.execute("select * from jobs where dedupe_key=?", (dedupe,)).fetchone()
        return self._job(row)

    def plan_live_windows(self, conversation_id: str, window_seconds: int = 10, context_seconds: int = 2) -> list[dict]:
        """Persist independent upload/provider boundaries for customer Sessions."""
        if not 5 <= window_seconds <= 30 or not 0 <= context_seconds <= 10:
            raise ValueError("invalid_live_window")
        queued = []
        with self.transaction() as db:
            found = db.execute("select data from records where table_name='conversations' and id=?", (conversation_id,)).fetchone()
            conversation = json.loads(found["data"]) if found else None
            if not conversation or conversation.get("capture_state") == "deleted" or conversation.get("upload_state") == "saved":
                return []
            if not (conversation.get("live_session") or conversation.get("live_session_id")) or conversation.get("audio_only"):
                return []
            chunks = [json.loads(row["data"]) for row in db.execute("select data from chunks where conversation_id=? and confirmed=1 order by sequence", (conversation_id,))]
            coverage = 0
            for chunk in chunks:
                if int(chunk["start_sample"]) != coverage:
                    break
                coverage += int(chunk["sample_count"])
            conversation.update(total_samples=coverage, duration_seconds=coverage / 16000, updated_at=now())
            db.execute("update records set data=? where table_name='conversations' and id=?", (encoded(conversation), conversation_id))
            prior = [json.loads(row["payload"]) for row in db.execute("select payload from jobs where conversation_id=? and kind='live_transcribe'", (conversation_id,))]
            start = max((int(payload["ownership_end"]) for payload in prior), default=0)
            window_samples = window_seconds * 16000
            while coverage - start >= window_samples:
                end = start + window_samples
                window_id = f"{start}:{end}"
                payload = {"run_id": conversation["run_id"], "window_id": window_id,
                           "sample_start": max(0, start - context_seconds * 16000), "sample_end": end,
                           "ownership_start": start, "ownership_end": end}
                dedupe = f"{conversation_id}:live_transcribe:{conversation['run_id']}:{window_id}"
                db.execute("insert into jobs(id,conversation_id,kind,dedupe_key,state,payload,created_at,updated_at) values(?,?,'live_transcribe',?,'queued',?,?,?) on conflict(dedupe_key) do nothing",
                           (str(uuid4()), conversation_id, dedupe, encoded(payload), now(), now()))
                queued.append(self._job(db.execute("select * from jobs where dedupe_key=?", (dedupe,)).fetchone()))
                start = end
        return queued

    def publish_live_window(self, job: dict, turns: list[dict], speakers: list[dict], provenance: dict) -> bool:
        """Merge one fenced window atomically; stale translations cannot erase newer speech."""
        conversation_id, payload = job["conversation_id"], job["payload"]
        window_key = f"{conversation_id}:{payload['window_id']}"
        with self.transaction() as db:
            self._fenced(db, job["id"], job.get("lease_token"))
            found = db.execute("select data from records where table_name='conversations' and id=?", (conversation_id,)).fetchone()
            conversation = json.loads(found["data"]) if found else None
            if not conversation or conversation.get("capture_state") == "deleted":
                raise ValueError("conversation_deleted")
            row = db.execute("select data from records where table_name='transcripts' and id=?", (conversation_id,)).fetchone()
            previous = json.loads(row["data"]) if row else {}
            previous_version = previous.get("version") or {}
            if previous_version and previous_version.get("kind") != "progressive":
                # A complete whole-recording source version is authoritative,
                # including while its final English translation is pending.
                return False
            old = db.execute("select data from records where table_name='live_windows' and id=?", (window_key,)).fetchone()
            old_window = json.loads(old["data"]) if old else {}
            source_job = payload.get("source_job_id") or job["id"]
            if job["kind"] == "live_translate" and old_window.get("source_job_id") != source_job:
                return False
            window = {"conversation_id": conversation_id, "window_id": payload["window_id"],
                      "ownership_start": payload["ownership_start"], "ownership_end": payload["ownership_end"],
                      "source_job_id": source_job, "turns": turns, "speakers": speakers, "provenance": provenance}
            db.execute("insert into records values('live_windows',?,?) on conflict(table_name,id) do update set data=excluded.data", (window_key, encoded(window)))
            windows = [json.loads(value["data"]) for value in db.execute("select data from records where table_name='live_windows'")]
            windows = sorted((value for value in windows if value["conversation_id"] == conversation_id), key=lambda value: value["ownership_start"])
            all_turns = [dict(turn) for value in windows for turn in value["turns"]]
            all_turns.sort(key=lambda turn: (turn["start_seconds"], turn["end_seconds"], turn["id"]))
            for ordinal, turn in enumerate(all_turns): turn["ordinal"] = ordinal
            all_speakers = {speaker["speaker_ref"]: speaker for value in windows for speaker in value["speakers"]}
            coverage = 0
            for value in windows:
                if value["ownership_start"] != coverage: break
                coverage = value["ownership_end"]
            english_state = "available" if all(turn.get("english_text") is not None for turn in all_turns) else "pending"
            version = {"id": str(uuid4()), "run_id": payload.get("run_id"), "kind": "progressive",
                       "coverage_samples": coverage, "english_state": english_state,
                       "speaker_state": "provisional", "provenance": {**provenance, "progressive": True}}
            value = {"version": version, "turns": all_turns, "speakers": list(all_speakers.values()), "revisions": previous.get("revisions", [])}
            db.execute("insert into records values('transcripts',?,?) on conflict(table_name,id) do update set data=excluded.data", (conversation_id, encoded(value)))
            # Local historic progressive selection is unsupported. Keep only
            # the current cumulative view, avoiding quadratic transcript copies.
            unresolved = db.execute("select state,error_code from jobs where conversation_id=? and id<>? and kind in ('live_transcribe','live_translate') and state in ('failed','blocked','uncertain') order by created_at desc limit 1", (conversation_id, job["id"])).fetchone()
            conversation.update(active_version_id=version["id"], transcription_state="available", english_state=english_state,
                                speaker_state="provisional", final_state=unresolved["state"] if unresolved else "queued" if conversation.get("upload_state") == "saved" else "pending",
                                processing_error_code=unresolved["error_code"] if unresolved else None, updated_at=now())
            db.execute("update records set data=? where table_name='conversations' and id=?", (encoded(conversation), conversation_id))
        return True

    def claim_job(self, worker_id: str, lease_seconds: int = 900) -> dict | None:
        timestamp = time.time()
        with self.transaction() as db:
            # Lost paid submissions are fenced as uncertain; they are never replayed automatically.
            expired = db.execute("select * from jobs where state='running' and lease_until<? and submission_state='accepted'", (timestamp,)).fetchall()
            for job in expired:
                saved = db.execute("select data from records where table_name='conversations' and id=?", (job["conversation_id"],)).fetchone()
                if saved:
                    value = json.loads(saved["data"])
                    value.update(final_state="uncertain", processing_error_code="external_execution_uncertain")
                    value["english_state" if job["kind"] in {"translate", "live_translate"} else "transcription_state"] = "uncertain"
                    db.execute("update records set data=? where table_name='conversations' and id=?", (encoded(value), job["conversation_id"]))
            db.execute("update jobs set state=case when submission_state='accepted' then 'uncertain' else 'queued' end,error_code=case when submission_state='accepted' then 'external_execution_uncertain' else error_code end,lease_token=null,lease_until=null,updated_at=? where state='running' and lease_until<?", (now(), timestamp))
            row = db.execute("select * from jobs where state='queued' order by case when kind='live_translate' then 0 else 1 end,created_at,id limit 1").fetchone()
            if not row:
                return None
            token = str(uuid4())
            db.execute("update jobs set state='running',worker_id=?,lease_token=?,lease_until=?,updated_at=? where id=? and state='queued'", (worker_id, token, timestamp + lease_seconds, now(), row["id"]))
            return self._job(db.execute("select * from jobs where id=?", (row["id"],)).fetchone())

    def _fenced(self, db, job_id: str, lease_token: str | None):
        row = db.execute("select * from jobs where id=?", (job_id,)).fetchone()
        if not row or row["state"] != "running" or (lease_token is not None and row["lease_token"] != lease_token) or (row["lease_until"] or 0) < time.time():
            raise ValueError("local_job_lease_lost")
        return row

    def mark_job_accepted(self, job_id: str, external_id: str | None = None, lease_token: str | None = None):
        with self.transaction() as db:
            self._fenced(db, job_id, lease_token)
            db.execute("update jobs set submission_state='accepted',updated_at=? where id=?", (now(), job_id))

    def renew_job(self, job_id: str, lease_token: str, lease_seconds: int = 900):
        with self.transaction() as db:
            self._fenced(db, job_id, lease_token)
            db.execute("update jobs set lease_until=?,updated_at=? where id=?", (time.time() + lease_seconds, now(), job_id))

    def complete_job(self, job_id: str, result: dict | None = None, lease_token: str | None = None):
        with self.transaction() as db:
            self._fenced(db, job_id, lease_token)
            db.execute("update jobs set state='succeeded',result=?,error_code=null,submission_state='received',lease_token=null,lease_until=null,updated_at=? where id=?", (encoded(result or {}), now(), job_id))

    def fail_job(self, job_id: str, state: str, error_code: str, error_message: str = "", lease_token: str | None = None):
        if state not in {"failed", "blocked", "uncertain", "cancelled"}:
            raise ValueError("invalid_job_failure_state")
        with self.transaction() as db:
            self._fenced(db, job_id, lease_token)
            db.execute("update jobs set state=?,error_code=?,lease_token=null,lease_until=null,updated_at=? where id=?", (state, error_code, now(), job_id))

    def retry_jobs(self, conversation_id: str, kinds: list[str]) -> tuple[list[str], list[str]]:
        resumed, uncertain = [], []
        kinds = list(kinds)
        if "transcribe" in kinds: kinds.append("live_transcribe")
        if "translate" in kinds: kinds.append("live_translate")
        with self.transaction() as db:
            rows = db.execute("select * from jobs where conversation_id=?", (conversation_id,)).fetchall()
            for row in rows:
                if row["kind"] not in kinds:
                    continue
                if row["state"] == "uncertain":
                    uncertain.append(row["id"])
                elif row["state"] in {"failed", "blocked"}:
                    db.execute("update jobs set state='queued',error_code=null,submission_state='not_submitted',lease_token=null,lease_until=null,updated_at=? where id=?", (now(), row["id"]))
                    resumed.append(row["id"])
        return resumed, uncertain

    def assert_job(self, job_id: str, lease_token: str):
        with self.transaction() as db:
            return self._job(self._fenced(db, job_id, lease_token))

    def replace_turns(self, conversation_id: str, turns: list[dict], version: dict, speakers: list[dict] | None = None, *, job_id: str | None = None, lease_token: str | None = None):
        with self.transaction() as db:
            if job_id:
                self._fenced(db, job_id, lease_token)
            previous = db.execute("select data from records where table_name='transcripts' and id=?", (conversation_id,)).fetchone()
            old = json.loads(previous["data"]) if previous else {}
            same = old.get("version", {}).get("id") == version.get("id")
            value = {"version": version, "turns": turns, "speakers": (old.get("speakers", []) if same and old.get("speakers") else speakers or []), "revisions": old.get("revisions", []) if same else []}
            if old.get("version"):
                db.execute("insert into records values('versions',?,?) on conflict(table_name,id) do update set data=excluded.data", (old["version"]["id"], encoded(old)))
            db.execute("insert into records values('transcripts',?,?) on conflict(table_name,id) do update set data=excluded.data", (conversation_id, encoded(value)))
            db.execute("insert into records values('versions',?,?) on conflict(table_name,id) do update set data=excluded.data", (version["id"], encoded(value)))

    def get_turns(self, conversation_id: str) -> list[dict]:
        return (self.get("transcripts", conversation_id) or {}).get("turns", [])

    def get_transcript(self, conversation_id: str) -> dict:
        return self.get("transcripts", conversation_id) or {"version": None, "turns": [], "speakers": [], "revisions": []}

    def delete_conversation(self, conversation_id: str) -> dict:
        conversation_id = identifier(conversation_id)
        with self.transaction() as db:
            found = db.execute("select data from records where table_name='conversations' and id=?", (conversation_id,)).fetchone()
            if not found:
                raise ValueError("conversation_not_found")
            row = json.loads(found["data"])
            row.update(capture_state="deleted", deleted_at=row.get("deleted_at") or now(), updated_at=now(),
                customer_name_snapshot="Deleted recording", salesperson_name_snapshot="", title="", location="",
                active_version_id=None, lease_token=None, lease_until=0, playback_object_path=None, playback_path=None)
            db.execute("update records set data=? where table_name='conversations' and id=?", (encoded(row), conversation_id))
            db.execute("update jobs set state='cancelled',lease_token=null,lease_until=null,result=null,updated_at=? where conversation_id=? and kind<>'delete'", (now(), conversation_id))
            dedupe = f"{conversation_id}:delete"
            db.execute("insert into jobs(id,conversation_id,kind,dedupe_key,state,payload,created_at,updated_at) values(?,?,'delete',?,'queued','{}',?,?) on conflict(dedupe_key) do update set state=case when jobs.state in ('failed','blocked','cancelled') then 'queued' else jobs.state end,updated_at=excluded.updated_at", (str(uuid4()), conversation_id, dedupe, now(), now()))
        return {"deleted": True, "cleanup": "queued"}

    def retry_cleanup_jobs(self):
        with self.transaction() as db:
            db.execute("update jobs set state='queued',lease_token=null,lease_until=null,updated_at=? where kind='delete' and state='failed'", (now(),))

    def finish_deleted_conversation(self, conversation_id, job_id, lease_token):
        with self.transaction() as db:
            self._fenced(db, job_id, lease_token)
            run_ids = {row["id"] for row in db.execute("select id,data from records where table_name='runs'") if json.loads(row["data"]).get("conversation_id") == conversation_id}
            current = db.execute("select data from records where table_name='transcripts' and id=?", (conversation_id,)).fetchone()
            current_id = (json.loads(current["data"]).get("version") or {}).get("id") if current else None
            for version in db.execute("select id,data from records where table_name='versions'").fetchall():
                data = json.loads(version["data"])
                if version["id"] == current_id or (data.get("version") or {}).get("run_id") in run_ids:
                    db.execute("delete from records where table_name='versions' and id=?", (version["id"],))
            db.execute("delete from records where table_name='transcripts' and id=?", (conversation_id,))
            db.execute("delete from records where table_name='live_windows' and id like ?", (conversation_id + ":%",))
            db.execute("delete from chunks where conversation_id=?", (conversation_id,))
            db.execute("update jobs set state='succeeded',result='{}',submission_state='received',lease_token=null,lease_until=null,updated_at=? where id=?", (now(), job_id))

    def create_enquiry(self, data: dict, idem: str) -> tuple[dict, bool]:
        digest = hashlib.sha256(encoded(data).encode()).hexdigest()
        with self.transaction() as db:
            previous = db.execute("select data,request_hash from enquiries where idempotency_key=?", (idem,)).fetchone()
            if previous:
                if previous["request_hash"] != digest:
                    raise ValueError("idempotency_conflict")
                return json.loads(previous["data"]), False
            if data.get("conversationId"):
                row = db.execute("select data from records where table_name='conversations' and id=?", (data["conversationId"],)).fetchone()
                conversation = json.loads(row["data"]) if row else None
                if not conversation:
                    raise ValueError("conversation_not_found")
                if conversation.get("upload_state") != "saved" or conversation.get("capture_state") not in {"stopped", "interrupted"} or conversation.get("total_samples", 0) <= 0:
                    raise ValueError("recording_not_saved")
                if db.execute("select 1 from enquiries where conversation_id=?", (data["conversationId"],)).fetchone():
                    raise ValueError("recording_already_linked")
            value = {**data, "id": str(uuid4()), "createdAt": now()}
            cursor = db.execute("insert into enquiries(id,idempotency_key,request_hash,conversation_id,data) values(?,?,?,?,?)", (value["id"], idem, digest, data.get("conversationId"), encoded(value)))
            value["enquiryNumber"] = f"ENQ-{datetime.now(timezone.utc).year}-{cursor.lastrowid:06d}"
            db.execute("update enquiries set data=? where id=?", (encoded(value), value["id"]))
        return value, True

    def enquiries(self) -> list[dict]:
        with self.transaction() as db:
            return [json.loads(row["data"]) for row in db.execute("select data from enquiries order by number desc")]

    def update_enquiry(self, enquiry_id: str, data: dict) -> dict:
        with self.transaction() as db:
            row = db.execute("select data from enquiries where id=?", (enquiry_id,)).fetchone()
            if not row:
                raise ValueError("enquiry_not_found")
            value = {**json.loads(row["data"]), **data, "id": enquiry_id}
            db.execute("update enquiries set data=? where id=?", (encoded(value), enquiry_id))
            return value
