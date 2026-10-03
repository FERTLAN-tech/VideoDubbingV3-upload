"""
The script: translation of every segment and the length rewrites decided
BEFORE the first voice take, both sent in batches (one API call for many lines).

Batches are validated: a line missing from the answer (or an unreadable answer)
is asked again in a smaller batch, then one by one as a last resort. Results
already paid for come from the cache (economy.CachedEngine) for free.

`run_parallel(items, worker, progress, stage, label, on_done)` is the pipeline's
parallel runner (passed in to keep this module free of pipeline imports).
"""
import logging

from . import config
from .costs import BudgetExceeded
from .economy import predicted_misfit
from .engine import word_budget
from .segments import is_speech_text

log = logging.getLogger("dubbing")


def make_batches(items, size, max_chars=None):
    """Consecutive batches of at most `size` items and about `max_chars` characters."""
    batches, cur, chars = [], [], 0
    for it in items:
        n = len(it.get("text", ""))
        if cur and (len(cur) >= size or (max_chars and chars + n > max_chars)):
            batches.append(cur)
            cur, chars = [], 0
        cur.append(it)
        chars += n
    if cur:
        batches.append(cur)
    return batches


def _batch_with_recovery(call, items, label):
    """call(items) -> {i: text}. One retry of the missing items as a smaller batch.
    Returns (answers, still_missing_items)."""
    got = {}
    for attempt in (1, 2):
        todo = [it for it in items if it["i"] not in got]
        if not todo:
            break
        try:
            got.update(call(todo))
        except BudgetExceeded:
            raise
        except Exception as exc:  # noqa: BLE001 - recovered below, line by line
            if type(exc).__name__ == "JobCancelled":
                raise
            log.warning("%s: batch of %d failed (%s)", label, len(todo), exc)
        missing = len([it for it in items if it["i"] not in got])
        if missing and attempt == 1:
            log.info("%s: %d of %d lines missing from the answer, asking again for those only",
                     label, missing, len(items))
    return got, [it for it in items if it["i"] not in got]


# --------------------------------------------------------------------------
# Translation
# --------------------------------------------------------------------------
def translate_all(engine, segments, language, progress, hooks, run_parallel):
    """Translation of every segment (list of str; "" for non-speech segments)."""
    n = len(segments)
    out = [""] * n
    items = []
    for i, seg in enumerate(segments):
        if not is_speech_text(seg.source_text):
            log.info("Segment %d is not speech (%r): no translation, no voice", seg.index, seg.source_text)
            hooks.segment_update(i, translated_text="")
            continue
        items.append({"i": i, "text": seg.source_text, "seconds": seg.natural_duration,
                      "words": word_budget(seg.natural_duration, language),
                      "prev": segments[i - 1].source_text if i > 0 else "",
                      "next": segments[i + 1].source_text if i + 1 < n else ""})

    def per_segment(it):
        i = it["i"]
        return engine.translate_segment(segments[i], language, it["prev"], it["next"])

    def publish(answers):
        for i, text in answers.items():
            out[i] = text
            hooks.segment_update(i, translated_text=text)

    if not getattr(engine, "can_batch_translate", False):
        run_parallel(items, per_segment, progress, "translate", "Translating",
                     on_done=lambda k, text: publish({items[k]["i"]: text}))
        return out

    known = engine.lookup_translations(items, language)
    if known:
        log.info("Translation: %d of %d lines reused from the cache", len(known), len(items))
        publish(known)
    todo = [it for it in items if it["i"] not in known]
    batches = make_batches(todo, config.TRANSLATE_BATCH_SIZE, config.TRANSLATE_BATCH_CHARS)
    if batches:
        log.info("Translating %d lines in %d batch call(s)", len(todo), len(batches))

    def run_batch(batch):
        # (if this batch is run again after a failure, what was already answered is free)
        got = engine.lookup_translations(batch, language)
        rest = [it for it in batch if it["i"] not in got]
        if rest:
            answers, missing = _batch_with_recovery(lambda b: engine.translate_batch(b, language), rest,
                                                    "Translation")
            got.update(answers)
            for it in missing:                       # last resort: one line at a time
                got[it["i"]] = per_segment(it)
        return got

    run_parallel(batches, run_batch, progress, "translate", "Translating", on_done=lambda k, got: publish(got))
    return out


# --------------------------------------------------------------------------
# Rewrites decided from the predicted length (before paying for a voice take)
# --------------------------------------------------------------------------
def pre_tts_rewrites(engine, segments, language, texts, indexes, predictor):
    """
    For the lines of `indexes` that are predicted not to fit, a shorter / fuller
    rewrite (batched). Returns {i: (new_text, "long"|"short")} for the lines changed.
    Failures are not fatal: the line is then simply voiced as it is.
    """
    items = []
    for i in indexes:
        kind = predicted_misfit(segments[i], texts[i], predictor)
        if not kind:
            continue
        seg = segments[i]
        target = seg.slot_duration if kind == "long" else seg.natural_duration
        items.append({"i": i, "segment": seg, "kind": kind, "mode": "shorter" if kind == "long" else "longer",
                      "source": seg.source_text, "text": texts[i], "target_s": target,
                      "current_s": predictor.predict(texts[i]), "words": word_budget(target, language),
                      "attempt": 1})
    if not items:
        return {}
    log.info("%d lines are predicted not to fit: rewriting them before voicing (%d too long, %d too short)",
             len(items), sum(it["kind"] == "long" for it in items), sum(it["kind"] == "short" for it in items))

    def one(it):
        method = engine.rewrite_concise if it["mode"] == "shorter" else engine.rewrite_expanded
        return method(it["segment"], it["text"], language, it["target_s"], it["current_s"], 1)

    got = {}
    if getattr(engine, "can_batch_rewrite", False):
        got.update(engine.lookup_rewrites(items, language))
        todo = [it for it in items if it["i"] not in got]
        for batch in make_batches(todo, config.REWRITE_BATCH_SIZE):
            answers, missing = _batch_with_recovery(lambda b: engine.rewrite_batch(b, language), batch, "Rewrite")
            got.update(answers)
            for it in missing:
                got[it["i"]] = _safe(one, it)
    else:
        for it in items:
            got[it["i"]] = _safe(one, it)

    out = {}
    for it in items:
        new = got.get(it["i"])
        if new and new != it["text"] and is_speech_text(new):
            out[it["i"]] = (new, it["kind"])
    return out


def _safe(fn, it):
    try:
        return fn(it)
    except BudgetExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "JobCancelled":
            raise
        log.warning("Rewrite of segment %d failed (%s): it is voiced as it is", it["segment"].index, exc)
        return None
