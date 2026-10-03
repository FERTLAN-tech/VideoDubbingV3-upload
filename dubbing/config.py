"""
Central configuration for Video Dubbing V3.

Every tunable value lives here. Values marked "env" can also be overridden
with an environment variable of the same name, so nobody has to edit code.
These are the DEFAULTS: choices made in the app's Settings page (saved in
%APPDATA%\\VideoDubbingV3\\settings.json) override them at the start of each job.
"""
import os
from pathlib import Path


def _env(name, default, cast=str):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    try:
        return cast(value)
    except ValueError:
        return default


APP_NAME = "Video Dubbing V3"

# --------------------------------------------------------------------------
# Folders
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(_env("DUBBING_OUTPUT_DIR", str(BASE_DIR / "output")))
LOG_DIR = OUTPUT_DIR / "logs"
# Keep intermediate files (downloaded video, TTS clips...) for debugging.
KEEP_TEMP_FILES = _env("DUBBING_KEEP_TEMP", "0") == "1"

# --------------------------------------------------------------------------
# Bidirectional adaptive timing (the core of V3)
# --------------------------------------------------------------------------
MAX_SPEED_UP = 1.35        # fastest allowed playback of a TTS clip
MAX_SLOW_DOWN = 0.82       # slowest allowed playback of a TTS clip
SAFETY_GAP = 0.04          # seconds kept free before the next segment starts
REWRITE_TRIES = 2          # concise / expanded rewrite attempts per segment

# A clip counts as "too short" when it is shorter than this fraction of the
# segment's natural length (the original speaker's speaking time).
SHORT_THRESHOLD = 0.85
# Differences below this many seconds are ignored.
FIT_TOLERANCE = 0.02
# Fade applied if a clip ever has to be trimmed as a last resort.
TRIM_FADE = 0.04

# --------------------------------------------------------------------------
# Saving money (fewer API calls, same result)
# --------------------------------------------------------------------------
# Economy mode: stretch first. A too-long line is rewritten (+ new voice take)
# only if it would need a speed-up above ECONOMY_SPEEDUP_LIMIT; a too-short line
# is expanded only if it would need a slow-down below ECONOMY_SLOWDOWN_LIMIT to
# reach its minimum length (SHORT_THRESHOLD x natural length). Otherwise the
# clip is just time-stretched (always within MAX_SLOW_DOWN..MAX_SPEED_UP).
# False = rewrite first whenever a line does not fit (the original behaviour).
ECONOMY_MODE = True
ECONOMY_SPEEDUP_LIMIT = 1.15
ECONOMY_SLOWDOWN_LIMIT = 0.90

# Predict the spoken length of a line from its text before paying for a voice
# take; lines far outside their window are rewritten first (batched).
PREDICT_BEFORE_TTS = True

# Translation / rewrites are sent in batches (one API call for many lines).
TRANSLATE_BATCH_SIZE = 30          # max lines per call
TRANSLATE_BATCH_CHARS = 6000       # max source characters per call (~1.5k tokens)
REWRITE_BATCH_SIZE = 30

# Persistent cache (%APPDATA%\VideoDubbingV3\cache): a re-run, a retry or the
# same video in another language reuses everything already paid for.
CACHE_ENABLED = _env("DUBBING_CACHE", "1") != "0"
CACHE_MAX_GB = 2.0

# Stop a job cleanly when its estimated cost goes above this (USD). None = no cap.
MAX_SPEND_PER_VIDEO = None

# Typical ratios used for the cost estimate shown before a job spends anything.
EST_SPEECH_FRACTION = 0.85         # share of the video that is speech
EST_VOICE_TAKES = 1.15             # voice minutes generated per minute of speech
EST_CHARS_PER_SECOND = 15.0        # characters of script per second of speech
EST_SCRIPT_IN_TOKENS_PER_S = 9.0   # translation + rewrites, input tokens per s of speech
EST_SCRIPT_OUT_TOKENS_PER_S = 6.0  # output tokens per s of speech

# Transcript clean-up: very short fragments are merged with their neighbour
MIN_SEGMENT_SECONDS = 1.2
MERGE_MAX_GAP = 0.35
MERGE_MAX_SECONDS = 12.0
# Whisper segments that are probably not speech (music, noise) are dropped
NO_SPEECH_PROB_LIMIT = 0.8
NO_SPEECH_LOGPROB_LIMIT = -1.0

# --------------------------------------------------------------------------
# AI models (OpenAI)
# --------------------------------------------------------------------------
TRANSCRIBE_MODEL = _env("DUBBING_TRANSCRIBE_MODEL", "whisper-1")   # must support segment timestamps
TEXT_MODEL = _env("DUBBING_TEXT_MODEL", "gpt-4.1-mini")
TTS_MODEL = _env("DUBBING_TTS_MODEL", "gpt-4o-mini-tts")
# Model for the length rewrites only; empty = same as TEXT_MODEL.
REWRITE_MODEL = _env("DUBBING_REWRITE_MODEL", "")
TEXT_TEMPERATURE = 0.3

# Audio sent to the transcription API is cut into chunks (API upload limit).
TRANSCRIBE_CHUNK_SECONDS = 600
TRANSCRIBE_BITRATE = "48k"

# --------------------------------------------------------------------------
# Reliability / speed
# --------------------------------------------------------------------------
API_RETRIES = 4            # attempts per API call
RETRY_BASE_DELAY = 2.0     # seconds, doubled after each failure
SEGMENT_RETRIES = 2        # full re-processing attempts for a failed segment
MAX_WORKERS = _env("DUBBING_MAX_WORKERS", 4, int)   # segments processed in parallel
API_TIMEOUT = 180

# --------------------------------------------------------------------------
# Audio / video output
# --------------------------------------------------------------------------
TTS_SAMPLE_RATE = 24000
OUTPUT_AUDIO_BITRATE = "192k"
OUTPUT_AUDIO_RATE = 48000
VIDEO_MAX_HEIGHT = _env("DUBBING_MAX_HEIGHT", 1080, int)
VIDEO_CRF = 20             # used only when the source video must be re-encoded
VIDEO_PRESET = "medium"
# Mix the original soundtrack under the dub (0.0 = dub only, 0.15 = quiet bed).
ORIGINAL_AUDIO_VOLUME = _env("DUBBING_ORIGINAL_VOLUME", 0.0, float)

# Validation tolerances
DURATION_TOLERANCE = 0.5   # seconds

# --------------------------------------------------------------------------
# Voices
# --------------------------------------------------------------------------
OPENAI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova",
                 "onyx", "sage", "shimmer", "verse"]
# {language name: voice} chosen in Settings; overrides languages.py
VOICE_OVERRIDES = {}

# --------------------------------------------------------------------------
# Price table (USD) used for the spend dashboard.
# These are ESTIMATES. Check https://openai.com/api/pricing and edit them in
# Settings if they change. A model missing here shows "price unknown".
#
#   per_minute      per minute of audio (sent for transcription, generated for TTS)
#   input_per_1m    per 1 million input (text) tokens
#   output_per_1m   per 1 million output (text) tokens
#   per_1m_chars    per 1 million input characters (old tts-1 models)
# --------------------------------------------------------------------------
PRICING = {
    # transcription ("Analysis")
    "whisper-1": {"per_minute": 0.006},
    "gpt-4o-transcribe": {"per_minute": 0.006},
    "gpt-4o-mini-transcribe": {"per_minute": 0.003},
    # text ("Script")
    "gpt-4.1-mini": {"input_per_1m": 0.40, "output_per_1m": 1.60},
    "gpt-4.1": {"input_per_1m": 2.00, "output_per_1m": 8.00},
    "gpt-4.1-nano": {"input_per_1m": 0.10, "output_per_1m": 0.40},
    "gpt-4o-mini": {"input_per_1m": 0.15, "output_per_1m": 0.60},
    "gpt-4o": {"input_per_1m": 2.50, "output_per_1m": 10.00},
    # voice ("Voice")
    "gpt-4o-mini-tts": {"input_per_1m": 0.60, "per_minute": 0.015},
    "tts-1": {"per_1m_chars": 15.00},
    "tts-1-hd": {"per_1m_chars": 30.00},
}
PRICE_FIELDS = ["per_minute", "input_per_1m", "output_per_1m", "per_1m_chars"]
