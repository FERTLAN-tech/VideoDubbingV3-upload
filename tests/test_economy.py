"""
Offline tests of the money-saving features (no internet, no API key):

* (a) cache: a second identical run makes ZERO transcription / translation / voice
      calls and gives the same output; the same video in another language is not
      transcribed again;
* (b) batch translation: ceil(n / batch) calls instead of n, recovery from a broken
      or incomplete batch answer;
* (c) economy mode: fewer voice + rewrite calls than economy off, no overlaps,
      every clip inside its slot;
* (d) the budget cap stops a job cleanly (no output folder);
* (e) non-speech lines are not translated or voiced, identical lines are voiced once.

It ends with a before / after table of API calls on the offline test plan.

Run:  .venv\\Scripts\\python.exe -m tests.test_economy
"""
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TMP = Path(tempfile.mkdtemp(prefix="vdub_economy_"))
os.environ["APPDATA"] = str(TMP / "appdata")          # never touch the real cache

from dubbing import config  # noqa: E402
from dubbing.cache import Cache  # noqa: E402
from dubbing.costs import BudgetExceeded, CostTracker, estimate_job_cost  # noqa: E402
from dubbing.economy import DurationPredictor, wrap_engine  # noqa: E402
from dubbing.fake_engine import FAKE_PLAN, FakeEngine, make_test_video  # noqa: E402
from dubbing.ffmpeg_utils import prepare_path  # noqa: E402
from dubbing.languages import get_language  # noqa: E402
from dubbing.pipeline import PipelineHooks, Progress, _process_segments, dub_video_file, friendly_error  # noqa: E402
from dubbing.script import translate_all  # noqa: E402
from dubbing.segments import Segment, calculate_segment_slots, is_speech_text  # noqa: E402

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
VIDEO = None
COUNTS = {}          # label -> fake engine call counts (for the before/after table)
COSTS = {}           # label -> estimated $ spent


class LegacyFake(FakeEngine):
    """The engine as it was before: no batch methods."""
    translate_batch = None
    rewrite_batch = None


def _video():
    global VIDEO
    if VIDEO is None:
        prepare_path()
        VIDEO = make_test_video(TMP / "source.mp4")
    return VIDEO


def _run(name, engine, cache, language="French", economy=True, predict=True, plan_segments=None):
    """One full dub with the fake engine; returns (result, report json, wrapped usage)."""
    saved = (config.ECONOMY_MODE, config.PREDICT_BEFORE_TTS, config.OUTPUT_DIR, config.LOG_DIR)
    config.ECONOMY_MODE, config.PREDICT_BEFORE_TTS = economy, predict
    config.OUTPUT_DIR = TMP / "out" / name
    config.LOG_DIR = config.OUTPUT_DIR / "logs"
    try:
        result = dub_video_file(_video(), f"Economy {name}", language, engine, TMP / "work" / name,
                                source_url=URL, segments=plan_segments, cache=cache)
    finally:
        config.ECONOMY_MODE, config.PREDICT_BEFORE_TTS, config.OUTPUT_DIR, config.LOG_DIR = saved
    report = json.loads(result.json_path.read_text(encoding="utf-8"))
    return result, report


def _check_timing(report):
    rows = report["segments"]
    for row in rows:
        assert row["voice_end"] <= row["slot_end"] + 1e-3, row
        assert config.MAX_SLOW_DOWN - 1e-3 <= row["stretch_factor"] <= config.MAX_SPEED_UP + 1e-3, row
    for a, b in zip(rows, rows[1:]):
        assert a["voice_end"] <= b["start"] + 1e-3, (a, b)


def _paid(calls):
    return {k: v for k, v in calls.items() if v}


# --------------------------------------------------------------------------- (a)
def test_cache_second_run_is_free():
    cache = Cache(TMP / "cache_a")
    cold_tracker = CostTracker(None, "cold")
    cold = FakeEngine(cold_tracker)
    r1, rep1 = _run("cold", cold, cache)
    assert cold.calls["transcribe"] == 1 and cold.calls["tts"] > 0, cold.calls
    assert cold_tracker.total_cost() > 0
    COUNTS["Economy ON, cold cache"] = dict(cold.calls)
    COSTS["Economy ON, cold cache"] = cold_tracker.total_cost()

    warm_tracker = CostTracker(None, "warm")
    warm = FakeEngine(warm_tracker)
    r2, rep2 = _run("warm", warm, cache)
    assert sum(warm.calls.values()) == 0, f"second run made API calls: {_paid(warm.calls)}"
    assert sum(r2.usage["api_calls"].values()) == 0, r2.usage
    hits = r2.usage["cache_hits"]
    assert hits["transcribe"] == 1 and hits["translate"] >= 8 and hits["tts"] >= 8, hits
    assert warm_tracker.total_cost() == 0 and len(warm_tracker.entries) == 0
    savings = warm_tracker.savings()
    assert savings["cache_hits"] == sum(hits.values()) and savings["saved_usd"] > 0, savings
    # the money saved is about what the cold run paid for the same work
    assert abs(savings["saved_usd"] - cold_tracker.total_cost()) < 0.25 * cold_tracker.total_cost() + 1e-6, \
        (savings, cold_tracker.total_cost())
    assert rep2["api_usage"]["saved_usd"] == savings["saved_usd"]
    assert rep2["summary"]["api_calls"] == 0 and rep2["summary"]["cache_hits"] == sum(hits.values())
    COUNTS["Economy ON, warm cache (re-run)"] = dict(warm.calls)
    COSTS["Economy ON, warm cache (re-run)"] = warm_tracker.total_cost()

    # same output
    keys = ("index", "translated_text", "tts_duration", "stretch_factor", "final_duration", "timing_mode", "steps")
    assert [{k: s[k] for k in keys} for s in rep1["segments"]] == [{k: s[k] for k in keys} for s in rep2["segments"]]
    assert r1.srt_path.read_text(encoding="utf-8") == r2.srt_path.read_text(encoding="utf-8")

    # the same video into another language: no new transcription
    other = FakeEngine(CostTracker(None, "es"))
    _run("spanish", other, cache, language="Spanish")
    assert other.calls["transcribe"] == 0 and other.calls["translate_batch"] >= 1, other.calls
    print(f"OK  (a) warm cache: 0 API calls, {savings['cache_hits']} hits, saved ${savings['saved_usd']:.4f}; "
          "other language reuses the transcript")


# --------------------------------------------------------------------------- (b)
def _many_segments(n):
    segs = [Segment(i + 1, i * 3.0, i * 3.0 + 2.5, f"Line {i + 1} of the talk. ratio=1.0 end") for i in range(n)]
    return calculate_segment_slots(segs, n * 3.0)


def test_batch_translation():
    language = get_language("French")
    n = 70
    segs = _many_segments(n)
    expected = [FakeEngine()._translate(s.source_text, s.natural_duration) for s in segs]

    fake = FakeEngine()
    out = translate_all(wrap_engine(fake, None), segs, language, Progress(), PipelineHooks(), _process_segments)
    want = math.ceil(n / config.TRANSLATE_BATCH_SIZE)
    assert fake.calls["translate_batch"] == want and fake.calls["translate"] == 0, fake.calls
    assert out == expected
    assert all(size <= config.TRANSLATE_BATCH_SIZE for size in fake.batch_sizes)

    # broken JSON once + items missing once + one item never answered
    fake = FakeEngine()
    fake.break_batches = 1
    fake.drop_ids = {5, 40}
    fake.never_answer = {66}
    out = translate_all(wrap_engine(fake, None), segs, language, Progress(), PipelineHooks(), _process_segments)
    assert out == expected, [i for i, (a, b) in enumerate(zip(out, expected)) if a != b]
    assert fake.calls["translate"] == 1, fake.calls            # only the never-answered line, alone
    assert fake.calls["translate_batch"] <= want * 2, fake.calls
    print(f"OK  (b) batch translation: {n} lines in {want} calls (was {n}); recovered from broken JSON, "
          f"missing items and an unanswered line ({fake.calls['translate_batch']} batch calls + 1 single)")


# --------------------------------------------------------------------------- (c)
def test_economy_mode_saves_calls():
    results = {}
    for label, economy in (("off", False), ("on", True)):
        fake = FakeEngine(CostTracker(None, label))
        _, report = _run(f"econ_{label}", fake, False, economy=economy)
        _check_timing(report)
        results[label] = (fake.calls, report)
        COSTS[f"Economy {label.upper()}, cold cache"] = fake.tracker.total_cost()
    off, on = results["off"][0], results["on"][0]
    paid = lambda c: c["tts"] + c["rewrite"] + c["rewrite_batch"]   # noqa: E731
    assert paid(on) < paid(off), (dict(on), dict(off))
    COUNTS["Economy OFF, cold cache"] = dict(off)
    modes_on = {s["index"]: s for s in results["on"][1]["segments"]}
    # stretch first: seg 5 (a bit short: 0.94x is enough) is now only slowed down, no rewrite
    assert modes_on[5]["timing_mode"] in ("slow_down", "padded"), modes_on[5]
    assert "expanded_rewrite" not in modes_on[5]["steps"], modes_on[5]
    assert modes_on[5]["stretch_factor"] >= config.MAX_SLOW_DOWN
    # seg 2 needs 1.18x (> ECONOMY_SPEEDUP_LIMIT 1.15): still rewritten
    assert "concise_rewrite" in modes_on[2]["steps"], modes_on[2]
    # a line far too long is still rewritten (before its first voice take)
    assert "concise_rewrite" in modes_on[3]["steps"] and modes_on[3]["warnings"]
    print(f"OK  (c) economy mode: {paid(on)} voice+rewrite calls instead of {paid(off)}; no overlaps, "
          "all clips inside their slots")


# --------------------------------------------------------------------------- (d)
def test_budget_cap():
    out_dir = TMP / "out" / "budget"
    tracker = CostTracker(None, "budget", budget=0.001)      # transcription alone costs $0.004
    fake = FakeEngine(tracker)
    try:
        _run("budget", fake, False)
    except BudgetExceeded as exc:
        msg = friendly_error(exc)
        assert "spending limit" in msg and "$0.00" in msg, msg
    else:
        raise AssertionError("the budget cap did not stop the job")
    assert fake.calls["transcribe"] == 1 and fake.calls["tts"] == 0 and fake.calls["translate_batch"] == 0
    assert not out_dir.exists() or not any(out_dir.rglob("*.mp4")), "no broken output may be written"
    print("OK  (d) budget cap stops the job cleanly before the next paid call")


# --------------------------------------------------------------------------- (e)
def test_non_speech_and_duplicates():
    for text in ("", "   ", "...", "♪ ♪", "[Music]", "(Applause)", "[Musique] ♪", "*laughs*", "-- !"):
        assert not is_speech_text(text), text
    for text in ("Hello", "Music is my life.", "[Music] Hello everyone", "OK", "42"):
        assert is_speech_text(text), text

    plan = [
        (0.5, 3.0, 1.0, "Hello there friend. ratio=1.0 end"),
        (3.6, 6.1, 1.0, "[Music]"),
        (6.7, 9.2, 1.0, "Hello there friend. ratio=1.0 end"),
        (9.8, 12.3, 1.0, "♪ ♪"),
        (13.0, 15.5, 1.0, "..."),
        (16.0, 18.5, 1.0, "And something else. ratio=1.0 end"),
    ]
    fake = FakeEngine(CostTracker(None, "dedup"), plan=plan)
    result, report = _run("dedup", fake, False)
    rows = {s["index"]: s for s in report["segments"]}
    for i in (2, 4, 5):
        assert rows[i]["translated_text"] == "" and rows[i]["tts_duration"] == 0.0, rows[i]
    # the fake translates by length, so the 3 spoken lines get the very same text: voiced once
    assert fake.calls["tts"] == 1, fake.calls
    assert result.usage["cache_hits"]["tts"] == 2, result.usage
    assert fake.calls["translate_batch"] == 1 and fake.calls["translate"] == 0, fake.calls
    srt = result.srt_path.read_text(encoding="utf-8")
    assert srt.count("-->") == 3, srt
    _check_timing(report)
    print("OK  (e) non-speech lines skipped (no translation, no voice); 3 identical lines -> 1 voice call")


# --------------------------------------------------------------------------- misc
def test_predictor_and_estimate():
    lang = get_language("French")
    p = DurationPredictor(lang)
    assert abs(p.predict("a" * 145) - 10.0) < 1e-6
    p.observe("a" * 145, 8.0)
    p.observe("a" * 145, 8.4)
    assert p.calibrated and abs(p.predict("a" * 145) - 8.2) < 1e-6
    est = estimate_job_cost(600)
    assert 0.1 < est["total"] < 0.5 and not est["unknown"], est        # ~10-min video: cents, not dollars
    assert abs(est["analysis"] - 0.06) < 1e-6
    print(f"OK  predictor calibration; estimate for a 10-minute video ${est['total']:.3f}")


def test_cache_cleanup():
    cache = Cache(TMP / "cache_lru", max_bytes=60_000)
    for n in range(30):
        cache.put("translate", f"{n:064x}", "x" * 4000)
    assert cache.size() <= 60_000, cache.size()
    assert cache.get("translate", f"{29:064x}") is not None          # the newest are kept
    cache.clear()
    assert cache.size() == 0
    print("OK  cache size cap (oldest entries removed) and clear")


def legacy_baseline():
    """The app as it was before: per-line translation, no prediction, rewrite first, no cache."""
    fake = LegacyFake(CostTracker(None, "legacy"))
    _, report = _run("legacy", fake, False, economy=False, predict=False)
    _check_timing(report)
    COUNTS["BEFORE (per-line, rewrite-first, no cache)"] = dict(fake.calls)
    COSTS["BEFORE (per-line, rewrite-first, no cache)"] = fake.tracker.total_cost()


def print_comparison():
    cols = ["transcribe", "translate", "translate_batch", "rewrite", "rewrite_batch", "tts"]
    print("\nAPI calls on the offline test plan (8 segments, 40 s video):")
    print(f"  {'run':46}" + "".join(f"{c:>16}" for c in cols) + f"{'TOTAL':>8}{'est. $':>10}")
    for label in ("BEFORE (per-line, rewrite-first, no cache)", "Economy OFF, cold cache",
                  "Economy ON, cold cache", "Economy ON, warm cache (re-run)"):
        c = COUNTS.get(label, {})
        print(f"  {label:46}" + "".join(f"{c.get(k, 0):>16}" for k in cols) + f"{sum(c.values()):>8}{COSTS.get(label, 0):>10.4f}")


if __name__ == "__main__":
    try:
        test_predictor_and_estimate()
        test_cache_cleanup()
        test_batch_translation()
        test_non_speech_and_duplicates()
        test_budget_cap()
        test_economy_mode_saves_calls()
        test_cache_second_run_is_free()
        legacy_baseline()
        print_comparison()
        print("ALL ECONOMY TESTS PASSED")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
