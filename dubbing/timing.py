"""
Bidirectional adaptive timing, applied to every segment independently.

TOO LONG  : concise rewrite -> TTS -> (retry) -> speed up (max MAX_SPEED_UP)
TOO SHORT : fuller rewrite  -> TTS -> (retry) -> slow down (min MAX_SLOW_DOWN) -> tiny padding

"Too long"  = longer than the slot (until the next segment starts, minus the safety gap).
"Too short" = shorter than SHORT_THRESHOLD x the natural length, which is how long the
              original speaker talked. Pauses after the original speech are kept as pauses.

Economy mode (default) is "stretch first": a rewrite + new voice take is only paid
for when time-stretching alone would not sound good, i.e. when the speed-up needed
to fit the slot is above ECONOMY_SPEEDUP_LIMIT, or the slow-down needed to reach the
minimum length (SHORT_THRESHOLD x natural) is below ECONOMY_SLOWDOWN_LIMIT.
Without economy mode every line that does not fit is rewritten first.
"""
import logging
from pathlib import Path

from . import config
from .ffmpeg_utils import measure_audio_duration, time_stretch, trim_with_fade
from .segments import SegmentResult, is_speech_text

log = logging.getLogger("dubbing")


def _tts(engine, text, language, path):
    engine.generate_tts(text, language, path)
    return measure_audio_duration(path)


def initial_tts(engine, segment, language, translation, work_dir):
    """First voice take of a segment: (wav path, duration), or None for a line with nothing to say."""
    if not is_speech_text(translation):
        return None
    wav = Path(work_dir) / f"seg_{segment.index:05d}_v0.wav"
    return str(wav), _tts(engine, translation, language, wav)


def wants_shorter(dur, slot):
    """Should a clip of `dur` seconds be rewritten shorter to fit a slot of `slot` s?"""
    if dur <= slot + config.FIT_TOLERANCE:
        return False
    if config.ECONOMY_MODE:
        return dur / max(slot, 0.01) > config.ECONOMY_SPEEDUP_LIMIT
    return True


def wants_longer(dur, natural):
    """Should a clip of `dur` seconds be rewritten fuller (natural = original speaking time)?"""
    short_limit = natural * config.SHORT_THRESHOLD
    if dur >= short_limit:
        return False
    if config.ECONOMY_MODE:
        return dur / max(short_limit, 0.01) < config.ECONOMY_SLOWDOWN_LIMIT
    return True


def fit_audio_bidirectionally(engine, segment, language, translation, work_dir, initial=None,
                              pre_steps=None, rewrites_used=0):
    """
    `initial`       (wav, duration) from initial_tts(); generated here when not given.
    `pre_steps`     adjustments already made before the first voice take (a rewrite
                    decided from the predicted length), reported in `steps`.
    `rewrites_used` rewrite attempts already spent on this line (out of REWRITE_TRIES).
    """
    work_dir = Path(work_dir)
    tag = f"seg_{segment.index:05d}"
    slot = segment.slot_duration
    natural = segment.natural_duration
    short_limit = natural * config.SHORT_THRESHOLD
    tol = config.FIT_TOLERANCE
    steps, warnings = list(pre_steps or []), []
    attempts = 1 + rewrites_used

    if not is_speech_text(translation):
        return SegmentResult(segment, "", None, 0.0, 0.0, 1.0, "normal", ["empty"], 0,
                             ["nothing to say (empty or non-speech text), segment left silent"])

    # ---- initial TTS ------------------------------------------------------
    text = translation
    if initial:
        wav, dur = Path(initial[0]), initial[1]
    else:
        wav = work_dir / f"{tag}_v0.wav"
        dur = _tts(engine, text, language, wav)

    # ---- CASE A: too long -> concise rewrites ------------------------------
    if wants_shorter(dur, slot):
        for attempt in range(rewrites_used + 1, config.REWRITE_TRIES + 1):
            new_text = engine.rewrite_concise(segment, text, language, slot, dur, attempt)
            attempts += 1
            if not new_text or new_text == text or not is_speech_text(new_text):
                continue
            new_wav = work_dir / f"{tag}_c{attempt}.wav"
            new_dur = _tts(engine, new_text, language, new_wav)
            if new_dur < dur:                       # keep the shortest version
                text, wav, dur = new_text, new_wav, new_dur
                if "concise_rewrite" not in steps:
                    steps.append("concise_rewrite")
            if not wants_shorter(dur, slot):
                break

    # ---- CASE B: too short -> fuller rewrites ------------------------------
    elif wants_longer(dur, natural):
        for attempt in range(rewrites_used + 1, config.REWRITE_TRIES + 1):
            new_text = engine.rewrite_expanded(segment, text, language, natural, dur, attempt)
            attempts += 1
            if not new_text or new_text == text or not is_speech_text(new_text):
                continue
            new_wav = work_dir / f"{tag}_e{attempt}.wav"
            new_dur = _tts(engine, new_text, language, new_wav)
            # accept a longer version only if it still fits the slot
            if dur < new_dur <= slot + tol:
                text, wav, dur = new_text, new_wav, new_dur
                if "expanded_rewrite" not in steps:
                    steps.append("expanded_rewrite")
            if not wants_longer(dur, natural):
                break

    tts_duration = dur

    # ---- time stretching (both directions) ---------------------------------
    factor = 1.0
    if dur > slot + tol:
        factor = min(dur / slot * 1.01, config.MAX_SPEED_UP)
        steps.append("speed_up")
    elif dur < short_limit:
        factor = max(dur / natural, config.MAX_SLOW_DOWN)
        steps.append("slow_down")

    if abs(factor - 1.0) > 0.005:
        stretched = work_dir / f"{tag}_stretch.wav"
        time_stretch(wav, stretched, factor)
        wav, dur = stretched, measure_audio_duration(stretched)

    # ---- last resort: never overlap the next segment -----------------------
    if dur > slot:
        cut = dur - slot
        trimmed = work_dir / f"{tag}_fit.wav"
        trim_with_fade(wav, trimmed, slot)
        wav, dur = trimmed, measure_audio_duration(trimmed)
        if cut > 0.08:
            warnings.append(f"still {cut:.2f} s too long at {config.MAX_SPEED_UP}x after rewrites; end faded out")
            log.warning("Segment %d: %.2f s had to be faded out to avoid overlap", segment.index, cut)

    # ---- tiny padding after maximum slow-down ------------------------------
    if "slow_down" in steps and factor <= config.MAX_SLOW_DOWN + 1e-6 and natural - dur > 0.05:
        steps.append("padded")

    mode = steps[-1] if steps else "normal"
    return SegmentResult(segment=segment, translated_text=text, audio_path=str(wav),
                         tts_duration=round(tts_duration, 3), final_duration=round(dur, 3),
                         stretch_factor=round(factor, 3), timing_mode=mode, steps=steps,
                         attempts=attempts, warnings=warnings)
