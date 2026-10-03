"""
Background jobs for the web interface.

One job at a time runs in a thread. Everything it does is turned into small
events ("stage", "progress", "log", "segment", "spend", "status"...) that are
pushed to every connected browser (Server-Sent Events). A full snapshot of the
state is available for clients that connect later.

The job's working folder (downloaded video, voice clips) is kept while the job
is shown, so the clips can be played, and deleted when the job is closed, when
a new job starts or when the app quits.
"""
import json
import logging
import os
import queue
import shutil
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import config
from .costs import CostTracker, UsageLedger
from .downloader import validate_youtube_url
from .languages import get_language
from .logging_setup import setup_logging
from .pipeline import JobCancelled, PipelineHooks, friendly_error, run_pipeline
from .settings import apply_settings, load_settings, resolve_keys, usage_path

log = logging.getLogger("dubbing")

WORK_PREFIX = "vdubui_"

STAGES = [
    ("download", "Download"),
    ("probe", "Analyse video"),
    ("extract", "Extract audio"),
    ("transcribe", "Transcribe"),
    ("translate", "Translate"),
    ("voice", "Voice"),
    ("timing", "Timing fit"),
    ("assemble", "Build audio"),
    ("export", "Export MP4"),
    ("validate", "Quality check"),
]

MAX_LOG_LINES = 600


class JobError(RuntimeError):
    """A request that cannot be done now (shown to the user as is)."""


def fake_mode():
    return os.environ.get("DUBBING_FAKE_ENGINE", "").strip() == "1"


def cleanup_stale_work_dirs(max_age_hours=6):
    """Remove working folders left behind by a previous run that was killed."""
    limit = time.time() - max_age_hours * 3600
    for d in Path(tempfile.gettempdir()).glob(WORK_PREFIX + "*"):
        try:
            if d.is_dir() and d.stat().st_mtime < limit:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _r(x, n=3):
    return None if x is None else round(float(x), n)


class _LogForwarder(logging.Handler):
    def __init__(self, job):
        super().__init__()
        self.job = job
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        try:
            line = self.format(record)
            level = record.levelname.lower()
            self.job.manager._log(self.job, line, level)
        except Exception:  # noqa: BLE001
            pass


class _JobHooks(PipelineHooks):
    def __init__(self, job):
        self.job = job
        self.m = job.manager

    def stage(self, stage, status, message=""):
        self.m._stage(self.job, stage, status, message)

    def video_info(self, info):
        self.m._video(self.job, {
            "duration": _r(info.duration), "width": info.width, "height": info.height,
            "fps": _r(info.fps, 2), "video_codec": info.video_codec, "pix_fmt": info.pix_fmt,
            "format": info.format_name, "has_audio": info.has_audio, "audio_codec": info.audio_codec,
            "audio_sample_rate": info.audio_sample_rate, "audio_channels": info.audio_channels,
        })

    def cost_estimate(self, estimate):
        self.m._estimate(self.job, estimate)

    def segments_ready(self, segments):
        self.job.segments = segments
        rows = [{
            "i": i, "index": s.index, "start": _r(s.start), "end": _r(s.end), "slot_end": _r(s.slot_end),
            "slot": _r(s.slot_duration), "natural": _r(s.natural_duration), "source_text": s.source_text,
            "translated_text": "", "tts_duration": None, "final_duration": None, "stretch_factor": None,
            "timing_mode": None, "steps": [], "warnings": [], "attempts": None, "has_audio": False,
            "status": "waiting",
        } for i, s in enumerate(segments)]
        self.m._segments(self.job, rows)

    def segment_update(self, index, **fields):
        patch = {}
        if "translated_text" in fields:
            patch.update(translated_text=fields["translated_text"], status="translated")
        if "tts" in fields:
            take = fields["tts"]
            patch.update(tts_duration=_r(take[1]) if take else 0.0, status="voiced")
        if "result" in fields:
            r = fields["result"]
            self.job.results[index] = r
            patch.update(translated_text=r.translated_text, tts_duration=r.tts_duration,
                         final_duration=r.final_duration, stretch_factor=r.stretch_factor,
                         timing_mode=r.timing_mode, steps=list(r.steps), warnings=list(r.warnings),
                         attempts=r.attempts, has_audio=bool(r.audio_path), status="done")
        self.m._segment_patch(self.job, index, patch)

    def review(self, segments, translations):
        job = self.job
        job.review_text = list(translations)
        job.review_event.clear()
        self.m._set_status(job, "review")
        log.info("Script ready: review it, then click Continue.")
        while not job.review_event.wait(0.3):
            if job.cancel_event.is_set():
                raise JobCancelled()
        self.m._set_status(job, "running")
        return job.review_text


class Job:
    def __init__(self, manager, url, language, review):
        self.manager = manager
        self.id = uuid.uuid4().hex[:12]
        self.url, self.language, self.review = url, language, review
        self.cancel_event = threading.Event()
        self.review_event = threading.Event()
        self.review_text = None
        self.work_dir = Path(tempfile.mkdtemp(prefix=WORK_PREFIX))
        self.segments = []
        self.results = {}
        self.result = None
        self.tracker = None
        self.thread = None
        self.state = {
            "id": self.id, "url": url, "language": language, "review": review,
            "status": "running", "error": "", "started": time.time(), "ended": None,
            "progress": 0.0, "message": "Starting...",
            "stages": [{"key": k, "label": label, "status": "waiting", "started": None,
                        "ended": None, "duration": None, "message": ""} for k, label in STAGES],
            "video": None, "estimate": None, "segments": [], "logs": [], "result": None,
        }

    @property
    def running(self):
        return self.state["status"] in ("running", "review", "cancelling")

    def cleanup(self):
        shutil.rmtree(self.work_dir, ignore_errors=True)


class JobManager:
    def __init__(self, fake=None, fake_delay=None):
        self.fake = fake_mode() if fake is None else fake
        self.fake_delay = float(os.environ.get("DUBBING_FAKE_DELAY", "0") or 0) if fake_delay is None else fake_delay
        self.lock = threading.RLock()
        self.subscribers = []
        self.job = None
        self.last_job_id = None
        self.ledger = UsageLedger(usage_path())
        self.last_client_seen = time.time()

    # ------------------------------------------------------------ events
    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            self.subscribers.append(q)
            self.last_client_seen = time.time()
            return q, self.snapshot()

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)
            self.last_client_seen = time.time()

    def client_count(self):
        with self.lock:
            return len(self.subscribers)

    def broadcast(self, kind, data):
        """Queue an event for every browser. Serialized now, under the lock (no races)."""
        with self.lock:
            payload = json.dumps(data, ensure_ascii=False, default=str)
            for q in self.subscribers:
                q.put((kind, payload))

    def spend(self):
        data = self.ledger.summary(self.job.id if self.job else self.last_job_id)
        tracker = self.job.tracker if self.job else None
        # API calls, cache hits and money saved by the cache for the current job
        data["savings"] = tracker.savings() if tracker else None
        return data

    def snapshot(self):
        with self.lock:
            snap = {"job": self.job.state if self.job else None, "spend": self.spend(),
                    "fake": self.fake, "server_time": time.time()}
            return json.loads(json.dumps(snap, default=str))

    # ------------------------------------------- state changes (job thread)
    def _stage(self, job, key, status, message=""):
        now = time.time()
        with self.lock:
            st = next(s for s in job.state["stages"] if s["key"] == key)
            st["status"] = status
            if status == "running":
                st["started"], st["ended"], st["duration"] = now, None, None
                st["message"] = message
            else:
                st["ended"] = now
                st["duration"] = round(now - (st["started"] or now), 2)
                if message:
                    st["message"] = message
            self.broadcast("stage", st)

    def _progress(self, job, pct, message):
        with self.lock:
            job.state["progress"], job.state["message"] = round(pct, 1), message
            self.broadcast("progress", {"progress": job.state["progress"], "message": message})

    def _log(self, job, line, level="info"):
        with self.lock:
            entry = {"t": time.time(), "level": level, "line": line}
            logs = job.state["logs"]
            logs.append(entry)
            if len(logs) > MAX_LOG_LINES:
                del logs[: len(logs) - MAX_LOG_LINES]
            self.broadcast("log", entry)

    def _video(self, job, info):
        with self.lock:
            job.state["video"] = info
            self.broadcast("video", info)

    def _estimate(self, job, estimate):
        with self.lock:
            job.state["estimate"] = estimate
            self.broadcast("estimate", estimate)

    def _segments(self, job, rows):
        with self.lock:
            job.state["segments"] = rows
            self.broadcast("segments", {"segments": rows})

    def _segment_patch(self, job, i, patch):
        with self.lock:
            row = job.state["segments"][i]
            row.update(patch)
            self.broadcast("segment", row)

    def _set_status(self, job, status, error="", result=None):
        with self.lock:
            job.state["status"] = status
            job.state["error"] = error
            if result is not None:
                job.state["result"] = result
            if status in ("done", "failed", "cancelled"):
                job.state["ended"] = time.time()
            self.broadcast("status", {"status": status, "error": error, "result": job.state["result"],
                                      "ended": job.state["ended"]})

    def _on_spend(self, entry):
        self.broadcast("spend", self.spend())

    # ------------------------------------------------------------ actions
    def start(self, url, language, review=False):
        with self.lock:
            if self.job and self.job.running:
                raise JobError("A video is already being processed.")
        url = validate_youtube_url(url)
        get_language(language)
        settings = load_settings()
        apply_settings(settings)
        keys = resolve_keys(settings)

        with self.lock:
            if self.job:                       # previous job: delete its temporary files
                self.job.cleanup()
            job = Job(self, url, language, bool(review))
            tracker = CostTracker(self.ledger, job.id, pricing=config.PRICING, on_record=self._on_spend,
                                  budget=config.MAX_SPEND_PER_VIDEO)
            job.tracker = tracker
            if self.fake:
                from .fake_engine import FakeEngine, fake_download
                engine, download_fn = FakeEngine(tracker, self.fake_delay), fake_download
            else:
                from .engine import OpenAIEngine
                engine = OpenAIEngine(keys=keys, tracker=tracker)   # raises MissingApiKey
                download_fn = None
            self.job = job
            self.last_job_id = job.id
            self.broadcast("job", job.state)
            self.broadcast("spend", self.spend())
        job.thread = threading.Thread(target=self._run, args=(job, engine, download_fn),
                                      name=f"job-{job.id}", daemon=True)
        job.thread.start()
        return job

    def _run(self, job, engine, download_fn):
        try:
            log_file = setup_logging(_LogForwarder(job))
            log.info("Log file: %s", log_file)
            if self.fake:
                log.info("OFFLINE TEST MODE: fake AI engine and a generated test video (no API calls).")
            result = run_pipeline(
                job.url, job.language,
                on_progress=lambda p, m: self._progress(job, p, m),
                engine=engine, hooks=_JobHooks(job), cancel_event=job.cancel_event,
                review=job.review, work_dir=job.work_dir, download_fn=download_fn)
            job.result = result
            for c in result.checks:
                log.info("  OK  %s", c)
            for w in result.warnings:
                log.warning("  WARNING  %s", w)
            log.info("FINAL VIDEO READY: %s", result.video_path)
            self._set_status(job, "done", result={
                "video_path": str(result.video_path), "srt_path": str(result.srt_path),
                "json_path": str(result.json_path), "output_dir": str(result.output_dir),
                "video_name": result.video_path.name, "srt_name": result.srt_path.name,
                "json_name": result.json_path.name, "title": result.title,
                "checks": result.checks, "warnings": result.warnings, "cost": result.cost,
                "usage": result.usage,
            })
            self._progress(job, 100.0, "Done.")
        except Exception as exc:  # noqa: BLE001
            if job.cancel_event.is_set():
                log.info("Job cancelled.")
                self._set_status(job, "cancelled", "The job was cancelled.")
            else:
                log.debug("Job failed:\n%s", traceback.format_exc())
                msg = friendly_error(exc)
                log.error("ERROR: %s", msg)
                self._set_status(job, "failed", msg)
        finally:
            self.broadcast("spend", self.spend())

    def cancel(self):
        with self.lock:
            job = self.job
            if not job or not job.running:
                raise JobError("No video is being processed.")
            job.cancel_event.set()
            job.state["status"] = "cancelling"
            self.broadcast("status", {"status": "cancelling", "error": "", "result": None, "ended": None})

    def submit_review(self, edits):
        """edits: {position (0-based) : new text}."""
        with self.lock:
            job = self.job
            if not job or job.state["status"] != "review":
                raise JobError("The script is not waiting for a review.")
            texts = list(job.review_text)
            for key, text in (edits or {}).items():
                i = int(key)
                if 0 <= i < len(texts) and isinstance(text, str):
                    texts[i] = text
            job.review_text = texts
            job.review_event.set()

    def close_job(self):
        with self.lock:
            job = self.job
            if job and job.running:
                raise JobError("Cancel the running job first.")
            if job:
                job.cleanup()
            self.job = None
            self.broadcast("job", None)

    def shutdown(self):
        with self.lock:
            job = self.job
        if job and job.running:
            job.cancel_event.set()
            job.review_event.set()
            if job.thread:
                job.thread.join(timeout=20)
        if job:
            job.cleanup()
        with self.lock:
            for q in self.subscribers:
                q.put(None)

    # ------------------------------------------------------------ files
    def current_job(self):
        with self.lock:
            if not self.job:
                raise JobError("No job.")
            return self.job

    def output_file(self, kind):
        job = self.current_job()
        if not job.result:
            raise JobError("The video is not ready yet.")
        path = {"mp4": job.result.video_path, "srt": job.result.srt_path, "json": job.result.json_path}.get(kind)
        if path is None:
            raise JobError("Unknown file.")
        return Path(path)

    def segment_audio(self, i):
        job = self.current_job()
        r = job.results.get(i)
        if not r or not r.audio_path:
            raise JobError("No voice clip for this segment yet.")
        path = Path(r.audio_path).resolve()
        if job.work_dir.resolve() not in path.parents or not path.exists():
            raise JobError("The clip is no longer available.")
        return path

    def source_audio_clip(self, i):
        """Original speech of a segment as WAV bytes (cut from the extracted audio)."""
        import io
        import wave
        job = self.current_job()
        if not (0 <= i < len(job.segments)):
            raise JobError("Unknown segment.")
        src = job.work_dir / "source_audio.wav"
        if not src.exists():
            raise JobError("The original audio is not available.")
        seg = job.segments[i]
        with wave.open(str(src), "rb") as w:
            rate = w.getframerate()
            w.setpos(min(w.getnframes(), int(seg.start * rate)))
            frames = w.readframes(int((seg.end - seg.start) * rate))
            params = w.getparams()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as out:
            out.setparams(params)
            out.writeframes(frames)
        return buf.getvalue()
