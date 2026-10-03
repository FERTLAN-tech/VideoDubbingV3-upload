"""
The complete workflow:

URL -> download -> probe -> extract audio -> transcribe -> slots -> translate
    -> [optional review] -> TTS -> bidirectional timing per segment -> assemble
    -> mux MP4 -> SRT + timing JSON -> validate

An interface can follow every step through a PipelineHooks object and stop the
job with a threading.Event (`cancel_event`). Both are optional.
"""
import logging
import re
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import config
from .cache import default_cache
from .costs import BudgetExceeded, estimate_job_cost
from .downloader import download_video, validate_youtube_url, youtube_video_id
from .economy import DurationPredictor, predicted_misfit, wrap_engine
from .ffmpeg_utils import extract_audio, find_tool, mux_video, probe_video
from .languages import get_language
from .outputs import assemble_audio, create_srt, create_timing_json
from .script import pre_tts_rewrites, translate_all
from .segments import calculate_segment_slots, clean_segments, is_speech_text
from .timing import fit_audio_bidirectionally, initial_tts
from .validate import validate_output

log = logging.getLogger("dubbing")


class PipelineError(RuntimeError):
    pass


class JobCancelled(RuntimeError):
    def __init__(self, message="The job was cancelled."):
        super().__init__(message)


@dataclass
class JobResult:
    video_path: Path
    srt_path: Path
    json_path: Path
    output_dir: Path
    title: str
    checks: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    cost: dict | None = None
    usage: dict | None = None      # API calls / cache hits / money saved


class PipelineHooks:
    """Override any of these to follow a job (all are optional no-ops)."""

    def stage(self, stage, status, message=""):
        """status: running / done / failed."""

    def video_info(self, info):
        """ffprobe analysis of the source video (ffmpeg_utils.VideoInfo)."""

    def cost_estimate(self, estimate):
        """Estimated cost of this job (costs.estimate_job_cost), known once the video is probed."""

    def segments_ready(self, segments):
        """Speech segments with their slots, before translation."""

    def segment_update(self, index, **fields):
        """index = position in the segment list. fields: translated_text / tts=(wav, s) / result."""

    def review(self, segments, translations):
        """Called after translation when review=True. Return the (edited) translations."""
        return translations


class Progress:
    """Maps each stage to a share of the progress bar, forwards messages and tracks stages."""

    STAGES = {  # stage: (start %, end %)
        "download": (0, 12), "probe": (12, 13), "extract": (13, 15), "transcribe": (15, 30),
        "translate": (30, 42), "voice": (42, 60), "timing": (60, 88), "assemble": (88, 90),
        "export": (90, 96), "validate": (96, 100),
    }

    def __init__(self, callback=None, hooks=None, cancel_event=None):
        self.callback = callback
        self.hooks = hooks or PipelineHooks()
        self.cancel_event = cancel_event
        self.lock = threading.Lock()
        self.current = None

    def check_cancel(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise JobCancelled()

    def __call__(self, stage, message, fraction=0.0):
        self.check_cancel()
        a, b = self.STAGES[stage]
        pct = a + (b - a) * max(0.0, min(1.0, fraction))
        log.info("%s", message) if fraction == 0 else log.debug("%s", message)
        with self.lock:
            if stage != self.current:
                if self.current:
                    self.hooks.stage(self.current, "done")
                self.current = stage
                self.hooks.stage(stage, "running", message)
            if self.callback:
                self.callback(pct, message)

    def finish(self):
        with self.lock:
            if self.current:
                self.hooks.stage(self.current, "done")
            self.current = None

    def fail(self, message=""):
        with self.lock:
            if self.current:
                self.hooks.stage(self.current, "failed", message)
            self.current = None


def safe_filename(name, max_len=80):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name or "")
    name = " ".join(name.split()).strip(" .")
    name = name[:max_len].rstrip(" .")
    if not name or name.upper() in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?i)(COM|LPT)\d", name):
        name = "video"
    return name


def transcribe_audio(engine, wav, work_dir, duration):
    raw = engine.transcribe(wav, work_dir, duration)
    segments = clean_segments(raw, duration)
    if not segments:
        raise PipelineError("No speech was detected in this video.")
    return segments


def _process_segments(items, worker, progress, stage, label, on_done=None, done_before=0, total=None):
    """Run worker(item) in parallel; a failed segment is retried, then fails the job.
    on_done(i, result) is called (in this thread) as each item finishes.
    done_before / total: progress shown when one stage runs in several passes."""
    results = [None] * len(items)
    done = done_before
    total = total or len(items)
    check_cancel = getattr(progress, "check_cancel", lambda: None)

    def guarded(i):
        last = None
        for attempt in range(1, config.SEGMENT_RETRIES + 1):
            check_cancel()
            try:
                return worker(items[i])
            except (JobCancelled, BudgetExceeded):
                raise
            except Exception as exc:  # noqa: BLE001
                last = exc
                log.warning("%s: segment %d failed (attempt %d/%d): %s",
                            label, i + 1, attempt, config.SEGMENT_RETRIES, exc)
                time.sleep(1)
        raise PipelineError(f"{label} failed for segment {i + 1}: {last}") from last

    with ThreadPoolExecutor(max_workers=max(1, config.MAX_WORKERS)) as pool:
        futures = {pool.submit(guarded, i): i for i in range(len(items))}
        try:
            for fut in as_completed(futures):
                results[futures[fut]] = fut.result()
                if on_done:
                    on_done(futures[fut], results[futures[fut]])
                done += 1
                progress(stage, f"{label}... ({done}/{total})", done / max(1, total))
        except Exception:
            for f in futures:
                f.cancel()
            raise
    return results


def dub_video_file(video_path, title, language_name, engine, work_dir, on_progress=None,
                   source_url="", segments=None, hooks=None, cancel_event=None, review=False, cache=None):
    """Dub a local video file. `segments` can be supplied to skip transcription (tests).
    cache: a cache.Cache, None = the default persistent cache (if enabled in Settings),
    False = no persistent cache (identical lines are still voiced only once)."""
    progress = (on_progress if isinstance(on_progress, Progress)
                else Progress(on_progress, hooks, cancel_event))
    try:
        if cache is None:
            cache = default_cache()
        engine = wrap_engine(engine, cache or None)
        result = _dub(video_path, title, language_name, engine, Path(work_dir), progress,
                      source_url, segments, review)
    except Exception as exc:
        progress.fail(friendly_error(exc))
        raise
    progress.finish()
    return result


def _voice_all(engine, segments, language, translations, clips_dir, progress, hooks):
    """
    First voice take of every line. Lines predicted not to fit (from their length in
    characters) are rewritten first, in one batch, so no take is paid for and thrown
    away. The lines that look fine are voiced first: their real durations calibrate
    the prediction for the others.
    Returns (texts, takes, pre) with pre = {i: (steps already applied, rewrites used)}.
    """
    n = len(segments)
    texts, takes, pre = list(translations), [None] * n, {}
    predictor = DurationPredictor(language)
    speak = [i for i in range(n) if is_speech_text(texts[i])]
    for i in range(n):
        if i not in speak:
            hooks.segment_update(i, tts=None)
    predict = config.PREDICT_BEFORE_TTS and config.REWRITE_TRIES > 0
    later = [i for i in speak if predict and predicted_misfit(segments[i], texts[i], predictor)]
    first = [i for i in speak if i not in later]

    def voice(i):
        take = initial_tts(engine, segments[i], language, texts[i], clips_dir)
        if take:
            predictor.observe(texts[i], take[1])
        return take

    def store(i, take):
        takes[i] = take
        hooks.segment_update(i, tts=take)

    _process_segments(first, voice, progress, "voice", "Generating voice",
                      on_done=lambda k, take: store(first[k], take), total=len(speak))
    if later:
        progress("voice", "Rewriting lines that will not fit, before voicing them...", len(first) / len(speak))
        for i, (text, kind) in pre_tts_rewrites(engine, segments, language, texts, later, predictor).items():
            texts[i] = text
            pre[i] = (["concise_rewrite" if kind == "long" else "expanded_rewrite"], 1)
        _process_segments(later, voice, progress, "voice", "Generating voice",
                          on_done=lambda k, take: store(later[k], take), done_before=len(first),
                          total=len(speak))
    return texts, takes, pre


def _dub(video_path, title, language_name, engine, work_dir, progress, source_url, segments, review):
    hooks = progress.hooks
    language = get_language(language_name)
    clips_dir = work_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    tracker = getattr(engine, "tracker", None)

    progress("probe", "Analyzing video...")
    info = probe_video(video_path)
    log.info("Video: %s", info.summary())
    hooks.video_info(info)
    try:
        estimate = estimate_job_cost(info.duration, tracker.pricing if tracker else None)
        log.info("Estimated cost of this video: about $%.2f (before cache savings)", estimate["total"])
        if tracker is not None and tracker.budget is not None:
            log.info("Spending limit for this video: $%.2f", tracker.budget)
        hooks.cost_estimate(estimate)
    except Exception:  # noqa: BLE001 - an estimate must never break a job
        log.debug("cost estimate failed", exc_info=True)

    if segments is None:
        progress("extract", "Extracting audio...")
        wav = extract_audio(video_path, work_dir / "source_audio.wav")
        progress("transcribe", "Transcribing...")
        vid = youtube_video_id(source_url)
        engine.source_id = f"youtube:{vid}" if vid else None      # else: hash of the audio
        segments = transcribe_audio(engine, wav, work_dir, info.duration)
    calculate_segment_slots(segments, info.duration)
    log.info("%d speech segments", len(segments))
    hooks.segments_ready(segments)
    indexes = list(range(len(segments)))

    # ---- translation (in batches: the whole batch is the context) -----------
    progress("translate", "Translating...")
    translations = translate_all(engine, segments, language, progress, hooks, _process_segments)

    if review:
        progress("translate", "Waiting for your review of the script...", 1.0)
        edited = hooks.review(segments, list(translations))
        progress.check_cancel()
        if edited is not None:
            if len(edited) != len(translations):
                raise PipelineError("The reviewed script does not match the segments.")
            translations = [" ".join((t or "").split()) for t in edited]
            for i, t in enumerate(translations):
                hooks.segment_update(i, translated_text=t)

    # ---- TTS: first voice take of every segment -----------------------------
    progress("voice", "Generating voice...")
    texts, takes, pre = _voice_all(engine, segments, language, translations, clips_dir, progress, hooks)

    # ---- bidirectional timing ------------------------------------------------
    progress("timing", "Adjusting timing...")

    def fit(i):
        steps, used = pre.get(i, ([], 0))
        return fit_audio_bidirectionally(engine, segments[i], language, texts[i], clips_dir,
                                         initial=takes[i], pre_steps=steps, rewrites_used=used)

    results = _process_segments(indexes, fit, progress, "timing", "Adjusting timing",
                                on_done=lambda i, r: hooks.segment_update(i, result=r))

    progress("assemble", "Building final audio...")
    dub_wav = assemble_audio(results, info.duration, work_dir / "dub_track.wav")

    # ---- output files -------------------------------------------------------
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    base = f"{safe_filename(title)} [{language['code'].upper()}]"
    out_dir = config.OUTPUT_DIR / base
    if out_dir.exists():
        out_dir = config.OUTPUT_DIR / f"{base} {datetime.now():%Y-%m-%d %H-%M-%S}"
    out_dir.mkdir(parents=True)
    mp4 = out_dir / f"{base}.mp4"
    srt = out_dir / f"{base}.srt"
    report = out_dir / f"{base} - timing.json"

    progress("export", "Exporting MP4...")
    mux_video(info, dub_wav, mp4)
    create_srt(results, srt)
    cost = tracker.job_summary() if tracker else None
    usage = engine.usage() if hasattr(engine, "usage") else None
    if usage:
        calls, hits = sum(usage["api_calls"].values()), sum(usage["cache_hits"].values())
        log.info("API calls: %d (%s); reused from cache: %d%s", calls,
                 ", ".join(f"{k} {v}" for k, v in usage["api_calls"].items()), hits,
                 f" (saved about ${usage['saved_usd']:.3f})" if usage.get("saved_usd") else "")
    create_timing_json(results, report, {
        "title": title, "source_url": source_url, "target_language": language["name"],
        "created": datetime.now().isoformat(timespec="seconds"),
        "video": {"duration": round(info.duration, 3), "width": info.width, "height": info.height,
                  "fps": round(info.fps, 3), "video_codec": info.video_codec, "format": info.format_name},
        "cost_estimate_usd": cost,
        "api_usage": usage,
    })

    progress("validate", "Checking final video...")
    checks = validate_output(mp4, srt, report, results, info.duration)
    warnings = [f"Segment {r.segment.index}: {w}" for r in results for w in r.warnings]
    progress("validate", "Done.", 1.0)
    return JobResult(mp4, srt, report, out_dir, title, checks, warnings, cost, usage)


def run_pipeline(url, language_name, on_progress=None, engine=None, hooks=None, cancel_event=None,
                 review=False, work_dir=None, download_fn=None, tracker=None, cache=None):
    """
    Full automatic workflow from a YouTube URL.

    work_dir     if given, the caller owns it (it is NOT deleted here, so an interface can
                 still play the clips); otherwise a temporary folder is created and removed.
    download_fn  replaces download_video(url, work_dir, on_fraction) -> (path, title) (tests).
    tracker      costs.CostTracker attached to the default OpenAI engine.
    cache        see dub_video_file (None = persistent cache if enabled, False = off).
    """
    url = validate_youtube_url(url)
    get_language(language_name)
    find_tool("ffmpeg")
    find_tool("ffprobe")
    if engine is None:
        from .engine import OpenAIEngine
        from .settings import load_settings, resolve_keys
        engine = OpenAIEngine(keys=resolve_keys(load_settings()), tracker=tracker)

    progress = Progress(on_progress, hooks, cancel_event)
    owns_dir = work_dir is None
    work_dir = Path(tempfile.mkdtemp(prefix="video_dubbing_v3_")) if owns_dir else Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    log.info("Working folder: %s", work_dir)
    try:
        try:
            progress("download", "Downloading video...")
            video, title = (download_fn or download_video)(
                url, work_dir, lambda f: progress("download", "Downloading video...", f))
            progress.check_cancel()
        except Exception as exc:
            progress.fail(friendly_error(exc))
            raise
        return dub_video_file(video, title, language_name, engine, work_dir, progress, source_url=url,
                              review=review, cache=cache)
    finally:
        if config.KEEP_TEMP_FILES:
            log.info("Temporary files kept in %s", work_dir)
        elif owns_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


def friendly_error(exc):
    """Turn technical exceptions into a message a non-technical user can act on."""
    if isinstance(exc, JobCancelled):
        return "The job was cancelled."
    if isinstance(exc, BudgetExceeded):
        return str(exc)
    try:
        import openai
        if isinstance(exc, openai.AuthenticationError):
            return "Your OpenAI API key was rejected. Check the key in Settings and try again."
        if isinstance(exc, openai.RateLimitError):
            if "insufficient_quota" in str(exc):
                return "Your OpenAI account has no credit left. Add credit at platform.openai.com/settings/billing."
            return "OpenAI is rate-limiting requests. Wait a minute and try again."
        if isinstance(exc, openai.APIConnectionError):
            return "Could not reach OpenAI. Check your internet connection."
        if isinstance(exc, openai.NotFoundError):
            return f"An AI model chosen in Settings is not available on your account:\n{exc}"
    except ImportError:
        pass
    return str(exc) or exc.__class__.__name__
