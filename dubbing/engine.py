"""
AI engine: transcription, translation, rewriting and text-to-speech (OpenAI).

The pipeline only talks to an object with these methods, so another provider
(or the offline test engine) can be dropped in without touching the pipeline:

    transcribe(wav_path, work_dir, total_duration) -> [(start, end, text)]
    translate_segment(segment, language, prev_text, next_text) -> str
    rewrite_concise(segment, text, language, target_s, current_s, attempt) -> str
    rewrite_expanded(segment, text, language, target_s, current_s, attempt) -> str
    generate_tts(text, language, out_wav) -> Path

Optional batch methods (one API call for many lines; the pipeline falls back to
the methods above when an engine does not have them):

    translate_batch(items, language) -> {i: text}
        items: [{"i", "text", "seconds", "words"}]
    rewrite_batch(items, language) -> {i: text}
        items: [{"i", "mode" ("shorter"/"longer"), "source", "text", "target_s", "current_s", "words"}]

A batch answer may miss items (the model skipped one, or the JSON was broken):
the caller retries the missing ones. Caching, de-duplication and the budget
cap are added around any engine by economy.CachedEngine.

Each kind of work uses its own OpenAI client and API key ("role"):
    analysis -> transcription, script -> translation + rewrites, voice -> TTS.
An optional `tracker` (costs.CostTracker) records the usage of every call.
"""
import json
import logging
import os
import re
from pathlib import Path

from . import config
from .ffmpeg_utils import detect_silences, encode_chunk_for_upload, measure_audio_duration, normalize_tts
from .retry import with_retries

log = logging.getLogger("dubbing")

ROLE_LABELS = {"analysis": "Analysis (transcription)", "script": "Script (translation)", "voice": "Voice (TTS)"}


class MissingApiKey(RuntimeError):
    pass


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _clean_model_text(text):
    text = (text or "").strip()
    text = re.sub(r"^(rewritten line|translation|voice-over|line)\s*:\s*", "", text, flags=re.I)
    text = " ".join(text.split())
    pairs = [('"', '"'), ("«", "»"), ("“", "”"), ("'", "'"), ("„", "“")]
    for a, b in pairs:
        if len(text) > 1 and text.startswith(a) and text.endswith(b):
            text = text[len(a):-len(b)].strip()
    return text


def word_budget(seconds, language):
    return max(1, round(seconds * language["words_per_second"]))


def rewrite_model():
    return config.REWRITE_MODEL or config.TEXT_MODEL


def parse_batch_items(content, key):
    """{"<key>": [{"i": .., "text": ..}]} -> {i: cleaned text}. Broken JSON or
    malformed items are ignored (the caller retries what is missing)."""
    try:
        data = json.loads(content or "")
    except (TypeError, ValueError):
        log.warning("The text model returned invalid JSON; the missing lines will be retried.")
        return {}
    rows = data.get(key) if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return {}
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            i = int(row.get("i"))
        except (TypeError, ValueError):
            continue
        text = _clean_model_text(row.get("text") if isinstance(row.get("text"), str) else "")
        if text:
            out[i] = text
    return out


# Bump a version when its prompt changes: the cache then stops reusing old answers.
TRANSLATE_PROMPT_VERSION = "t2"
REWRITE_PROMPT_VERSION = "r2"

# Long, shared instructions first (identical for every batch of a job, so the
# OpenAI automatic prompt cache can apply); the variable part goes last.
SYSTEM_TRANSLATE_BATCH = (
    "You are an expert audiovisual translator writing a voice-over script for dubbing into {lang}.\n"
    "You receive consecutive spoken segments of one video as JSON. Translate EACH segment into "
    "natural, spoken {lang}.\n"
    "Rules:\n"
    "- Be faithful to the meaning. Never invent facts or add information.\n"
    "- Sound like a native {lang} speaker talking, not a literal translation.\n"
    "- No padding, no filler, no notes, no quotes, no stage directions.\n"
    "- Keep every segment separate: never merge, split, skip or move words between segments.\n"
    "- Match each segment's spoken length: about \"seconds\" seconds, roughly \"words\" words.\n"
    "- Use the other segments as context (consistent names, terms and tone).\n"
    "- If a segment is already in {lang}, return it cleaned up but unchanged in meaning.\n"
    "Answer with JSON only: {{\"translations\": [{{\"i\": <id>, \"text\": \"<{lang} voice-over>\"}}]}} "
    "with exactly one entry per input segment, same ids."
)

SYSTEM_REWRITE_BATCH = (
    "You are an expert dubbing script editor for {lang}. Each item is a {lang} voice-over line that "
    "must be rewritten to a target spoken length, staying natural and faithful.\n"
    "- mode \"shorter\": more concise; keep all important meaning; remove repetition, filler and "
    "redundant words.\n"
    "- mode \"longer\": slightly fuller, natural phrasing (complete sentences, connecting words); "
    "preserve the meaning exactly; invent nothing; no unnatural repetition.\n"
    "Aim for \"target_seconds\" when spoken (roughly \"words\" words). \"source\" is the original text, "
    "for meaning only.\n"
    "Answer with JSON only: {{\"lines\": [{{\"i\": <id>, \"text\": \"<rewritten {lang} line>\"}}]}} "
    "with exactly one entry per item, same ids."
)

SYSTEM_TRANSLATE = (
    "You are an expert audiovisual translator who writes voice-over scripts for dubbing.\n"
    "Translate the given spoken segment into natural, spoken {lang}.\n"
    "Rules:\n"
    "- Be faithful to the meaning. Never invent facts or add information.\n"
    "- Sound like a native {lang} speaker talking, not a literal translation.\n"
    "- No padding, no filler, no notes, no explanations, no quotes, no stage directions.\n"
    "- Write so it reads naturally aloud for a text-to-speech voice.\n"
    "- If the segment is already in {lang}, return it cleaned up but unchanged in meaning.\n"
    "Output ONLY the {lang} voice-over text."
)

SYSTEM_REWRITE = (
    "You are an expert dubbing script editor for {lang}. You adjust the length of "
    "voice-over lines so they match the original timing while staying natural and faithful. "
    "Output ONLY the rewritten {lang} line."
)


def test_api_key(key, model=None):
    """Cheap check (lists models, costs nothing). Returns a short message; raises on failure."""
    from openai import OpenAI
    client = OpenAI(api_key=key, timeout=20, max_retries=0)
    ids = {m.id for m in client.models.list()}
    if model and model not in ids:
        return f"Key works, but the model '{model}' is not available on this account."
    return f"Key works ({len(ids)} models available)."


class OpenAIEngine:
    cache_namespace = "openai"

    def __init__(self, api_key=None, keys=None, tracker=None):
        """
        keys: {"analysis": ..., "script": ..., "voice": ...}. A missing role falls
        back to `api_key`, then to the OPENAI_API_KEY environment variable.
        """
        keys = dict(keys or {})
        fallback = api_key or os.environ.get("OPENAI_API_KEY", "")
        missing = [r for r in ROLE_LABELS if not (keys.get(r) or fallback)]
        if missing:
            names = ", ".join(ROLE_LABELS[r] for r in missing)
            raise MissingApiKey(f"No OpenAI API key for: {names}.\n"
                                "Open Settings and paste your key(s), then try again.")
        from openai import OpenAI

        def client(role):
            return OpenAI(api_key=keys.get(role) or fallback, timeout=config.API_TIMEOUT, max_retries=1)

        self.analysis_client = client("analysis")
        self.script_client = client("script")
        self.voice_client = client("voice")
        self.tracker = tracker

    @property
    def client(self):
        """Backward compatibility: the old single client."""
        return self.script_client

    # ------------------------------------------------------------------ STT
    def _chunk_bounds(self, wav_path, total):
        size = config.TRANSCRIBE_CHUNK_SECONDS
        if total <= size + 30:
            return [(0.0, total)]
        silences = detect_silences(wav_path)
        bounds, start = [], 0.0
        while total - start > size + 30:
            ideal = start + size
            # cut in the middle of a silence close to (but before) the ideal point
            options = [(s + e) / 2 for s, e in silences if ideal - 90 <= (s + e) / 2 <= ideal]
            cut = max(options) if options else ideal
            bounds.append((start, cut))
            start = cut
        bounds.append((start, total))
        return bounds

    def transcribe(self, wav_path, work_dir, total_duration):
        results = []
        bounds = self._chunk_bounds(wav_path, total_duration)
        for n, (start, end) in enumerate(bounds, 1):
            chunk = encode_chunk_for_upload(wav_path, Path(work_dir) / f"stt_chunk_{n:03d}.mp3", start, end - start)
            log.info("Transcribing chunk %d/%d (%.0f s - %.0f s)", n, len(bounds), start, end)

            model = config.TRANSCRIBE_MODEL

            def call():
                with open(chunk, "rb") as fh:
                    return self.analysis_client.audio.transcriptions.create(
                        model=model, file=fh,
                        response_format="verbose_json", timestamp_granularities=["segment"])

            resp = with_retries(call, f"Transcription chunk {n}")
            if self.tracker:
                self.tracker.transcription(model, end - start)
            for s in _get(resp, "segments", None) or []:
                no_speech = _get(s, "no_speech_prob", 0) or 0
                logprob = _get(s, "avg_logprob", 0) or 0
                if no_speech > config.NO_SPEECH_PROB_LIMIT and logprob < config.NO_SPEECH_LOGPROB_LIMIT:
                    continue
                results.append((start + float(_get(s, "start", 0)), start + float(_get(s, "end", 0)),
                                _get(s, "text", "")))
        return results

    # ------------------------------------------------------------ text model
    def _chat(self, system, user, what, model=None):
        model = model or config.TEXT_MODEL

        def call():
            resp = self.script_client.chat.completions.create(
                model=model, temperature=config.TEXT_TEMPERATURE,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            # recorded once per answer received: each one is billed, even if it is unusable
            if self.tracker:
                self.tracker.chat(model, getattr(resp, "usage", None))
            text = _clean_model_text(resp.choices[0].message.content)
            if not text:
                raise RuntimeError("empty answer from the text model")
            return text
        return with_retries(call, what)

    def _chat_json(self, system, payload, key, what, model=None):
        """One JSON-mode call for a whole batch -> {i: text} (possibly incomplete)."""
        model = model or config.TEXT_MODEL
        user = json.dumps(payload, ensure_ascii=False)

        def call():
            resp = self.script_client.chat.completions.create(
                model=model, temperature=config.TEXT_TEMPERATURE,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            if self.tracker:
                self.tracker.chat(model, getattr(resp, "usage", None))
            return resp.choices[0].message.content
        # a broken / incomplete answer is NOT retried here: the caller retries only
        # the missing lines, which is cheaper than paying for the whole batch again
        return parse_batch_items(with_retries(call, what), key)

    def translate_batch(self, items, language):
        lang = language["name"]
        payload = {"segments": [{"i": it["i"], "text": it["text"], "seconds": round(it["seconds"], 1),
                                 "words": it["words"]} for it in items]}
        return self._chat_json(SYSTEM_TRANSLATE_BATCH.format(lang=lang), payload, "translations",
                               f"Translation of {len(items)} segments")

    def rewrite_batch(self, items, language):
        lang = language["name"]
        payload = {"items": [{"i": it["i"], "mode": it["mode"], "source": it["source"], "line": it["text"],
                              "current_seconds": round(it["current_s"], 1),
                              "target_seconds": round(it["target_s"], 1), "words": it["words"]}
                             for it in items]}
        return self._chat_json(SYSTEM_REWRITE_BATCH.format(lang=lang), payload, "lines",
                               f"Length rewrite of {len(items)} lines", model=rewrite_model())

    def translate_segment(self, segment, language, prev_text="", next_text=""):
        lang = language["name"]
        seconds = segment.natural_duration
        user = (
            (f"Previous segment (context only, do not translate): {prev_text}\n" if prev_text else "")
            + f"SEGMENT TO TRANSLATE: {segment.source_text}\n"
            + (f"Next segment (context only, do not translate): {next_text}\n" if next_text else "")
            + f"\nThe original speaker says this in about {seconds:.1f} seconds. "
              f"Aim for a similar spoken length (roughly {word_budget(seconds, language)} words)."
        )
        return self._chat(SYSTEM_TRANSLATE.format(lang=lang), user, f"Translation of segment {segment.index}")

    def rewrite_concise(self, segment, text, language, target_s, current_s, attempt):
        lang = language["name"]
        pct = max(40, round(100 * target_s / max(current_s, 0.01)))
        user = (
            f"Original source text: {segment.source_text}\n"
            f"Current {lang} line: {text}\n\n"
            f"Spoken aloud, the current line takes {current_s:.1f} s but it must fit in {target_s:.1f} s "
            f"(about {pct}% of its current length, roughly {word_budget(target_s, language)} words).\n"
            "Rewrite it more concisely:\n"
            "- keep all important meaning\n- remove repetition, filler and redundant words\n"
            "- keep it natural spoken language\n- do not add any facts\n"
            + ("- this is a second attempt: be clearly shorter than before\n" if attempt > 1 else "")
        )
        return self._chat(SYSTEM_REWRITE.format(lang=lang), user, f"Concise rewrite of segment {segment.index}",
                          model=rewrite_model())

    def rewrite_expanded(self, segment, text, language, target_s, current_s, attempt):
        lang = language["name"]
        user = (
            f"Original source text: {segment.source_text}\n"
            f"Current {lang} line: {text}\n\n"
            f"Spoken aloud, the current line takes only {current_s:.1f} s but the original speaker spoke "
            f"for {target_s:.1f} s. Rewrite it slightly fuller and more natural so it lasts about "
            f"{target_s:.1f} s (roughly {word_budget(target_s, language)} words):\n"
            "- preserve the original meaning exactly\n- do NOT invent new information\n"
            "- do NOT repeat the same idea unnaturally\n"
            "- use fuller natural phrasing, complete sentences, natural connecting words\n"
            + ("- this is a second attempt: make it a little longer than before\n" if attempt > 1 else "")
        )
        return self._chat(SYSTEM_REWRITE.format(lang=lang), user, f"Expanded rewrite of segment {segment.index}",
                          model=rewrite_model())

    # ------------------------------------------------------------------ TTS
    def generate_tts(self, text, language, out_wav):
        out_wav = Path(out_wav)
        raw = out_wav.with_name(out_wav.stem + "_raw.wav")

        model = config.TTS_MODEL

        def call():
            kwargs = dict(model=model, voice=language["voice"], input=text, response_format="wav")
            if not model.startswith("tts-1"):
                kwargs["instructions"] = language["tts_instructions"]
            with self.voice_client.audio.speech.with_streaming_response.create(**kwargs) as resp:
                resp.stream_to_file(raw)
            if not raw.exists() or raw.stat().st_size < 100:
                raise RuntimeError("empty audio returned by the TTS model")

        with_retries(call, "Voice generation")
        # generated audio length (24 kHz 16-bit mono WAV), used for the spend estimate
        generated_s = max(0.0, (raw.stat().st_size - 44) / (config.TTS_SAMPLE_RATE * 2))
        normalize_tts(raw, out_wav)
        raw.unlink(missing_ok=True)
        if self.tracker:
            if generated_s <= 0:
                generated_s = measure_audio_duration(out_wav)
            self.tracker.tts(model, text, generated_s)
        return out_wav
