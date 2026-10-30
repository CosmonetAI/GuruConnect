"""Private localhost processing worker; real providers and durable paid-call fences.

Audio assembly does not need provider keys. Source speech survives translation
failures, and accepted provider requests are never retried by the worker loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import math
import os
import tempfile
import time
import shutil
import wave
from pathlib import Path
from uuid import UUID, uuid4

from .audio import MAX_SESSION_SAMPLES, SAMPLE_RATE, ownership_segments, pcm_to_wav, validate_wav
from .openai_translator import OPENAI_TRANSLATION_MODEL, OpenAITranslator
from .providers import ElevenLabsAdapter, ProviderError
from .provider_bootstrap import server_credentials, bootstrap_local_managed_providers

log = logging.getLogger("recorder.local_worker")
BLOCKED_CODES = {
    "engine_unconfigured",
    "translator_unconfigured",
    "invalid_credentials",
    "connection_unavailable",
    "provider_permission_denied",
    "provider_model_access_denied",
    "provider_access_denied",
    "provider_payment_required",
    "provider_quota_exceeded",
    "provider_plan_access_denied",
}
KNOWN_REJECTIONS = BLOCKED_CODES | {
    "provider_rate_limited",
    "provider_request_rejected",
    "provider_connection_failed",
    "translator_unavailable",
    "translator_invalid_input",
    "translator_input_too_large",
}


class LocalWorker:
    def __init__(self, settings, store=None):
        from .local_store import LocalStore

        self.settings = settings
        self.store = store or LocalStore(Path(settings.local_data_dir))
        self.id = os.environ.get("MAIN_WORKER_ID") or f"local-main-{uuid4()}"
        self.stopping = asyncio.Event()
        self.last_cycle = None
        self.last_cleanup = 0.0
        bootstrap_local_managed_providers(settings, self.store)

    def mutate(self, job, method, *args, **kwargs):
        return getattr(self.store, method)(job["id"], *args, lease_token=job.get("lease_token"), **kwargs)

    def credentials(self, engine: str) -> dict:
        configured = self.store.connection(engine)
        if configured and configured.get("config", {}).get("platform_managed"):
            credentials = server_credentials(self.settings, engine)
            if credentials: return credentials
            raise ProviderError("translator_unconfigured" if engine == "openai-translator" else "engine_unconfigured")
        if not configured or configured.get("state") != "connected":
            raise ProviderError("translator_unconfigured" if engine == "openai-translator" else "engine_unconfigured")
        secrets = self.store.secrets(engine)
        if not secrets or not secrets.get("api_key"):
            raise ProviderError("translator_unconfigured" if engine == "openai-translator" else "engine_unconfigured")
        return secrets

    def patch_conversation(self, conversation_id, **updates):
        self.store.patch("conversations", conversation_id, updates)

    def patch_run(self, job, **updates):
        run_id = job.get("payload", {}).get("run_id")
        if run_id:
            self.store.patch("runs", run_id, updates)

    def publish_turns(self, job, turns, version, speakers):
        self.store.replace_turns(job["conversation_id"], turns, version, speakers, job_id=job["id"], lease_token=job.get("lease_token"))

    async def renew(self, job):
        while True:
            await asyncio.sleep(30)
            self.store.renew_job(job["id"], job["lease_token"], lease_seconds=max(900, int(self.settings.worker_lease_seconds)))

    async def assemble(self, job):
        conversation_id = str(UUID(str(job["conversation_id"])))
        conversation = self.store.get("conversations", conversation_id)
        if not conversation or conversation.get("capture_state") == "deleted":
            raise ProviderError("permission_denied")
        rows = self.store.chunks(conversation_id)
        expected_samples = int(conversation.get("total_samples") or job.get("payload", {}).get("total_samples") or 0)
        if not rows or not 0 < expected_samples <= MAX_SESSION_SAMPLES:
            raise ProviderError("invalid_audio")
        destination = self.store.audio_path(conversation_id)
        self.store.assert_job(job["id"], job["lease_token"])
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, scratch = tempfile.mkstemp(prefix="assembly-", suffix=".wav", dir=destination.parent)
        os.close(descriptor)
        position = 0
        try:
            with wave.open(scratch, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(SAMPLE_RATE)
                for row in rows:
                    start = int(row.get("start_sample", row.get("sample_start", -1)))
                    if start != position:
                        raise ProviderError("invalid_audio")
                    path = Path(row["path"])
                    # Validate the generated conversation directory and use a
                    # bounded read so a modified/symlinked chunk cannot pull an
                    # arbitrary local file into a provider submission.
                    expected_path = self.store.chunk_path(conversation_id, row["part_id"], row["sequence"])
                    if path != expected_path or not path.resolve().is_relative_to(self.store.root):
                        raise ProviderError("invalid_audio")
                    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                    with os.fdopen(descriptor, "rb") as source:
                        data = source.read(180 * SAMPLE_RATE * 2 + 4097)
                    if len(data) > 180 * SAMPLE_RATE * 2 + 4096:
                        raise ProviderError("invalid_audio")
                    info = validate_wav(data, int(row["sample_count"]), max_samples=180 * SAMPLE_RATE)
                    if info.sha256 != row["sha256"] or len(data) != int(row["bytes"]):
                        raise ProviderError("input_hash_mismatch")
                    output.writeframesraw(info.pcm)
                    position += info.samples
                    if position > expected_samples:
                        raise ProviderError("invalid_audio")
            if position != expected_samples:
                raise ProviderError("invalid_audio")
            digest = hashlib.sha256()
            # Windows fsync uses a handle that requires write access. Keep
            # the completed WAV intact while granting that access for sync.
            with open(scratch, "r+b") as file:
                while data := file.read(1024 * 1024):
                    digest.update(data)
                os.fsync(file.fileno())
            os.chmod(scratch, 0o600)
            os.replace(scratch, destination)
        finally:
            Path(scratch).unlink(missing_ok=True)
        try:
            self.store.assert_job(job["id"], job["lease_token"])
        except ValueError:
            # A deletion can fence a worker between directory creation and
            # atomic rename. Do not leave reconstructed audio behind it.
            destination.unlink(missing_ok=True)
            raise
        self.patch_conversation(
            conversation_id,
            playback_object_path=str(destination),
            playback_path=str(destination),
            playback_state="complete",
            playback_sha256=digest.hexdigest(),
            playback_bytes=destination.stat().st_size,
        )
        if not conversation.get("audio_only"):
            self.store.enqueue_job(conversation_id, "transcribe", {**job.get("payload", {}), "total_samples": position})
        self.mutate(job, "complete_job", {"samples": position, "playback_path": str(destination)})

    @staticmethod
    def normalize_result(result, conversation_id: str, duration_seconds: float):
        if not isinstance(result, dict) or not isinstance(result.get("source_segments"), list):
            raise ProviderError("provider_invalid_response", uncertain=True)
        language = result.get("language")
        probability = result.get("language_probability")
        english = language == "en" and isinstance(probability, (int, float)) and probability >= 0.95
        segments = result["source_segments"]
        reliably_english = english and all(
            isinstance(segment.get("text"), str) and not any("\u0900" <= character <= "\u0dff" for character in segment["text"])
            for segment in segments
        )
        turns, speakers = [], {}
        for ordinal, segment in enumerate(segments):
            start, end, text = segment.get("start"), segment.get("end"), segment.get("text")
            if (
                not isinstance(text, str)
                or not isinstance(start, (int, float))
                or not isinstance(end, (int, float))
                or not math.isfinite(start)
                or not math.isfinite(end)
                or start < 0
                or end < start
                or end > duration_seconds + 0.5
            ):
                raise ProviderError("provider_invalid_timestamps", uncertain=True)
            speaker = segment.get("speaker")
            speaker_ref = f"{conversation_id}:{speaker}" if speaker is not None else None
            if speaker_ref is not None:
                speakers.setdefault(
                    speaker_ref, {"speaker_ref": speaker_ref, "display_name": None, "role": "other", "is_provisional": True}
                )
            words = []
            for word in segment.get("words") or []:
                if not isinstance(word, dict) or not isinstance(word.get("text"), str):
                    raise ProviderError("provider_invalid_timestamps", uncertain=True)
                left, right = word.get("start"), word.get("end")
                if (
                    not isinstance(left, (int, float))
                    or not isinstance(right, (int, float))
                    or not math.isfinite(left)
                    or not math.isfinite(right)
                    or left < start
                    or right < left
                    or right > end + 0.001
                ):
                    raise ProviderError("provider_invalid_timestamps", uncertain=True)
                words.append({"text": word["text"], "start": float(left), "end": float(right)})
            turns.append(
                {
                    "id": str(uuid4()),
                    "ordinal": ordinal,
                    "start_seconds": float(start),
                    "end_seconds": float(end),
                    "speaker_ref": speaker_ref,
                    "speaker_confidence": "provisional",
                    "attribution_state": "provisional",
                    "source_text": text,
                    "source_language": language or "auto",
                    "english_text": text if reliably_english else None,
                    "words": words,
                }
            )
        return turns, list(speakers.values()), reliably_english or not segments

    def live_audio(self, job):
        """Read and validate only the bounded confirmed chunks needed by this window."""
        payload, conversation_id = job["payload"], job["conversation_id"]
        start, end = int(payload["sample_start"]), int(payload["sample_end"])
        if not 0 <= start < end <= MAX_SESSION_SAMPLES or end - start > 40 * SAMPLE_RATE:
            raise ProviderError("invalid_audio")
        pcm, position = bytearray(), start
        for row in self.store.chunks(conversation_id):
            left, right = int(row["start_sample"]), int(row["start_sample"]) + int(row["sample_count"])
            if right <= start or left >= end:
                continue
            path = Path(row["path"])
            expected = self.store.chunk_path(conversation_id, row["part_id"], row["sequence"])
            if path != expected or not path.resolve().is_relative_to(self.store.root):
                raise ProviderError("invalid_audio")
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as source:
                data = source.read(180 * SAMPLE_RATE * 2 + 4097)
            info = validate_wav(data, int(row["sample_count"]), max_samples=180 * SAMPLE_RATE)
            if info.sha256 != row["sha256"] or len(data) != int(row["bytes"]):
                raise ProviderError("input_hash_mismatch")
            take_start, take_end = max(start, left), min(end, right)
            if take_start != position:
                raise ProviderError("invalid_audio")
            pcm.extend(info.pcm[(take_start-left)*2:(take_end-left)*2])
            position = take_end
        if position != end:
            raise ProviderError("invalid_audio")
        return io.BytesIO(pcm_to_wav(bytes(pcm)))

    def live_superseded(self, job):
        version = self.store.get_transcript(job["conversation_id"]).get("version") or {}
        return bool(version and version.get("kind") != "progressive")

    async def live_transcribe(self, job):
        conversation_id, payload = job["conversation_id"], job["payload"]
        conversation = self.store.get("conversations", conversation_id)
        if not conversation or conversation.get("capture_state") == "deleted":
            raise ProviderError("permission_denied")
        if self.live_superseded(job):
            self.mutate(job, "complete_job", {"superseded": True}); return
        credentials = self.credentials("elevenlabs")
        with self.live_audio(job) as audio:
            self.mutate(job, "mark_job_accepted", str(job["id"]))
            result = await ElevenLabsAdapter(credentials["api_key"]).process(audio, conversation.get("language_hint", "auto"), str(job["id"]))
        duration = (payload["sample_end"] - payload["sample_start"]) / SAMPLE_RATE
        relative, _, english = self.normalize_result(result, f"{conversation_id}:window-{payload['ownership_start']}", duration)
        segments = [{"start": turn["start_seconds"], "end": turn["end_seconds"], "text": turn["source_text"],
                     "speaker": turn["speaker_ref"], "words": turn["words"]} for turn in relative]
        owned = ownership_segments(segments, payload["sample_start"] / SAMPLE_RATE,
                                   payload["ownership_start"] / SAMPLE_RATE, payload["ownership_end"] / SAMPLE_RATE,
                                   f"{conversation_id}:window-{payload['ownership_start']}")
        turns, speakers = [], {}
        for ordinal, segment in enumerate(owned):
            speaker_ref = segment.get("speaker")
            if speaker_ref:
                speakers[speaker_ref] = {"speaker_ref": speaker_ref, "display_name": None, "role": "other", "is_provisional": True}
            turns.append({"id": str(uuid4()), "ordinal": ordinal, "start_seconds": segment["start"], "end_seconds": segment["end"],
                          "source_text": segment["text"], "source_language": result.get("language") or "auto",
                          "english_text": segment["text"] if english else None, "speaker_ref": speaker_ref,
                          "speaker_confidence": "provisional", "attribution_state": "provisional", "words": segment.get("words", [])})
        provenance = {**result.get("provenance", {}), "translation": "source-english" if english else "pending", "translated_word_alignment": None}
        published = self.store.publish_live_window(job, turns, list(speakers.values()), provenance)
        if published and not english:
            self.store.enqueue_job(conversation_id, "live_translate", {**payload, "source_job_id": job["id"]})
        self.mutate(job, "complete_job", {"window_id": payload["window_id"], "turns": len(turns), "superseded": not published})

    async def live_translate(self, job):
        conversation_id, payload = job["conversation_id"], job["payload"]
        if self.live_superseded(job):
            self.mutate(job, "complete_job", {"superseded": True}); return
        window = self.store.get("live_windows", f"{conversation_id}:{payload['window_id']}")
        if not window or window.get("source_job_id") != payload.get("source_job_id"):
            self.mutate(job, "complete_job", {"superseded": True}); return
        credentials = self.credentials("openai-translator")
        model = (self.store.connection("openai-translator") or {}).get("config", {}).get("model", OPENAI_TRANSLATION_MODEL)
        turns = window["turns"]
        for turn in turns:
            if turn.get("english_text") is not None:
                continue
            self.mutate(job, "mark_job_accepted", turn["id"])
            async def account_usage(usage):
                run = self.store.get("runs", payload["run_id"]) or {}
                self.patch_run(job, translation_input_tokens=int(run.get("translation_input_tokens", 0)) + usage.input_tokens,
                               translation_output_tokens=int(run.get("translation_output_tokens", 0)) + usage.output_tokens,
                               translator_model=model)
            translated = await OpenAITranslator(credentials["api_key"], usage_callback=account_usage, model=model).translate(turn["source_text"], turn.get("source_language"))
            turn["english_text"] = translated.text
            published = self.store.publish_live_window(job, turns, window["speakers"], {**window["provenance"], "translation": f"openai-{model}"})
            if not published:
                break
        self.mutate(job, "complete_job", {"window_id": payload["window_id"], "translated_turns": len(turns)})

    async def transcribe(self, job):
        conversation_id = job["conversation_id"]
        conversation = self.store.get("conversations", conversation_id)
        if not conversation or conversation.get("capture_state") == "deleted":
            raise ProviderError("permission_denied")
        credentials = self.credentials("elevenlabs")
        audio_path = self.store.audio_path(conversation_id)
        if not audio_path.is_file():
            raise ProviderError("invalid_audio")
        self.patch_conversation(conversation_id, transcription_state="processing", final_state="processing", processing_error_code=None)
        self.patch_run(job, state="running")
        with audio_path.open("rb") as audio:
            with wave.open(audio, "rb") as recording:
                if (
                    recording.getnchannels(),
                    recording.getsampwidth(),
                    recording.getframerate(),
                    recording.getcomptype(),
                    recording.getnframes(),
                ) != (1, 2, SAMPLE_RATE, "NONE", int(conversation["total_samples"])):
                    raise ProviderError("invalid_audio")
            audio.seek(0)
            digest = hashlib.sha256()
            while data := audio.read(1024 * 1024):
                digest.update(data)
            if conversation.get("playback_sha256") and digest.hexdigest() != conversation["playback_sha256"]:
                raise ProviderError("input_hash_mismatch")
            audio.seek(0)
            self.mutate(job, "mark_job_accepted", str(job["id"]))
            result = await ElevenLabsAdapter(credentials["api_key"]).process(
                audio, conversation.get("language_hint", "auto"), str(job["id"])
            )
        turns, speakers, english_complete = self.normalize_result(
            result, conversation_id, float(conversation["total_samples"]) / SAMPLE_RATE
        )
        version_id = str(uuid4())
        provenance = {
            **result.get("provenance", {}),
            "translation": "source-english" if english_complete else "pending",
            "translated_word_alignment": None,
        }
        version = {
            "id": version_id,
            "run_id": job.get("payload", {}).get("run_id"),
            "kind": "production",
            "english_state": "complete" if english_complete else "pending",
            "speaker_state": "complete",
            "coverage_samples": conversation["total_samples"],
            "provenance": provenance,
        }
        # Source turns and original word timing commit before any translation request.
        self.publish_turns(job, turns, version, speakers)
        self.patch_conversation(
            conversation_id,
            transcription_state="complete",
            source_language=result.get("language"),
            english_state=version["english_state"],
            speaker_state="complete",
            active_version_id=version_id,
            final_state="complete" if english_complete else "queued",
            processing_error_code=None,
        )
        self.patch_run(
            job,
            state="succeeded" if english_complete else "running",
            coverage_samples=conversation["total_samples"],
            model_version="scribe_v2",
            provenance=provenance,
            processed_audio_seconds=conversation["total_samples"] / SAMPLE_RATE,
        )
        # Persist only bounded parsed source output; provider secrets never enter records.
        if not english_complete:
            self.store.enqueue_job(conversation_id, "translate", {**job.get("payload", {}), "source_version_id": version_id})
        self.mutate(job, "complete_job", {"source_version_id": version_id, "source_language": result.get("language"), "turns": len(turns)})

    async def translate(self, job):
        conversation_id = job["conversation_id"]
        conversation = self.store.get("conversations", conversation_id)
        if not conversation or conversation.get("capture_state") == "deleted":
            raise ProviderError("permission_denied")
        credentials = self.credentials("openai-translator")
        model = (self.store.connection("openai-translator") or {}).get("config", {}).get("model", OPENAI_TRANSLATION_MODEL)
        transcript = self.store.get_transcript(conversation_id)
        version = transcript.get("version")
        if not version or not transcript.get("turns"):
            raise ProviderError("invalid_result")
        if job.get("payload", {}).get("source_version_id") and version.get("id") != job["payload"]["source_version_id"]:
            raise ProviderError("invalid_result")
        turns = transcript["turns"]
        self.patch_conversation(conversation_id, english_state="processing", final_state="processing", processing_error_code=None)
        for turn in turns:
            if turn.get("english_text") is not None:
                continue
            if not turn.get("source_text", "").strip():
                turn["english_text"] = turn.get("source_text", "")
                self.publish_turns(job, turns, version, transcript.get("speakers", []))
                continue
            self.mutate(job, "mark_job_accepted", turn["id"])

            async def account_usage(usage):
                run_id = job.get("payload", {}).get("run_id")
                prior = (self.store.get("runs", run_id) or {}) if run_id else {}
                self.patch_run(
                    job,
                    translation_input_tokens=int(prior.get("translation_input_tokens", 0)) + usage.input_tokens,
                    translation_output_tokens=int(prior.get("translation_output_tokens", 0)) + usage.output_tokens,
                    translator_model=model,
                )

            translated = await OpenAITranslator(credentials["api_key"], usage_callback=account_usage, model=model).translate(
                turn["source_text"], turn.get("source_language")
            )
            turn["english_text"] = translated.text
            # Cache each successful source turn immediately. An explicit recovery
            # translates only missing turns and never repeats speech inference.
            self.publish_turns(job, turns, version, transcript.get("speakers", []))
        provenance = {
            **version.get("provenance", {}),
            "translation": f"openai-{model}",
            "translation_model": model,
            "translated_word_alignment": None,
        }
        version = {**version, "english_state": "complete", "provenance": provenance}
        self.publish_turns(job, turns, version, transcript.get("speakers", []))
        self.patch_conversation(conversation_id, english_state="complete", final_state="complete", processing_error_code=None)
        self.patch_run(
            job, state="succeeded", provenance=provenance, translation_strategy="openai", translator_model=model
        )
        self.mutate(job, "complete_job", {"source_version_id": version["id"], "translated_turns": len(turns)})

    async def execute(self, job):
        renewal = asyncio.create_task(self.renew(job))
        execution = None
        try:
            operation = (
                self.live_transcribe(job)
                if job["kind"] == "live_transcribe"
                else self.live_translate(job)
                if job["kind"] == "live_translate"
                else self.assemble(job)
                if job["kind"] == "assemble"
                else self.transcribe(job)
                if job["kind"] in {"transcribe", "finalise"}
                else self.translate(job)
                if job["kind"] == "translate"
                else self.delete(job)
                if job["kind"] == "delete"
                else None
            )
            if operation is None:
                raise ProviderError("invalid_result")
            execution = asyncio.create_task(operation)
            done, _ = await asyncio.wait({execution, renewal}, return_when=asyncio.FIRST_COMPLETED)
            if renewal in done:
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
                renewal.result()
            else:
                execution.result()
        except asyncio.CancelledError:
            # The persisted accepted fence keeps a killed provider request from
            # becoming eligible for automatic resubmission after lease expiry.
            raise
        except Exception as error:
            code = (
                error.code
                if isinstance(error, ProviderError)
                else "invalid_audio"
                if isinstance(error, (ValueError, wave.Error))
                else "worker_execution_failed"
            )
            uncertain = isinstance(error, ProviderError) and error.uncertain
            state = "uncertain" if uncertain else "blocked" if code in BLOCKED_CODES else "failed"
            current = self.store.get("jobs", job["id"]) or {}
            accepted = current.get("submission_state") == "accepted" or bool(current.get("accepted_at"))
            if accepted and code not in KNOWN_REJECTIONS and code != "invalid_audio":
                state = "uncertain"
            self.mutate(
                job,
                "fail_job",
                state,
                code,
                "The server administrator needs to check the provider configuration before processing can resume."
                if state == "blocked"
                else "Review processing before retrying; a provider request may already have executed."
                if state == "uncertain"
                else "Your saved audio and source text are preserved. Check processing and retry explicitly.",
            )
            if job["kind"] == "delete":
                log.warning("local_cleanup_pending job_id=%s code=%s", job["id"], code)
                return
            if job["kind"] in {"live_transcribe", "live_translate"} and self.live_superseded(job):
                return
            updates = {"final_state": state, "processing_error_code": code}
            updates["english_state" if job["kind"] in {"translate", "live_translate"} else "transcription_state"] = state
            self.patch_conversation(job["conversation_id"], **updates)
            self.patch_run(job, state=state)
            log.warning("local_job_failed job_id=%s code=%s state=%s", job["id"], code, state)
        finally:
            renewal.cancel()
            if execution and not execution.done():
                execution.cancel()
            await asyncio.gather(renewal, *([execution] if execution else []), return_exceptions=True)

    async def run_once(self):
        self.last_cycle = time.monotonic()
        if self.last_cycle - self.last_cleanup >= 60:
            self.store.retry_cleanup_jobs()
            self.last_cleanup = self.last_cycle
        job = self.store.claim_job(self.id, lease_seconds=max(900, int(self.settings.worker_lease_seconds)))
        if job:
            await self.execute(job)
        return job is not None

    async def delete(self, job):
        conversation_id = str(UUID(str(job["conversation_id"])))
        self.store.assert_job(job["id"], job["lease_token"])
        row = self.store.get("conversations", conversation_id)
        if not row or row.get("capture_state") != "deleted":
            raise ProviderError("permission_denied")
        directory = self.store.root / "audio" / conversation_id
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.store.root):
            raise ProviderError("invalid_audio")
        if directory.exists():
            await asyncio.to_thread(shutil.rmtree, directory)
        self.store.finish_deleted_conversation(conversation_id, job["id"], job["lease_token"])

    async def run(self):
        print("Local processing worker started", flush=True)
        while not self.stopping.is_set():
            try:
                if not await self.run_once():
                    try:
                        await asyncio.wait_for(self.stopping.wait(), timeout=1)
                    except TimeoutError:
                        pass
            except Exception:
                log.warning("local_worker_cycle_failed code=dependency_unavailable")
                try:
                    await asyncio.wait_for(self.stopping.wait(), timeout=3)
                except TimeoutError:
                    pass
