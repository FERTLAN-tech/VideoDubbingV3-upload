"""
Spend tracking: every OpenAI call is recorded with its usage and an estimated
price, per role:

    analysis  transcription (Whisper)        units: audio_minutes
    script    translation + rewrites (GPT)   units: input_tokens, output_tokens
    voice     text-to-speech                 units: chars, input_tokens (~chars/4), audio_minutes

Prices come from config.PRICING (editable in Settings). They are estimates.
A model missing from the table is recorded with cost None ("price unknown").

The ledger is an append-only JSON-lines file (usage.jsonl) in the app data folder.
Everything here is thread-safe: segments are processed in parallel.

Cache hits (see cache.py) are counted separately: they cost nothing, are never
written to the ledger as spend, and carry an estimate of the money they saved.
"""
import contextlib
import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path

from . import config

log = logging.getLogger("dubbing")

ROLES = ("analysis", "script", "voice")

# price field -> (unit name, divisor)
_PRICE_UNITS = {
    "per_minute": ("audio_minutes", 1.0),
    "input_per_1m": ("input_tokens", 1_000_000.0),
    "output_per_1m": ("output_tokens", 1_000_000.0),
    "per_1m_chars": ("chars", 1_000_000.0),
}


def find_price(model, pricing=None):
    """Price entry for a model; dated variants (gpt-4.1-mini-2025-04-14) match their base name."""
    pricing = config.PRICING if pricing is None else pricing
    model = (model or "").strip()
    if model in pricing:
        return pricing[model]
    best = None
    for name in pricing:
        if model.startswith(name + "-") and (best is None or len(name) > len(best)):
            best = name
    return pricing[best] if best else None


def estimate_cost(model, units, pricing=None):
    """USD cost of one call, or None if the model's price is unknown."""
    price = find_price(model, pricing)
    if not price:
        return None
    total = 0.0
    for field, rate in price.items():
        if field not in _PRICE_UNITS or rate in (None, ""):
            continue
        unit, div = _PRICE_UNITS[field]
        total += float(rate) * float(units.get(unit, 0) or 0) / div
    return total


def _empty_totals():
    return {role: {"cost": 0.0, "calls": 0, "unknown": 0} for role in ROLES}


def _add(totals, entry):
    t = totals.setdefault(entry["role"], {"cost": 0.0, "calls": 0, "unknown": 0})
    t["calls"] += 1
    if entry.get("cost") is None:
        t["unknown"] += 1
    else:
        t["cost"] += entry["cost"]


def _finish(totals):
    out = {}
    for role in ROLES:
        t = totals.get(role, {"cost": 0.0, "calls": 0, "unknown": 0})
        out[role] = {"cost": round(t["cost"], 6), "calls": t["calls"], "unknown": t["unknown"]}
    out["total"] = {
        "cost": round(sum(out[r]["cost"] for r in ROLES), 6),
        "calls": sum(out[r]["calls"] for r in ROLES),
        "unknown": sum(out[r]["unknown"] for r in ROLES),
    }
    return out


class UsageLedger:
    """All recorded calls (kept in memory, appended to usage.jsonl)."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.entries = []
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue            # a half-written line must not break the app
                if isinstance(entry, dict) and entry.get("role") in ROLES:
                    self.entries.append(entry)

    def append(self, entry):
        with self.lock:
            self.entries.append(entry)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def summary(self, job_id=None, now=None):
        """Totals per role for one job, this month and all time."""
        now = now or datetime.now()
        month = now.strftime("%Y-%m")
        job, this_month, all_time = _empty_totals(), _empty_totals(), _empty_totals()
        with self.lock:
            entries = list(self.entries)
        for e in entries:
            _add(all_time, e)
            if str(e.get("ts", "")).startswith(month):
                _add(this_month, e)
            if job_id and e.get("job_id") == job_id:
                _add(job, e)
        return {"job": _finish(job), "month": _finish(this_month), "all": _finish(all_time),
                "job_id": job_id, "month_name": month}

    def reset(self):
        """Erase all counters (the ledger file is kept as a backup next to it)."""
        with self.lock:
            self.entries = []
            if self.path.exists():
                backup = self.path.with_name(f"usage-reset-{datetime.now():%Y%m%d-%H%M%S}.jsonl.bak")
                self.path.replace(backup)


class BudgetExceeded(RuntimeError):
    """The job reached the "Max spend per video" limit (stops it cleanly)."""


def merge_units(entries):
    """Sum the units of several ledger entries -> (model, units)."""
    units, model = {}, None
    for e in entries:
        model = model or e.get("model")
        for k, v in (e.get("units") or {}).items():
            if isinstance(v, (int, float)):
                units[k] = units.get(k, 0) + v
    return model, units


class CostTracker:
    """Records the calls of one job. `on_record(entry)` is called after each call
    (and after each cache hit, with entry["cache_hit"] = True)."""

    def __init__(self, ledger=None, job_id=None, pricing=None, on_record=None, budget=None):
        self.ledger = ledger
        self.job_id = job_id or uuid.uuid4().hex[:12]
        self.pricing = dict(config.PRICING if pricing is None else pricing)
        self.on_record = on_record
        self.budget = budget            # USD, None = no cap
        self.lock = threading.Lock()
        self.entries = []
        self.hits = []
        self._local = threading.local()

    @contextlib.contextmanager
    def capture(self):
        """Collect the entries recorded by this thread inside the block (used by the
        cache to remember what a result cost, so a later hit knows what it saved)."""
        previous = getattr(self._local, "capture", None)
        captured = []
        self._local.capture = captured
        try:
            yield captured
        finally:
            self._local.capture = previous
            if previous is not None:
                previous.extend(captured)

    def total_cost(self):
        with self.lock:
            return sum(e["cost"] or 0.0 for e in self.entries)

    def check_budget(self):
        """Raise BudgetExceeded when the job already spent more than its cap."""
        if self.budget is None:
            return
        spent = self.total_cost()
        if spent > self.budget:
            raise BudgetExceeded(
                f"Stopped: this video reached your spending limit of ${self.budget:.2f} "
                f"(estimated ${spent:.3f} spent). Raise \"Max spend per video\" in Settings, or leave "
                "it empty for no limit. Everything already paid for is cached and will be reused "
                "if you start this video again.")

    def record_hit(self, role, model, units=None, kind=""):
        """A result reused from the cache: $0 spent, `saved` = what the call would have cost."""
        saved = estimate_cost(model, units or {}, self.pricing) if model else None
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), "job_id": self.job_id,
                 "role": role, "model": model, "kind": kind, "saved": round(saved or 0.0, 6),
                 "cache_hit": True}
        with self.lock:
            self.hits.append(entry)
        if self.on_record:
            try:
                self.on_record(entry)
            except Exception:  # noqa: BLE001
                log.debug("on_record callback failed", exc_info=True)
        return entry

    def savings(self):
        with self.lock:
            hits = list(self.hits)
            calls = len(self.entries)
        by_kind = {}
        for h in hits:
            by_kind[h["kind"] or h["role"]] = by_kind.get(h["kind"] or h["role"], 0) + 1
        return {"api_calls": calls, "cache_hits": len(hits),
                "saved_usd": round(sum(h["saved"] for h in hits), 6), "hits_by_kind": by_kind}

    def record(self, role, model, **units):
        if role not in ROLES:
            raise ValueError(f"unknown role {role}")
        units = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in units.items() if v is not None}
        cost = estimate_cost(model, units, self.pricing)
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "job_id": self.job_id, "role": role, "model": model,
            "units": units, "cost": None if cost is None else round(cost, 6),
        }
        with self.lock:
            self.entries.append(entry)
        captured = getattr(self._local, "capture", None)
        if captured is not None:
            captured.append(entry)
        if self.ledger is not None:
            try:
                self.ledger.append(entry)
            except OSError as exc:      # spend tracking must never break a dub
                log.warning("Could not write the usage ledger: %s", exc)
        if self.on_record:
            try:
                self.on_record(entry)
            except Exception:  # noqa: BLE001
                log.debug("on_record callback failed", exc_info=True)
        return entry

    def job_summary(self):
        totals = _empty_totals()
        with self.lock:
            entries = list(self.entries)
        for e in entries:
            _add(totals, e)
        out = _finish(totals)
        out["job_id"] = self.job_id
        out.update(self.savings())
        if self.budget is not None:
            out["budget_usd"] = self.budget
        out["note"] = "Estimated from the price table in Settings; not an invoice."
        return out

    # ---- helpers used by the engines -------------------------------------
    def transcription(self, model, audio_seconds):
        return self.record("analysis", model, audio_minutes=audio_seconds / 60.0)

    def chat(self, model, usage):
        def g(name):
            if usage is None:
                return 0
            v = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, 0)
            return int(v or 0)
        return self.record("script", model, input_tokens=g("prompt_tokens"), output_tokens=g("completion_tokens"))

    def tts(self, model, text, audio_seconds):
        chars = len(text or "")
        return self.record("voice", model, chars=chars, input_tokens=max(1, round(chars / 4)),
                           audio_minutes=audio_seconds / 60.0)


def estimate_job_cost(duration_s, pricing=None, models=None):
    """
    Rough cost of dubbing a video of `duration_s` seconds, BEFORE anything is spent
    (and before cache savings), from the price table and typical ratios in config.
    Returns {"analysis", "script", "voice", "total"} in USD (None when a price is unknown).
    """
    models = models or {"analysis": config.TRANSCRIBE_MODEL, "script": config.TEXT_MODEL,
                        "voice": config.TTS_MODEL}
    speech_s = max(0.0, float(duration_s)) * config.EST_SPEECH_FRACTION
    voice_s = speech_s * config.EST_VOICE_TAKES
    chars = voice_s * config.EST_CHARS_PER_SECOND
    units = {
        "analysis": {"audio_minutes": duration_s / 60.0},
        "script": {"input_tokens": speech_s * config.EST_SCRIPT_IN_TOKENS_PER_S,
                   "output_tokens": speech_s * config.EST_SCRIPT_OUT_TOKENS_PER_S},
        "voice": {"audio_minutes": voice_s / 60.0, "chars": chars, "input_tokens": chars / 4},
    }
    out = {"duration": round(float(duration_s), 1)}
    for role in ROLES:
        cost = estimate_cost(models[role], units[role], pricing)
        out[role] = None if cost is None else round(cost, 4)
    known = [out[r] for r in ROLES if out[r] is not None]
    out["total"] = round(sum(known), 4)
    out["unknown"] = [r for r in ROLES if out[r] is None]
    return out
