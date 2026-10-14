"""Conservative speaker assignment; preserve overlap and uncertain multi-voice turns."""
from __future__ import annotations


def align_segments(segments: list[dict], intervals: list[dict], scope: str) -> list[dict]:
    output = []
    for segment in segments:
        start, end = float(segment["start"]), float(segment["end"])
        if end <= start:
            continue
        overlaps: dict[str, float] = {}
        supported = []
        for interval in intervals:
            duration = min(end, float(interval["end"])) - max(start, float(interval["start"]))
            if duration <= 0:
                continue
            speaker = str(interval["speaker"])
            overlaps[speaker] = overlaps.get(speaker, 0.0) + duration
            supported.append(interval)
        meaningful = {speaker for speaker, seconds in overlaps.items() if seconds >= min(0.15, (end-start)*0.1)}
        overlap = any(max(start, a["start"], b["start"]) < min(end, a["end"], b["end"])
                      for i, a in enumerate(supported) for b in supported[i+1:] if a["speaker"] != b["speaker"])
        speaker = next(iter(meaningful)) if len(meaningful) == 1 and not overlap else None
        if speaker and overlaps[speaker] / (end-start) < 0.8:
            speaker = None
        output.append({**segment, "speaker": f"{scope}:{speaker}" if speaker else None,
                       "speaker_confidence": "supported" if speaker else "overlap" if overlap else "uncertain"})
    return output
