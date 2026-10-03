"""
Spending less on the API without changing the result.

CachedEngine wraps any engine (OpenAI or the offline fake) and adds, for every
paid call:

* the persistent cache (cache.py): a result already paid for is reused, $0;
* de-duplication inside a job: identical lines are voiced once (even with the
  cache turned off), and two threads never pay twice for the same thing;
* the budget cap: no new call once the job went over "Max spend per video";
* counters: API calls made and cache hits per kind of work, + money saved.

DurationPredictor estimates how long a line will last when spoken, from its
length in characters, so lines that cannot fit are rewritten BEFORE paying for
a voice take that would be thrown away.
"""
import contextlib
import logging
import shutil
import statistics
import threading
from collections import Counter
from pathlib import Path

from . import config
from .cache import file_hash, make_key
from .costs import merge_units
from .engine import REWRITE_PROMPT_VERSION, TRANSLATE_PROMPT_VERSION, rewrite_model

log = logging.getLogger("dubbing")

KINDS = ("transcribe", "translate", "rewrite", "tts")
_ROLE = {"transcribe": "analysis", "translate": "script", "rewrite": "script", "tts": "voice"}


def _r(x, step=0.1):
    return round(round(float(x) / step) * step, 3)


class CachedEngine:
    def __init__(self, inner, cache=None):
        self.inner = inner
        self.cache = cache
        self.ns = getattr(inner, "cache_namespace", type(inner).__name__)
        self.source_id = None          # YouTube id (set by the pipeline); else the audio is hashed
        self.calls = Counter()         # API calls actually sent, per kind
        self.hits = Counter()          # results reused (cache or duplicate), per kind
        self._memo = {}                # key -> (value, meta): this job's results
        self._locks = {}
        self._locks_guard = threading.Lock()

    # the pipeline reads engine.tracker; anything else goes to the real engine
    @property
    def tracker(self):
        return getattr(self.inner, "tracker", None)

    def __getattr__(self, name):
        if name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)

    @property
    def can_batch_translate(self):
        return callable(getattr(self.inner, "translate_batch", None))

    @property
    def can_batch_rewrite(self):
        return callable(getattr(self.inner, "rewrite_batch", None))

    def usage(self):
        out = {"api_calls": {k: self.calls[k] for k in KINDS},
               "cache_hits": {k: self.hits[k] for k in KINDS},
               "cache_enabled": self.cache is not None}
        tracker = self.tracker
        if tracker is not None:
            s = tracker.savings()
            out["saved_usd"] = s["saved_usd"]
            out["billed_calls"] = s["api_calls"]
        return out

    # ------------------------------------------------------------ plumbing
    def _lock(self, key):
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _count(self, counter, kind):
        with self._locks_guard:
            counter[kind] += 1

    def _before_call(self):
        tracker = self.tracker
        if tracker is not None:
            tracker.check_budget()

    @contextlib.contextmanager
    def _capture(self):
        tracker = self.tracker
        if tracker is None:
            yield []
        else:
            with tracker.capture() as captured:
                yield captured

    def _hit(self, kind, meta):
        self._count(self.hits, kind)
        tracker = self.tracker
        if tracker is not None:
            meta = meta or {}
            tracker.record_hit(_ROLE[kind], meta.get("model"), meta.get("units"), kind)

    def _lookup(self, kind, key):
        """(value, meta) from this job or the disk cache, or None."""
        if key in self._memo:
            return self._memo[key]
        if self.cache is not None:
            entry = self.cache.get(kind, key)
            if entry is not None:
                self._memo[key] = (entry["value"], entry.get("meta") or {})
                return self._memo[key]
        return None

    def _store(self, kind, key, value, meta):
        self._memo[key] = (value, meta)
        if self.cache is not None:
            self.cache.put(kind, key, value, meta)

    def _cached(self, kind, key, compute):
        with self._lock(key):
            found = self._lookup(kind, key)
            if found is not None:
                self._hit(kind, found[1])
                return found[0]
            self._before_call()
            with self._capture() as captured:
                value = compute()
            self._count(self.calls, kind)
            model, units = merge_units(captured)
            self._store(kind, key, value, {"model": model, "units": units})
            return value

    # ------------------------------------------------------------ transcription
    def transcribe(self, wav, work_dir, duration):
        source = self.source_id or ("sha256:" + file_hash(wav))
        key = make_key("transcribe", self.ns, config.TRANSCRIBE_MODEL, source, _r(duration),
                       config.TRANSCRIBE_CHUNK_SECONDS, config.NO_SPEECH_PROB_LIMIT,
                       config.NO_SPEECH_LOGPROB_LIMIT)
        rows = self._cached("transcribe", key, lambda: [
            [float(s), float(e), str(t)] for s, e, t in self.inner.transcribe(wav, work_dir, duration)])
        return [tuple(r) for r in rows]

    # ------------------------------------------------------------ translation
    def translation_key(self, item, language):
        return make_key("translate", self.ns, TRANSLATE_PROMPT_VERSION, config.TEXT_MODEL, language["name"],
                        item["text"], item.get("prev", ""), item.get("next", ""), _r(item["seconds"]))

    def translate_segment(self, segment, language, prev_text="", next_text=""):
        item = {"text": segment.source_text, "prev": prev_text, "next": next_text,
                "seconds": segment.natural_duration}
        return self._cached("translate", self.translation_key(item, language),
                            lambda: self.inner.translate_segment(segment, language, prev_text, next_text))

    def lookup_translations(self, items, language):
        """Translations already paid for: {i: text} (recorded as cache hits)."""
        return self._lookup_many("translate", items, lambda it: self.translation_key(it, language))

    def translate_batch(self, items, language):
        return self._batch("translate", items, lambda it: self.translation_key(it, language),
                           lambda: self.inner.translate_batch(items, language))

    # ------------------------------------------------------------ rewrites
    def rewrite_key(self, item, language):
        return make_key("rewrite", self.ns, REWRITE_PROMPT_VERSION, rewrite_model(), language["name"],
                        item["mode"], item["source"], item["text"], _r(item["target_s"]),
                        _r(item["current_s"]), item.get("attempt", 1))

    def _rewrite_one(self, mode, method, segment, text, language, target_s, current_s, attempt):
        item = {"mode": mode, "source": segment.source_text, "text": text, "target_s": target_s,
                "current_s": current_s, "attempt": attempt}
        return self._cached("rewrite", self.rewrite_key(item, language),
                            lambda: getattr(self.inner, method)(segment, text, language, target_s,
                                                                current_s, attempt))

    def rewrite_concise(self, segment, text, language, target_s, current_s, attempt):
        return self._rewrite_one("shorter", "rewrite_concise", segment, text, language, target_s, current_s,
                                 attempt)

    def rewrite_expanded(self, segment, text, language, target_s, current_s, attempt):
        return self._rewrite_one("longer", "rewrite_expanded", segment, text, language, target_s, current_s,
                                 attempt)

    def lookup_rewrites(self, items, language):
        return self._lookup_many("rewrite", items, lambda it: self.rewrite_key(it, language))

    def rewrite_batch(self, items, language):
        clean = [{k: v for k, v in it.items() if k != "segment"} for it in items]
        return self._batch("rewrite", items, lambda it: self.rewrite_key(it, language),
                           lambda: self.inner.rewrite_batch(clean, language))

    # ------------------------------------------------------------ batch helpers
    def _lookup_many(self, kind, items, key_of):
        found = {}
        for it in items:
            hit = self._lookup(kind, key_of(it))
            if hit is not None and hit[0]:
                self._hit(kind, hit[1])
                found[it["i"]] = hit[0]
        return found

    def _batch(self, kind, items, key_of, call):
        """One API call for many items; each valid answer is cached on its own,
        with its share of the call's cost (by text length)."""
        self._before_call()
        with self._capture() as captured:
            got = call() or {}
        self._count(self.calls, kind)
        model, units = merge_units(captured)
        wanted = {it["i"]: it for it in items}
        answered = {i: t for i, t in got.items() if i in wanted and isinstance(t, str) and t.strip()}
        total = sum(len(wanted[i]["text"]) for i in answered) or 1
        for i, text in answered.items():
            share = len(wanted[i]["text"]) / total
            self._store(kind, key_of(wanted[i]), text,
                        {"model": model, "units": {k: v * share for k, v in units.items()}})
        return answered

    # ------------------------------------------------------------ voice
    def tts_key(self, text, language):
        model = config.TTS_MODEL
        style = "" if model.startswith("tts-1") else language.get("tts_instructions", "")
        return make_key("tts", self.ns, model, language["voice"], style, text)

    def generate_tts(self, text, language, out_wav):
        out_wav = Path(out_wav)
        key = self.tts_key(text, language)
        with self._lock(key):
            if key in self._memo:                       # same line already voiced in this job
                src, meta = self._memo[key]
                if Path(src).exists():
                    shutil.copyfile(src, out_wav)
                    self._hit("tts", meta)
                    return out_wav
            if self.cache is not None:
                meta = self.cache.get_file("tts", key, out_wav)
                if meta is not None:
                    self._memo[key] = (str(out_wav), meta)
                    self._hit("tts", meta)
                    return out_wav
            self._before_call()
            with self._capture() as captured:
                self.inner.generate_tts(text, language, out_wav)
            self._count(self.calls, "tts")
            model, units = merge_units(captured)
            meta = {"model": model, "units": units}
            self._memo[key] = (str(out_wav), meta)
            if self.cache is not None:
                self.cache.put_file("tts", key, out_wav, meta)
            return out_wav


def wrap_engine(engine, cache=None):
    """CachedEngine around `engine` (an engine that is already wrapped is returned as is)."""
    if isinstance(engine, CachedEngine):
        return engine
    return CachedEngine(engine, cache)


# --------------------------------------------------------------------------
# Spoken-length prediction
# --------------------------------------------------------------------------
class DurationPredictor:
    """Seconds a line will last when spoken = characters / chars_per_second x a
    correction learnt from the voice takes measured so far in this job (median)."""

    def __init__(self, language):
        self.cps = float(language.get("chars_per_second") or 14.5)
        self.ratios = []
        self.lock = threading.Lock()

    def raw(self, text):
        return len(" ".join((text or "").split())) / self.cps

    @property
    def calibrated(self):
        return len(self.ratios) >= 2

    def ratio(self):
        with self.lock:
            return statistics.median(self.ratios) if self.ratios else 1.0

    def predict(self, text):
        return self.raw(text) * self.ratio()

    def observe(self, text, seconds):
        raw = self.raw(text)
        if raw >= 0.4 and seconds and seconds > 0.1:
            with self.lock:
                self.ratios.append(seconds / raw)


def predicted_misfit(segment, text, predictor):
    """'long' if the line will clearly not fit even at MAX_SPEED_UP, 'short' if it will
    clearly be too short even at MAX_SLOW_DOWN, else None. Uncalibrated predictions
    get a 10% safety margin."""
    if not text or not text.strip():
        return None
    p = predictor.predict(text)
    margin = 1.0 if predictor.calibrated else 1.1
    if p > segment.slot_duration * config.MAX_SPEED_UP * margin:
        return "long"
    if p < segment.natural_duration * config.SHORT_THRESHOLD * config.MAX_SLOW_DOWN / margin:
        return "short"
    return None
