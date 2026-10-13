"""Native durable main worker. No HTTP listener and no optional model dependencies."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import tempfile
import time
import wave
from uuid import uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from .audio import assemble_wav, validate_wav
from .config import get_settings
from .db import Database, many, one
from .processing import publish_result, schedule
from .openai_translator import OPENAI_TRANSLATION_MODEL, OpenAITranslator
from .providers import AzureTranslator, ElevenLabsAdapter, ProviderError, protect_terms
from .security import SecretBox
from .storage import Storage

log = logging.getLogger("recorder.worker")
TRANSLATION_BLOCKED_CODES = {"invalid_credentials", "connection_unavailable", "translator_unconfigured", "translator_unavailable", "provider_model_access_denied", "provider_access_denied", "provider_permission_denied", "provider_payment_required", "provider_quota_exceeded"}


class MainWorker:
    def __init__(self, settings):
        self.settings = settings; self.db = Database(settings); self.storage = Storage(settings)
        self.id = os.environ.get("MAIN_WORKER_ID") or f"main-{uuid4()}"
        self.stopping = asyncio.Event()

    async def credentials(self, connection_id) -> dict:
        async with self.db.connection() as connection:
            row = await one(connection, "select s.encrypted_value,e.engine_id,e.config from engine_connections e left join provider_secrets s on s.connection_id=e.id where e.id=%s", (connection_id,))
        if not row: raise ProviderError("connection_unavailable")
        if row["encrypted_value"]: return SecretBox(self.settings).decrypt(row["encrypted_value"])
        # Shared platform-managed secrets remain in service environment, never browser config.
        if row["config"].get("platform_managed"):
            if row["engine_id"] == "elevenlabs" and self.settings.elevenlabs_api_key:
                return {"api_key": self.settings.elevenlabs_api_key.get_secret_value()}
            if row["engine_id"] == "azure-translator" and self.settings.azure_translator_key:
                return {"api_key": self.settings.azure_translator_key.get_secret_value(), "region": self.settings.azure_translator_region}
        raise ProviderError("invalid_credentials")

    async def audio(self, job: dict) -> tuple[bytes, dict]:
        async with self.db.connection() as connection:
            conv = await one(connection, "select * from conversations where id=%s and deleted_at is null", (job["conversation_id"],))
            chunks = await many(connection, "select * from audio_chunks where conversation_id=%s and state='validated' order by sample_start", (job["conversation_id"],))
        if not conv: raise ProviderError("permission_denied")
        start, end = int(job["payload"].get("sample_start", 0)), int(job["payload"]["sample_end"])
        selected = [chunk for chunk in chunks if chunk["sample_start"] < end and chunk["sample_start"] + chunk["sample_count"] > start]
        if not selected: raise ProviderError("invalid_audio")
        actual_hash = hashlib.sha256("".join(chunk["sha256"] for chunk in selected).encode()).hexdigest()
        if job["payload"].get("input_hash") and actual_hash != job["payload"]["input_hash"]:
            raise ProviderError("input_hash_mismatch")
        expected = selected[0]["sample_start"]
        buffers = []
        for chunk in selected:
            if chunk["sample_start"] != expected: raise ProviderError("invalid_audio")
            data = await self.storage.read(chunk["object_path"], chunk["bytes"])
            info = validate_wav(data, chunk["sample_count"])
            if info.sha256 != chunk["sha256"] or len(data) != chunk["bytes"]: raise ProviderError("input_hash_mismatch")
            buffers.append(data); expected += chunk["sample_count"]
        return assemble_wav(buffers, start-selected[0]["sample_start"], end-selected[0]["sample_start"]), conv

    async def audio_file(self, job):
        """Assemble a two-hour session with bounded RAM; scratch is reconstructible."""
        async with self.db.connection() as connection:
            conv = await one(connection, "select * from conversations where id=%s and deleted_at is null", (job["conversation_id"],))
            chunks = await many(connection, "select * from audio_chunks where conversation_id=%s and state='validated' order by sample_start", (job["conversation_id"],))
        if not conv: raise ProviderError("permission_denied")
        start, end = int(job["payload"].get("sample_start",0)), int(job["payload"]["sample_end"])
        selected=[chunk for chunk in chunks if chunk["sample_start"] < end and chunk["sample_start"]+chunk["sample_count"] > start]
        if not selected or selected[0]["sample_start"]>start or selected[-1]["sample_start"]+selected[-1]["sample_count"]<end: raise ProviderError("invalid_audio")
        input_hash=hashlib.sha256("".join(chunk["sha256"] for chunk in selected).encode()).hexdigest()
        if job["payload"].get("input_hash") and input_hash!=job["payload"]["input_hash"]: raise ProviderError("input_hash_mismatch")
        output=tempfile.TemporaryFile(mode="w+b");expected=selected[0]["sample_start"]
        try:
            with wave.open(output,"wb") as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
                for chunk in selected:
                    if chunk["sample_start"]!=expected: raise ProviderError("invalid_audio")
                    data=await self.storage.read(chunk["object_path"],chunk["bytes"])
                    info=validate_wav(data,chunk["sample_count"])
                    if info.sha256!=chunk["sha256"] or len(data)!=chunk["bytes"]:raise ProviderError("input_hash_mismatch")
                    left=max(0,start-chunk["sample_start"]);right=min(chunk["sample_count"],end-chunk["sample_start"])
                    wav.writeframesraw(info.pcm[left*2:right*2]);expected+=chunk["sample_count"]
            output.seek(0);return output,conv
        except BaseException:output.close();raise

    async def renew(self, job: dict):
        while True:
            await asyncio.sleep(max(5, self.settings.worker_lease_seconds//3))
            async with self.db.connection() as connection:
                row = await one(connection, "select renew_job(%s,%s,%s,%s,%s) as ok", (job["id"],self.id,job["lease_token"],job["fence"],self.settings.worker_lease_seconds))
                await connection.execute("insert into worker_heartbeats(worker_id,worker_type) values(%s,'main') on conflict(worker_id) do update set last_seen_at=now()", (self.id,))
            if not row["ok"]: raise ProviderError("permission_denied")

    async def begin_submission(self, job):
        async with self.db.connection() as connection:
            row = await one(connection, "select begin_job_submission(%s,%s,%s,%s,%s) as ok", (job["id"],self.id,job["lease_token"],job["fence"],str(job["id"])))
            if not row["ok"]: raise ProviderError("connection_unavailable")

    async def usage(self, job, kind, audio_seconds=0, characters=0, *, input_tokens=0, output_tokens=0, provider_model=None):
        if not job.get("run_id"): return
        async with self.db.connection() as connection:
            await connection.execute("insert into usage_records(conversation_id,run_id,job_id,audio_seconds,translation_characters,attempt,processing_kind,input_tokens,output_tokens,provider_model) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict(job_id,attempt,processing_kind) do nothing", (job["conversation_id"],job["run_id"],job["id"],audio_seconds,characters,job["attempt"],kind,input_tokens,output_tokens,provider_model))

    async def transcribe(self, job):
        audio, conv = await self.audio_file(job)
        credentials = await self.credentials(job["connection_id"])
        await self.begin_submission(job)
        await self.usage(job, "final-pass" if job["kind"] == "finalise" else "progressive-context", (job["payload"]["sample_end"]-job["payload"].get("sample_start",0))/16000)
        try: result = await ElevenLabsAdapter(credentials.get("api_key") or credentials.get("key")).process(audio, conv["language_hint"], str(job["id"]))
        finally: audio.close()
        raw = result.pop("raw")
        artifact = json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode()
        path = f"{conv['organisation_id']}/{conv['owner_id']}/{conv['id']}/results/{job['run_id']}/{job['id']}/{job['fence']}.json"
        await self.storage.put(path, artifact)
        reliably_english = result["language"] == "en" and (result.get("language_probability") or 0) >= 0.95 and all(not any('\u0900' <= character <= '\u0dff' for character in segment['text']) for segment in result["source_segments"])
        if reliably_english or not result["source_segments"]:
            result["english_segments"] = [dict(segment) for segment in result["source_segments"]]
            result["english_state"] = "complete" if job["kind"] == "finalise" else "available"
        else: result["english_state"] = "pending"
        async with self.db.connection() as connection:
            finished = await one(connection, "select finish_job(%s,%s,%s,%s,%s) as ok", (job["id"],self.id,job["lease_token"],job["fence"],Jsonb(result)))
            if not finished["ok"]: return
            await connection.execute("insert into inference_artifacts(conversation_id,run_id,job_id,kind,object_path,sha256,bytes,provenance) values(%s,%s,%s,'provider-response',%s,%s,%s,%s) on conflict(object_path) do nothing", (conv["id"],job["run_id"],job["id"],path,hashlib.sha256(artifact).hexdigest(),len(artifact),Jsonb(result["provenance"])))
            version_id = await publish_result(connection, job, result)
            if not reliably_english and result["source_segments"]:
                await schedule(connection, conv["id"],job["run_id"],"translate",f"translate:{job['id']}",{**job["payload"],"source_job_id":str(job["id"]),"source_version_id":str(version_id),"final":job["kind"]=="finalise"},job.get("window_id"),job["priority"])

    async def translate(self, job):
        async with self.db.connection() as connection:
            provider = await one(connection, "select engine_id,config,generation from engine_connections where id=%s and organisation_id=%s", (job["connection_id"],job["organisation_id"]))
            source_job = await one(connection, "select p.result from processing_jobs p join transcript_versions v on v.job_id=p.id and v.run_id=p.run_id and v.conversation_id=p.conversation_id where p.id=%s and p.conversation_id=%s and p.run_id=%s and p.state='succeeded' and v.id=%s", (job["payload"]["source_job_id"],job["conversation_id"],job["run_id"],job["payload"]["source_version_id"]))
            turns = await many(connection, "select t.* from transcript_turns t join transcript_versions v on v.id=t.version_id where v.id=%s and v.conversation_id=%s and v.run_id=%s and (%s::uuid is null or t.window_id=%s::uuid) order by t.ordinal", (job["payload"]["source_version_id"],job["conversation_id"],job["run_id"],job.get("window_id"),job.get("window_id")))
            words = await many(connection, "select w.turn_id,w.word,w.start_seconds,w.end_seconds from transcript_words w join transcript_turns t on t.id=w.turn_id join transcript_versions v on v.id=t.version_id where v.id=%s and v.conversation_id=%s and v.run_id=%s and (%s::uuid is null or t.window_id=%s::uuid) order by w.start_seconds,w.end_seconds,w.id", (job["payload"]["source_version_id"],job["conversation_id"],job["run_id"],job.get("window_id"),job.get("window_id")))
        if not source_job: raise ProviderError("invalid_result")
        if not provider or provider["generation"] != job["connection_generation"]:
            raise ProviderError("connection_unavailable")
        engine = provider["engine_id"]
        if engine not in {"openai-translator", "azure-translator"}:
            raise ProviderError("invalid_result")
        if job["payload"].get("translation_provider", engine) != engine:
            raise ProviderError("connection_unavailable")
        is_openai = engine == "openai-translator"
        model = OPENAI_TRANSLATION_MODEL if is_openai else "standard-v3"
        if is_openai and (provider["config"].get("model") != model or job["payload"].get("translator_model", model) != model):
            raise ProviderError("provider_model_access_denied")
        strategy = "openai-gpt-5.6-luna/translate-v1" if is_openai else "standard-v3/protect-v1"
        credentials = await self.credentials(job["connection_id"])
        await self.begin_submission(job)
        result = dict(source_job["result"]); source, english = [], []
        offset = job["payload"].get("sample_start",0)/16000
        source_words = {}
        for word in words:
            source_words.setdefault(word["turn_id"], []).append({"text":word["word"],"start":word["start_seconds"]-offset,"end":word["end_seconds"]-offset})
        paid = False
        try:
            for turn in turns:
                if turn["source_text"] is None: continue
                source.append({"start":turn["start_seconds"]-offset,"end":turn["end_seconds"]-offset,"text":turn["source_text"],"speaker":turn["speaker_ref"],"speaker_confidence":turn["speaker_confidence"],"words":source_words.get(turn["id"],[])})
                # Include provider, fixed model, prompt revision and credential generation.
                cache_key = hashlib.sha256(json.dumps([turn["source_text"], "auto", str(job["connection_id"]),job["connection_generation"],strategy],ensure_ascii=False).encode()).hexdigest()
                async with self.db.connection() as connection:
                    cached = await one(connection, "select english_text from translations where run_id=%s and cache_key=%s and state='complete'", (job["run_id"],cache_key))
                if cached: text = cached["english_text"]
                else:
                    await self.usage(job, f"turn-{turn['ordinal']}", characters=len(turn["source_text"]) if is_openai else len(protect_terms(turn["source_text"])[0]))
                    if is_openai:
                        chunk_index = 0
                        async def account_call(usage):
                            nonlocal chunk_index, paid
                            paid = True
                            chunk_index += 1
                            await self.usage(job, f"turn-{turn['ordinal']}-chunk-{chunk_index}", input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, provider_model=usage.model)
                        translated = await OpenAITranslator(credentials.get("api_key"), usage_callback=account_call).translate(turn["source_text"])
                        text = translated.text
                    else:
                        text = await AzureTranslator(credentials.get("api_key") or credentials.get("key"), credentials.get("region", self.settings.azure_translator_region)).translate(turn["source_text"])
                        paid = True
                    async with self.db.connection() as connection:
                        await connection.execute("insert into translations(conversation_id,run_id,source_turn_id,cache_key,source_language,translator_config,english_text,state,character_count) values(%s,%s,%s,%s,%s,%s,%s,'complete',%s) on conflict(run_id,cache_key) do nothing", (job["conversation_id"],job["run_id"],turn["id"],cache_key,turn["source_language"],Jsonb({"connection":str(job["connection_id"]),"generation":job["connection_generation"],"provider":engine,"model":model,"strategy":strategy}),text,len(turn["source_text"])))
                english.append({**source[-1],"text":text,"words":None})
        except ProviderError as exc:
            # A later turn can fail after earlier chunks have already incurred charges.
            if paid and not exc.uncertain:
                raise ProviderError(exc.code, uncertain=True) from None
            raise
        result.update(source_segments=source,english_segments=english,english_state="complete" if job["payload"].get("final") else "available")
        result["provenance"] = {**result.get("provenance",{}),"translation":"openai-gpt-5.6-luna" if is_openai else "azure-standard-v3","translation_model":model,"translation_connection_id":str(job['connection_id']),"translation_generation":job['connection_generation'],"translated_word_alignment":None}
        async with self.db.connection() as connection:
            finished = await one(connection,"select finish_job(%s,%s,%s,%s,%s) as ok",(job["id"],self.id,job["lease_token"],job["fence"],Jsonb(result)))
            if finished["ok"]: await publish_result(connection,job,result)

    async def assemble(self, job):
        audio, conv = await self.audio_file(job)
        target_job = job["payload"].get("inference_job_id")
        path = f"{conv['organisation_id']}/{conv['owner_id']}/{conv['id']}/artifacts/{target_job}/input.wav" if target_job else f"{conv['organisation_id']}/{conv['owner_id']}/{conv['id']}/playback/{conv['manifest_hash']}.wav"
        digest=hashlib.sha256();count=0
        while data:=audio.read(1024*1024):digest.update(data);count+=len(data)
        audio.seek(0)
        try:await self.storage.put_file(path,audio,"audio/wav")
        finally:audio.close()
        async with self.db.connection() as connection:
            finished = await one(connection,"select finish_job(%s,%s,%s,%s,%s) as ok",(job["id"],self.id,job["lease_token"],job["fence"],Jsonb({"object_path":path,"sha256":digest.hexdigest()})))
            if finished["ok"]:
                if target_job:
                    await connection.execute("insert into inference_artifacts(conversation_id,run_id,job_id,kind,object_path,sha256,bytes) values(%s,%s,%s,'input',%s,%s,%s) on conflict(object_path) do nothing",(conv["id"],job["run_id"],target_job,path,digest.hexdigest(),count))
                else:await connection.execute("update conversations set playback_object_path=%s,updated_at=now() where id=%s and deleted_at is null",(path,conv["id"]))

    async def execute(self, job):
        renewal = asyncio.create_task(self.renew(job))
        task = None
        try:
            operation = self.transcribe(job) if job["kind"] in {"transcribe","finalise"} else self.translate(job) if job["kind"] == "translate" else self.assemble(job) if job["kind"] == "assemble" else self.delete(job) if job["kind"] == "delete" else None
            if operation is None: raise ProviderError("invalid_result")
            task = asyncio.create_task(operation)
            done, _ = await asyncio.wait({task,renewal},return_when=asyncio.FIRST_COMPLETED)
            if renewal in done:
                task.cancel(); await asyncio.gather(task,return_exceptions=True); renewal.result()
            else: task.result()
        except Exception as exc:
            if isinstance(exc,ProviderError): code,uncertain = exc.code,exc.uncertain
            elif isinstance(exc,ValueError): code,uncertain = "invalid_audio",False
            elif isinstance(exc,HTTPException): code,uncertain = exc.detail.get("code","worker_dependency_failed"),False
            else: code,uncertain = "worker_execution_failed",False
            log.warning("job_failed job_id=%s code=%s",job["id"],code)
            async with self.db.connection() as connection:
                # Clear submission uncertainty only on a known pre-execution rejection.
                if isinstance(exc,ProviderError) and not uncertain and code in TRANSLATION_BLOCKED_CODES | {"provider_rate_limited","provider_request_rejected","provider_connection_failed","translation_term_preservation_failed","translator_invalid_response","translator_input_too_large","translator_invalid_input"}:
                    await connection.execute("update processing_jobs set submission_state='not_submitted' where id=%s and lease_token=%s and fence=%s",(job["id"],job["lease_token"],job["fence"]))
                retried = await one(connection,"select retry_job(%s,%s,%s,%s,%s,%s) as accepted",(job["id"],self.id,job["lease_token"],job["fence"],code,uncertain))
                if retried and retried["accepted"] and job.get("run_id"):
                    stage = "english_state" if job["kind"]=="translate" else "final_state" if job["kind"]=="finalise" else "transcription_state"
                    await connection.execute(f"update conversations set {stage}=%s,updated_at=now() where id=%s and deleted_at is null",("blocked" if code in TRANSLATION_BLOCKED_CODES else "failed",job["conversation_id"]))
        finally:
            if task and not task.done(): task.cancel()
            renewal.cancel()
            await asyncio.gather(*([task,renewal] if task else [renewal]),return_exceptions=True)

    async def delete(self, job):
        await self.cleanup_deleted(job["conversation_id"])
        async with self.db.connection() as connection:
            await connection.execute("select finish_job(%s,%s,%s,%s,%s)",(job["id"],self.id,job["lease_token"],job["fence"],Jsonb({"deleted":True})))

    async def cleanup_deleted(self, conversation_id=None):
        async with self.db.connection() as connection:
            expired=await many(connection,"update conversations c set deleted_at=now(),capture_state='deleted',upload_state='pending',active_version_id=null,updated_at=now() from organisations o where c.organisation_id=o.id and c.deleted_at is null and c.created_at<now()-make_interval(days=>o.retention_days) returning c.id")
            for expired_conv in expired:
                await connection.execute("delete from recording_leases where conversation_id=%s",(expired_conv["id"],))
                await connection.execute("update processing_jobs set state='cancelled',cancelled_at=now(),finish_in_flight=false where conversation_id=%s and state<>'succeeded'",(expired_conv["id"],))
                await connection.execute("update processing_runs set state='cancelled',cancelled_at=now() where conversation_id=%s",(expired_conv["id"],))
            rows = await many(connection,"select id,organisation_id,owner_id,playback_object_path from conversations where deleted_at is not null and (upload_state<>'saved' or deleted_at>now()-interval '1 day') and (%s::uuid is null or id=%s) order by updated_at limit 10",(conversation_id,conversation_id))
        for conv in rows:
            async with self.db.connection() as connection:
                paths = await many(connection,"select object_path from audio_chunks where conversation_id=%s union select object_path from inference_artifacts where conversation_id=%s",(conv["id"],conv["id"]))
            objects = [row["object_path"] for row in paths]
            if conv["playback_object_path"]: objects.append(conv["playback_object_path"])
            prefix=f"{conv['organisation_id']}/{conv['owner_id']}/{conv['id']}"
            objects.extend(await self.storage.list_paths(prefix))
            await self.storage.delete(objects)
            async with self.db.connection() as connection:
                # Mark cleanup complete; retained tombstone fences all late work.
                await connection.execute("delete from transcript_words where version_id in(select id from transcript_versions where conversation_id=%s)",(conv["id"],))
                for table in ("transcript_revisions","speaker_assignments","diarization_intervals","translations"):
                    await connection.execute(f"delete from {table} where conversation_id=%s",(conv["id"],))
                await connection.execute("delete from transcript_turns where conversation_id=%s",(conv["id"],))
                await connection.execute("delete from transcript_versions where conversation_id=%s",(conv["id"],))
                await connection.execute("update processing_jobs set result=case when result is null then null else '{\"deleted\":true}'::jsonb end where conversation_id=%s",(conv["id"],))
                await connection.execute("update conversations set upload_state='saved',customer_name_snapshot='Deleted recording',salesperson_name_snapshot='',title='',location='',updated_at=now() where id=%s and deleted_at is not null",(conv["id"],))

    async def run(self):
        if not self.settings.configured: raise RuntimeError("Set backend Supabase/database configuration before starting the main worker.")
        await self.db.open(); last_cleanup=0.0
        try:
            while not self.stopping.is_set():
                try:
                    async with self.db.connection() as connection:
                        await connection.execute("insert into worker_heartbeats(worker_id,worker_type) values(%s,'main') on conflict(worker_id) do update set last_seen_at=now()",(self.id,))
                        jobs=await many(connection,"select * from claim_jobs(%s,'main',1,%s)",(self.id,self.settings.worker_lease_seconds))
                    if jobs: await self.execute(jobs[0])
                    else:
                        try: await asyncio.wait_for(self.stopping.wait(),timeout=1)
                        except TimeoutError: pass
                    if time.monotonic()-last_cleanup>60: await self.cleanup_deleted();last_cleanup=time.monotonic()
                except Exception: log.warning("worker_cycle_failed code=dependency_unavailable");await asyncio.sleep(3)
        finally: await self.db.close()


async def main():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    if settings.environment in {"development", "local"} and not settings.configured:
        from .local_worker import LocalWorker
        worker = LocalWorker(settings)
        print("Local processing worker started. Saved audio assembly runs without provider keys.", flush=True)
    else:
        worker = MainWorker(settings)
    for signum in (signal.SIGINT,signal.SIGTERM): asyncio.get_running_loop().add_signal_handler(signum,worker.stopping.set)
    await worker.run()


if __name__ == "__main__": asyncio.run(main())
