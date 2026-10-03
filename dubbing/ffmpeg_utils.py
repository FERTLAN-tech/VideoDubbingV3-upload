"""FFmpeg / FFprobe helpers: probing, extraction, time-stretching, muxing."""
import json
import os
import re
import shutil
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

from . import config


class FFmpegError(RuntimeError):
    pass


_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _registry_path_dirs():
    """PATH as currently saved in Windows (it may be newer than our own environment)."""
    if sys.platform != "win32":
        return []
    import winreg
    dirs = []
    keys = [(winreg.HKEY_CURRENT_USER, r"Environment"),
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")]
    for root, sub in keys:
        try:
            with winreg.OpenKey(root, sub) as k:
                value, _ = winreg.QueryValueEx(k, "Path")
                dirs += [os.path.expandvars(p) for p in value.split(";") if p.strip()]
        except OSError:
            pass
    return [Path(d) for d in dirs]


def extra_tool_dirs():
    """Folders where install.bat / winget put FFmpeg and Deno."""
    dirs = [config.BASE_DIR / "ffmpeg" / "bin"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        winget = Path(local) / "Microsoft" / "WinGet"
        dirs.append(winget / "Links")
        dirs += sorted(winget.glob("Packages/Gyan.FFmpeg*/*/bin"))
        dirs += sorted(winget.glob("Packages/DenoLand.Deno*"))
    dirs.append(Path.home() / ".deno" / "bin")
    dirs += _registry_path_dirs()
    return [d for d in dirs if d.is_dir()]


def prepare_path():
    """Make freshly installed tools visible even if PATH was not refreshed."""
    parts = os.environ.get("PATH", "").split(os.pathsep)
    known = {p.lower().rstrip("\\") for p in parts}
    extra = [str(d) for d in extra_tool_dirs() if str(d).lower().rstrip("\\") not in known]
    os.environ["PATH"] = os.pathsep.join(parts + extra)


def find_tool(name):
    prepare_path()
    path = shutil.which(name)
    if not path:
        raise FFmpegError(
            f"{name} was not found. Run install.bat again, or install FFmpeg "
            f"(winget install Gyan.FFmpeg) and restart the application."
        )
    return path


def run(args, what, capture_stderr_log=False):
    proc = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()[-1500:]
        raise FFmpegError(f"{what} failed (exit code {proc.returncode}).\n{tail}")
    return proc


def ffmpeg(args, what):
    return run([find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args], what)


def ffprobe_json(path):
    proc = run([find_tool("ffprobe"), "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path)], f"Reading {Path(path).name}")
    return json.loads(proc.stdout or "{}")


# --------------------------------------------------------------------------
# Video analysis
# --------------------------------------------------------------------------
@dataclass
class VideoInfo:
    path: str
    duration: float
    width: int
    height: int
    fps: float
    video_codec: str
    pix_fmt: str
    format_name: str
    has_audio: bool
    audio_codec: str = ""
    audio_sample_rate: int = 0
    audio_channels: int = 0

    def summary(self):
        audio = (f"{self.audio_codec} {self.audio_sample_rate} Hz {self.audio_channels} ch"
                 if self.has_audio else "no audio")
        return (f"{self.width}x{self.height} @ {self.fps:.3f} fps, {self.video_codec}, "
                f"{self.duration:.2f} s, {self.format_name}, audio: {audio}")


def _rate(text):
    try:
        if "/" in text:
            num, den = text.split("/")
            return float(num) / float(den) if float(den) else 0.0
        return float(text)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe_video(path):
    data = ffprobe_json(path)
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise FFmpegError("The downloaded file contains no video stream.")

    duration = float(video.get("duration") or 0) or float(fmt.get("duration") or 0)
    if duration <= 0:
        raise FFmpegError("Could not determine the video duration.")

    return VideoInfo(
        path=str(path),
        duration=duration,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=_rate(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0"),
        video_codec=video.get("codec_name", ""),
        pix_fmt=video.get("pix_fmt", ""),
        format_name=fmt.get("format_name", ""),
        has_audio=audio is not None,
        audio_codec=(audio or {}).get("codec_name", ""),
        audio_sample_rate=int((audio or {}).get("sample_rate") or 0),
        audio_channels=int((audio or {}).get("channels") or 0),
    )


# --------------------------------------------------------------------------
# Audio helpers
# --------------------------------------------------------------------------
def extract_audio(video_path, out_wav):
    """16 kHz mono PCM: the best input format for speech recognition."""
    ffmpeg(["-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav)],
           "Audio extraction")
    return Path(out_wav)


def encode_chunk_for_upload(wav_path, out_path, start, duration):
    ffmpeg(["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(wav_path),
            "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", config.TRANSCRIBE_BITRATE, str(out_path)],
           "Preparing audio chunk")
    return Path(out_path)


def detect_silences(wav_path, noise_db=-35, min_duration=0.3):
    """Return a list of (start, end) silences, used to cut transcription chunks cleanly."""
    proc = run([find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-nostats", "-i", str(wav_path),
                "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}", "-f", "null", "-"],
               "Silence detection")
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", proc.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", proc.stderr)]
    return list(zip(starts, ends))


def measure_audio_duration(path):
    path = Path(path)
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                return w.getnframes() / float(w.getframerate())
        except (wave.Error, EOFError):
            pass
    data = ffprobe_json(path)
    return float(data.get("format", {}).get("duration") or 0)


def normalize_tts(in_path, out_path, trim_silence=True):
    """Convert TTS output to 24 kHz mono PCM and remove leading/trailing silence."""
    base = ["-i", str(in_path), "-ac", "1", "-ar", str(config.TTS_SAMPLE_RATE), "-c:a", "pcm_s16le"]
    if trim_silence:
        trim = ("silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.03,"
                "areverse,silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05,areverse")
        ffmpeg(base[:2] + ["-af", trim] + base[2:] + [str(out_path)], "TTS clean-up")
        if measure_audio_duration(out_path) > 0.05:
            return Path(out_path)
    ffmpeg(base + [str(out_path)], "TTS conversion")
    return Path(out_path)


def time_stretch(in_path, out_path, factor):
    """Change speech speed without changing pitch. factor > 1 = faster, < 1 = slower."""
    if not 0.5 <= factor <= 2.0:
        raise ValueError(f"Unsupported stretch factor {factor}")
    ffmpeg(["-i", str(in_path), "-af", f"atempo={factor:.5f}", "-ac", "1",
            "-ar", str(config.TTS_SAMPLE_RATE), "-c:a", "pcm_s16le", str(out_path)], "Time stretching")
    return Path(out_path)


def trim_with_fade(in_path, out_path, duration, fade=None):
    fade = config.TRIM_FADE if fade is None else fade
    fade = min(fade, duration / 2)
    ffmpeg(["-i", str(in_path), "-t", f"{duration:.4f}",
            "-af", f"afade=t=out:st={max(0.0, duration - fade):.4f}:d={fade:.4f}",
            "-ac", "1", "-ar", str(config.TTS_SAMPLE_RATE), "-c:a", "pcm_s16le", str(out_path)], "Trimming")
    return Path(out_path)


# --------------------------------------------------------------------------
# Final muxing
# --------------------------------------------------------------------------
def mux_video(video_info, dub_wav, out_mp4):
    """Original video track (timing untouched) + dubbed audio -> YouTube/TikTok friendly MP4."""
    args = ["-i", video_info.path, "-i", str(dub_wav)]

    if config.ORIGINAL_AUDIO_VOLUME > 0 and video_info.has_audio:
        args += ["-filter_complex",
                 f"[0:a:0]volume={config.ORIGINAL_AUDIO_VOLUME}[bg];"
                 f"[1:a:0][bg]amix=inputs=2:duration=first:normalize=0[aout]",
                 "-map", "0:v:0", "-map", "[aout]"]
    else:
        args += ["-map", "0:v:0", "-map", "1:a:0"]

    if video_info.video_codec == "h264" and video_info.pix_fmt in ("yuv420p", "yuvj420p"):
        args += ["-c:v", "copy"]
    else:
        args += ["-c:v", "libx264", "-preset", config.VIDEO_PRESET, "-crf", str(config.VIDEO_CRF),
                 "-pix_fmt", "yuv420p", "-profile:v", "high"]

    args += ["-c:a", "aac", "-b:a", config.OUTPUT_AUDIO_BITRATE, "-ar", str(config.OUTPUT_AUDIO_RATE),
             "-ac", "2", "-t", f"{video_info.duration:.3f}", "-movflags", "+faststart", str(out_mp4)]
    ffmpeg(args, "MP4 export")
    return Path(out_mp4)
