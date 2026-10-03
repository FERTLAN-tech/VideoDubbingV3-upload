"""Quality control of the final deliverables."""
from pathlib import Path

from . import config
from .ffmpeg_utils import FFmpegError, ffmpeg, ffprobe_json


class ValidationError(RuntimeError):
    pass


def validate_output(mp4, srt, timing_json, results, expected_duration):
    problems, checks = [], []

    def check(ok, msg):
        (checks if ok else problems).append(msg)

    mp4 = Path(mp4)
    check(mp4.exists() and mp4.stat().st_size > 0, "final MP4 exists")
    check(Path(srt).exists(), "SRT exists")
    check(Path(timing_json).exists(), "timing JSON exists")

    if mp4.exists():
        try:
            info = ffprobe_json(mp4)
            streams = info.get("streams", [])
            v = next((s for s in streams if s.get("codec_type") == "video"), None)
            a = next((s for s in streams if s.get("codec_type") == "audio"), None)
            check(True, "MP4 opens with FFprobe")
            check(v is not None, "video stream present")
            check(a is not None, "audio stream present")
            fmt_dur = float(info.get("format", {}).get("duration") or 0)
            vd = float((v or {}).get("duration") or fmt_dur)
            ad = float((a or {}).get("duration") or fmt_dur)
            tol = config.DURATION_TOLERANCE
            check(abs(vd - expected_duration) <= tol,
                  f"video duration {vd:.2f} s (expected {expected_duration:.2f} s)")
            check(abs(ad - expected_duration) <= tol,
                  f"audio duration {ad:.2f} s (expected {expected_duration:.2f} s)")
            ffmpeg(["-i", str(mp4), "-map", "0:a:0", "-f", "null", "-"], "Audio decode test")
            check(True, "audio decodes without errors")
        except FFmpegError as exc:
            check(False, f"FFmpeg could not read the final MP4: {exc}")

    ordered = sorted(results, key=lambda r: r.segment.start)
    overlaps = []
    for cur, nxt in zip(ordered, ordered[1:]):
        if cur.voice_end > nxt.segment.start + 1e-3:
            overlaps.append(cur.segment.index)
    for r in ordered:
        if r.voice_end > r.segment.slot_end + 1e-3:
            overlaps.append(r.segment.index)
    check(not overlaps, "no segment overlaps the next one"
          if not overlaps else f"segments overlapping: {sorted(set(overlaps))}")

    if problems:
        raise ValidationError("Final video check failed:\n- " + "\n- ".join(problems))
    return checks
