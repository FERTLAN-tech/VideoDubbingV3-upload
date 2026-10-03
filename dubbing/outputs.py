"""Audio assembly, subtitles (SRT) and the timing report (JSON)."""
import json
import textwrap
import wave
from pathlib import Path

import numpy as np

from . import config


class AssemblyError(RuntimeError):
    pass


def _read_wav(path):
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != config.TTS_SAMPLE_RATE:
            raise AssemblyError(f"Unexpected audio format in {Path(path).name}")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def assemble_audio(results, total_duration, out_wav):
    """Place every clip at its original start time on a silent track as long as the video."""
    sr = config.TTS_SAMPLE_RATE
    track = np.zeros(int(round(total_duration * sr)), dtype=np.int32)
    prev_end = 0
    for r in sorted(results, key=lambda r: r.segment.start):
        if not r.audio_path:
            continue
        clip = _read_wav(r.audio_path)
        start = int(round(r.segment.start * sr))
        limit = int(round(r.segment.slot_end * sr))
        if start < prev_end:
            raise AssemblyError(f"Segment {r.segment.index} would overlap the previous segment.")
        clip = clip[: max(0, min(limit, len(track)) - start)]   # hard guarantee: never past the slot
        track[start:start + len(clip)] += clip
        prev_end = start + len(clip)

    track = np.clip(track, -32768, 32767).astype(np.int16)
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(track.tobytes())
    return Path(out_wav)


def _srt_time(t):
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def create_srt(results, out_path):
    lines, n = [], 0
    for r in sorted(results, key=lambda r: r.segment.start):
        if not r.translated_text:
            continue
        n += 1
        start = r.segment.start
        # same timing as the voice; keep very short lines readable but inside the slot
        end = min(r.segment.slot_end, max(r.voice_end, start + 1.0))
        text = "\n".join(textwrap.wrap(r.translated_text, width=42)) or r.translated_text
        lines.append(f"{n}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n")
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
    return Path(out_path)


def create_timing_json(results, out_path, meta):
    segments = []
    for r in sorted(results, key=lambda r: r.segment.start):
        s = r.segment
        segments.append({
            "index": s.index,
            "start": round(s.start, 3),
            "source_end": round(s.end, 3),
            "slot_end": round(s.slot_end, 3),
            "slot_duration": round(s.slot_duration, 3),
            "natural_duration": round(s.natural_duration, 3),
            "source_text": s.source_text,
            "translated_text": r.translated_text,
            "tts_duration": r.tts_duration,
            "stretch_factor": r.stretch_factor,
            "final_duration": r.final_duration,
            "voice_end": round(r.voice_end, 3),
            "timing_mode": r.timing_mode,
            "steps": r.steps,
            "api_attempts": r.attempts,
            "warnings": r.warnings,
        })
    modes = {}
    for seg in segments:
        modes[seg["timing_mode"]] = modes.get(seg["timing_mode"], 0) + 1
    summary = {"segments": len(segments), "timing_modes": modes}
    usage = meta.get("api_usage")
    if usage:                                   # API calls made, results reused, money saved
        summary.update(api_calls=sum(usage["api_calls"].values()),
                       cache_hits=sum(usage["cache_hits"].values()), saved_usd=usage.get("saved_usd"))
    report = {
        **meta,
        "settings": {
            "MAX_SPEED_UP": config.MAX_SPEED_UP, "MAX_SLOW_DOWN": config.MAX_SLOW_DOWN,
            "SAFETY_GAP": config.SAFETY_GAP, "REWRITE_TRIES": config.REWRITE_TRIES,
            "SHORT_THRESHOLD": config.SHORT_THRESHOLD,
            "ECONOMY_MODE": config.ECONOMY_MODE,
            "ECONOMY_SPEEDUP_LIMIT": config.ECONOMY_SPEEDUP_LIMIT,
            "ECONOMY_SLOWDOWN_LIMIT": config.ECONOMY_SLOWDOWN_LIMIT,
            "PREDICT_BEFORE_TTS": config.PREDICT_BEFORE_TTS,
            "REWRITE_MODEL": config.REWRITE_MODEL or config.TEXT_MODEL,
            "TRANSCRIBE_MODEL": config.TRANSCRIBE_MODEL,
            "TEXT_MODEL": config.TEXT_MODEL, "TTS_MODEL": config.TTS_MODEL,
        },
        "summary": summary,
        "segments": segments,
    }
    Path(out_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return Path(out_path)
