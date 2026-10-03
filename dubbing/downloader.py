"""YouTube URL validation and download (yt-dlp)."""
import logging
import re
from pathlib import Path

from . import config
from .ffmpeg_utils import find_tool

log = logging.getLogger("dubbing")

_YOUTUBE_RE = re.compile(
    r"^(https?://)?((www|m|music)\.)?"
    r"(youtube\.com/(watch\?(.*&)?v=|shorts/|live/|embed/|v/)|youtu\.be/)"
    r"[A-Za-z0-9_-]{11}"
)


class DownloadError(RuntimeError):
    pass


def validate_youtube_url(url):
    url = (url or "").strip()
    if not url:
        raise ValueError("Please paste a YouTube video URL.")
    if not _YOUTUBE_RE.match(url):
        raise ValueError("This does not look like a YouTube video URL.\n"
                         "Example: https://www.youtube.com/watch?v=XXXXXXXXXXX")
    if not url.startswith("http"):
        url = "https://" + url
    return url


_ID_RE = re.compile(r"(?:[?&]v=|youtu\.be/|shorts/|live/|embed/|/v/)([A-Za-z0-9_-]{11})")


def youtube_video_id(url):
    """The 11-character video id of a YouTube URL, or '' (used as the transcript cache key)."""
    m = _ID_RE.search(url or "")
    return m.group(1) if m else ""


def download_video(url, work_dir, on_progress=None):
    """Download the best MP4-compatible version. Returns (path, title)."""
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError as YtDlpError
    except ImportError as exc:
        raise DownloadError("yt-dlp is not installed. Run install.bat again.") from exc

    work_dir = Path(work_dir)
    h = config.VIDEO_MAX_HEIGHT

    def hook(d):
        if on_progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                on_progress(min(1.0, d.get("downloaded_bytes", 0) / total))

    opts = {
        # Prefer H.264 + AAC (can be copied into the final MP4 without re-encoding)
        "format": (f"bv*[vcodec^=avc1][height<={h}]+ba[ext=m4a]/"
                   f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"),
        "merge_output_format": "mp4",
        "outtmpl": str(work_dir / "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
        "fragment_retries": 5,
        "ffmpeg_location": find_tool("ffmpeg"),
        "progress_hooks": [hook],
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except YtDlpError as exc:
        raise DownloadError(
            f"Could not download the video.\n{exc}\n\n"
            "If YouTube changed something, run update.bat to update the downloader."
        ) from exc

    files = [p for p in work_dir.glob("source.*") if p.suffix not in (".part", ".ytdl")]
    if not files:
        raise DownloadError("The download finished but no video file was found.")
    files.sort(key=lambda p: (p.suffix != ".mp4", -p.stat().st_size))
    title = (info or {}).get("title") or "video"
    log.info("Downloaded: %s (%s)", title, files[0].name)
    return files[0], title
