"""Load large-v3/community-1 once. All imports of optional models stay here."""
from __future__ import annotations

import os
import struct
import wave
from pathlib import Path

from .alignment import align_segments

WHISPER_REVISION = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
PYANNOTE_REVISION = "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee"
MAX_SESSION_SAMPLES = 16000 * 7200


def partition_saved_segments(segments, start: float, end: float):
    """Keep saved source words exactly once when retrying bounded speaker groups."""
    output = []
    for segment in segments or []:
        if segment.get("words"):
            words = [{**word, "start": max(start, word["start"])-start, "end": min(end, word["end"])-start}
                for word in segment["words"] if start <= (word["start"]+word["end"])/2 < end]
            if words:
                output.append({**segment, "start": words[0]["start"], "end": words[-1]["end"],
                    "text": "".join(word["text"] for word in words), "words": words})
        elif start <= (segment["start"]+segment["end"])/2 < end:
            output.append({**segment, "start": max(start, segment["start"])-start,
                "end": min(end, segment["end"])-start, "words": None})
    return output


def validate_audio_file(audio_path: str) -> int:
    """Reject malformed/oversized containers before any model/decoder sees them."""
    path = Path(audio_path)
    size = path.stat().st_size
    if not 44 < size <= MAX_SESSION_SAMPLES * 2 + 4096:
        raise ValueError("invalid_worker_audio")
    with path.open("rb") as source:
        header = source.read(12)
        if header[:4] != b"RIFF" or header[8:] != b"WAVE" or struct.unpack_from("<I", header, 4)[0] + 8 != size:
            raise ValueError("invalid_worker_audio")
        source.seek(0)
        with wave.open(source, "rb") as audio:
            total = audio.getnframes()
            if ((audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE")
                    or not 0 < total <= MAX_SESSION_SAMPLES):
                raise ValueError("invalid_worker_audio")
            # Stream the payload so forged data lengths/truncated PCM cannot
            # reach native decoders, while a two-hour file stays bounded in RAM.
            count = 0
            while block := audio.readframes(16000 * 30):
                count += len(block)
            if count != total * 2:
                raise ValueError("invalid_worker_audio")
    return total


class Engine:
    def __init__(self):
        from huggingface_hub import snapshot_download
        from faster_whisper import WhisperModel
        from pyannote.audio import Pipeline
        import torch

        device = os.environ.get("INFERENCE_DEVICE", "cpu")
        if device not in {"cpu", "cuda"}:
            raise ValueError("unsupported_inference_device")
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("cuda_not_available")
        cache = Path(os.environ.get("MODEL_CACHE", "./models")).resolve()
        token = os.environ.get("HF_TOKEN")
        whisper = snapshot_download("Systran/faster-whisper-large-v3", revision=WHISPER_REVISION, cache_dir=cache / "whisper")
        diarization = snapshot_download("pyannote/speaker-diarization-community-1", revision=PYANNOTE_REVISION, cache_dir=cache / "pyannote", token=token)
        self.model = WhisperModel(whisper, device=device, compute_type="int8" if device == "cpu" else "float16", cpu_threads=int(os.environ.get("INFERENCE_CPU_THREADS", "4")), num_workers=1)
        # Use a pinned local pipeline directory rather than an unpinned model identifier.
        self.pipeline = Pipeline.from_pretrained(diarization, token=token)
        self.pipeline.to(torch.device(device))
        self.device = device
        self.model_version = f"large-v3@{WHISPER_REVISION}/community-1@{PYANNOTE_REVISION}"

    def process(self, audio_path: str, job: dict) -> dict:
        """Bound long sessions; speaker identities never silently span bounded groups."""
        import tempfile
        import wave
        block_seconds = int(os.environ.get("INFERENCE_BLOCK_SECONDS", "900"))
        if not 30 <= block_seconds <= 900:
            raise ValueError("invalid_inference_block_seconds")
        block_samples = 16000 * block_seconds
        validate_audio_file(audio_path)
        with wave.open(audio_path, "rb") as audio:
            total = audio.getnframes()
            if total <= block_samples:
                return self._process_block(audio_path, job)
            results = []
            with tempfile.TemporaryDirectory(prefix="guru-turns-") as scratch:
                offset = 0
                while offset < total:
                    path = str(Path(scratch) / "block.wav")
                    with wave.open(path, "wb") as block:
                        block.setnchannels(1); block.setsampwidth(2); block.setframerate(16000)
                        block.writeframes(audio.readframes(min(block_samples, total-offset)))
                    base = job.get('base_result')
                    if base:
                        base={**base}
                        for key in ('source_segments','english_segments'):
                            base[key] = partition_saved_segments(base.get(key), offset/16000, min(total, offset+block_samples)/16000)
                    result = self._process_block(path, {**job, "base_result":base,"job_id": f"{job['job_id']}:group-{offset//block_samples}"})
                    for key in ("source_segments", "english_segments", "diarization_intervals"):
                        for segment in result.get(key) or []:
                            segment["start"] += offset/16000; segment["end"] += offset/16000
                            for word in segment.get("words") or []: word["start"] += offset/16000; word["end"] += offset/16000
                    results.append(result); offset += block_samples
        return {"language": results[0]["language"], "source_segments": [segment for result in results for segment in result.get("source_segments") or []] or None,
                "english_segments": [segment for result in results for segment in result["english_segments"]],
                "diarization_intervals": [segment for result in results for segment in result["diarization_intervals"]],
                "diarization_status": "failed" if any(result["diarization_status"] == "failed" for result in results) else "completed",
                "provenance": {**results[0]["provenance"], "session_speakers_unresolved": True, "bounded_groups": len(results), "block_samples": block_samples}}

    def _process_block(self, audio_path: str, job: dict) -> dict:
        import numpy as np
        import torch
        import wave
        from types import SimpleNamespace

        hint = job.get("language_hint")
        hint = {"kan": "kn", "hin": "hi", "ben": "bn", "eng": "en"}.get(hint, hint)
        if hint == "auto": hint = None
        speaker_only = job.get("kind") == "diarize"
        if speaker_only:
            base=job.get("base_result") or {}
            if base.get("english_segments") is None: raise ValueError("speaker_retry_requires_saved_english")
            english=[dict(segment) for segment in base['english_segments']]
            source=[dict(segment) for segment in base.get('source_segments') or []] or None
            info=SimpleNamespace(language=base.get('language'),language_probability=None)
        else:
            segments, info = self.model.transcribe(audio_path, task="translate", language=hint,
                                                   beam_size=5, vad_filter=True, word_timestamps=False,
                                                   condition_on_previous_text=True)
            english = [{"start": float(segment.start), "end": float(segment.end), "text": segment.text.strip(), "words": None}
                       for segment in segments]
            source = None
        if not speaker_only and job.get("config_snapshot", {}).get("source_pass"):
            original, _ = self.model.transcribe(audio_path, task="transcribe", language=hint, beam_size=5, word_timestamps=True, vad_filter=True)
            source = [{"start": float(segment.start), "end": float(segment.end), "text": segment.text.strip(),
                       "words": [{"text": word.word, "start": word.start, "end": word.end} for word in segment.words or []]}
                      for segment in original]
        scope = str(job["run_id"]) + ":" + str(job["job_id"])
        intervals = []; speaker_status = "completed"
        try:
            # PCM input avoids pyannote/torchcodec dependence on a system FFmpeg decoder.
            with wave.open(audio_path, "rb") as wav:
                if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                    raise ValueError("invalid_worker_audio")
                pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768
            result = self.pipeline({"waveform": torch.from_numpy(pcm).unsqueeze(0), "sample_rate": 16000})
            regular = result.speaker_diarization
            for turn, _, speaker in regular.itertracks(yield_label=True):
                intervals.append({"start": float(turn.start), "end": float(turn.end), "speaker": str(speaker)})
            english = align_segments(english, intervals, scope)
            # A separate source pass cannot imply segment-for-segment translated alignment.
            if source: source = align_segments(source, intervals, scope)
        except Exception:
            speaker_status = "failed"
            english = [{**segment, "speaker": None, "speaker_confidence": "uncertain"} for segment in english]
        for index, interval in enumerate(intervals):
            interval["overlap"] = any(other["speaker"] != interval["speaker"] and max(other["start"], interval["start"]) < min(other["end"], interval["end"]) for other in intervals[:index] + intervals[index+1:])
            interval["speaker"] = f"{scope}:{interval['speaker']}"
        return {"language": info.language, "language_probability": info.language_probability,
                "source_segments": source, "english_segments": english, "english_state": "complete" if job.get("final") else "available",
                "diarization_status": speaker_status, "diarization_intervals": intervals,
                "provenance": {"engine": "faster-whisper", "model": self.model_version, "device": self.device,
                               "native_english": True, "translated_word_alignment": None,
                               "original_language_pass": bool(source), "additional_source_pass": bool(source) and not speaker_only,"speaker_only_retry":speaker_only}}
