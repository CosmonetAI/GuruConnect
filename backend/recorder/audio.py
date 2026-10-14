"""Bounded PCM validation/assembly using stdlib wave; no system FFmpeg."""
from __future__ import annotations

import hashlib
import io
import struct
import wave
from dataclasses import dataclass
from typing import Iterable

SAMPLE_RATE = 16_000
MAX_SESSION_SAMPLES = SAMPLE_RATE * 7200


@dataclass(frozen=True)
class WavInfo:
    samples: int
    sample_rate: int
    pcm: bytes
    sha256: str

    @property
    def sample_count(self) -> int:
        return self.samples


def validate_wav(data: bytes, expected_samples: int | None = None,
                 max_samples: int = SAMPLE_RATE * 120) -> WavInfo:
    if len(data) < 44 or len(data) > max_samples * 2 + 4096:
        raise ValueError("audio_size_invalid")
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("audio_not_wav")
    if struct.unpack_from("<I", data, 4)[0] + 8 != len(data):
        raise ValueError("audio_riff_size_invalid")
    try:
        with wave.open(io.BytesIO(data), "rb") as recording:
            if (recording.getnchannels(), recording.getsampwidth(), recording.getframerate(), recording.getcomptype()) != (1, 2, SAMPLE_RATE, "NONE"):
                raise ValueError("audio_format_invalid")
            samples = recording.getnframes()
            if not 0 < samples <= max_samples:
                raise ValueError("audio_sample_count_invalid")
            if expected_samples is not None and expected_samples != samples:
                raise ValueError("audio_sample_count_mismatch")
            pcm = recording.readframes(samples + 1)
            if len(pcm) != samples * 2:
                raise ValueError("audio_truncated")
    except (wave.Error, EOFError, struct.error) as exc:
        raise ValueError("audio_decode_failed") from exc
    return WavInfo(samples, SAMPLE_RATE, pcm, hashlib.sha256(data).hexdigest())


def pcm_to_wav(pcm: bytes) -> bytes:
    if len(pcm) % 2:
        raise ValueError("unaligned_pcm")
    output = io.BytesIO()
    with wave.open(output, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(SAMPLE_RATE)
        recording.writeframes(pcm)
    return output.getvalue()


def assemble_wav(chunks: Iterable[bytes], start_sample: int = 0,
                 end_sample: int | None = None) -> bytes:
    if start_sample < 0 or (end_sample is not None and end_sample <= start_sample):
        raise ValueError("audio_range_invalid")
    output = bytearray()
    position = 0
    for data in chunks:
        recording = validate_wav(data)
        upper = position + recording.samples
        if upper > MAX_SESSION_SAMPLES:
            raise ValueError("conversation_duration_exceeded")
        left = max(0, start_sample - position)
        right = min(recording.samples, (end_sample - position) if end_sample is not None else recording.samples)
        if right > left:
            output.extend(recording.pcm[left * 2:right * 2])
        position = upper
    if start_sample >= position or (end_sample is not None and end_sample > position):
        raise ValueError("audio_range_missing")
    return pcm_to_wav(bytes(output))


def ownership_segments(segments: list[dict], context_start: float, owned_start: float,
                       owned_end: float, speaker_scope: str) -> list[dict]:
    """Retain words by midpoint ownership, not text dedupe (real repetitions survive)."""
    output = []
    for original in segments:
        segment = dict(original)
        words = original.get("words")
        if words:
            kept = [dict(word, start=float(word["start"]) + context_start,
                         end=float(word["end"]) + context_start)
                    for word in words
                    if owned_start <= (float(word["start"]) + float(word["end"])) / 2 + context_start < owned_end]
            if not kept:
                continue
            segment.update(words=kept, text="".join(word["text"] for word in kept),
                           start=max(owned_start, kept[0]["start"]), end=min(owned_end, kept[-1]["end"]))
        else:
            start = float(segment["start"]) + context_start
            end = float(segment["end"]) + context_start
            if not owned_start <= (start + end) / 2 < owned_end:
                continue
            segment.update(start=max(owned_start, start), end=min(owned_end, end))
        if segment.get("speaker") is not None:
            # Native adapters already use run/job scopes shared with their
            # diarization intervals. Preserve those references consistently.
            speaker=str(segment['speaker'])
            segment["speaker"] = speaker if speaker.startswith(speaker_scope.split(':')[0]+':') else f"{speaker_scope}:{speaker}"
        output.append(segment)
    return output
