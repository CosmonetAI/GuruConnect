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
        for job in db.execute("select * from jobs where kind=? and state in ('queued','running')", (kind,)).fetchall():
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
        job_id = str(uuid4())
        with self.transaction() as db:
            db.execute("insert into jobs(id,conversation_id,kind,dedupe_key,state,payload,created_at,updated_at) values(?,?,?,?,'queued',?,?,?) on conflict(dedupe_key) do nothing", (job_id, conversation_id, kind, dedupe, encoded(payload), now(), now()))
            row = db.execute("select * from jobs where dedupe_key=?", (dedupe,)).fetchone()
        return self._job(row)

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
                    value["english_state" if job["kind"] == "translate" else "transcription_state"] = "uncertain"
                    db.execute("update records set data=? where table_name='conversations' and id=?", (encoded(value), job["conversation_id"]))
            db.execute("update jobs set state=case when submission_state='accepted' then 'uncertain' else 'queued' end,error_code=case when submission_state='accepted' then 'external_execution_uncertain' else error_code end,lease_token=null,lease_until=null,updated_at=? where state='running' and lease_until<?", (now(), timestamp))
            row = db.execute("select * from jobs where state='queued' order by created_at,id limit 1").fetchone()
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
