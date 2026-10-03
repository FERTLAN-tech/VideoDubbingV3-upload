"""
Target-language table.

To add a language, add one entry here: nothing else in the code needs to change.

  code               ISO code, used in file names
  voice              OpenAI TTS voice
  words_per_second   typical voice-over speaking rate, used to give the
                     translator a length budget
  chars_per_second   typical TTS speaking rate in characters (spaces included),
                     used to predict a line's length before paying for its voice.
                     Only a starting point: it is calibrated during each job.
  tts_instructions   speaking style sent to the TTS model (sent with every voice
                     call, so keep it short)
"""

_STYLE = "Natural, clear {} voice-over. Neutral, warm tone."

LANGUAGES = {
    "French": {
        "code": "fr", "voice": "alloy", "words_per_second": 2.6, "chars_per_second": 14.5,
        "tts_instructions": _STYLE.format("French (France)"),
    },
    "English": {
        "code": "en", "voice": "alloy", "words_per_second": 2.6, "chars_per_second": 14.5,
        "tts_instructions": _STYLE.format("English"),
    },
    "Spanish": {
        "code": "es", "voice": "alloy", "words_per_second": 2.9, "chars_per_second": 15.5,
        "tts_instructions": _STYLE.format("Spanish"),
    },
    "German": {
        "code": "de", "voice": "alloy", "words_per_second": 2.3, "chars_per_second": 14.5,
        "tts_instructions": _STYLE.format("German"),
    },
    "Dutch": {
        "code": "nl", "voice": "alloy", "words_per_second": 2.5, "chars_per_second": 14.5,
        "tts_instructions": _STYLE.format("Dutch"),
    },
    "Italian": {
        "code": "it", "voice": "alloy", "words_per_second": 2.8, "chars_per_second": 15.0,
        "tts_instructions": _STYLE.format("Italian"),
    },
    "Portuguese": {
        "code": "pt", "voice": "alloy", "words_per_second": 2.7, "chars_per_second": 15.0,
        "tts_instructions": _STYLE.format("Portuguese"),
    },
    "Arabic": {
        "code": "ar", "voice": "alloy", "words_per_second": 2.2, "chars_per_second": 12.0,
        "tts_instructions": _STYLE.format("Modern Standard Arabic"),
    },
}

DEFAULT_LANGUAGE = "French"


def get_language(name):
    if name not in LANGUAGES:
        raise ValueError(f"Unsupported language: {name}. Choose one of: {', '.join(LANGUAGES)}")
    from . import config
    cfg = dict(LANGUAGES[name])
    cfg["name"] = name
    cfg["voice"] = config.VOICE_OVERRIDES.get(name) or cfg["voice"]   # chosen in Settings
    return cfg
