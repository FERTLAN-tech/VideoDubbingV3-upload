"""
User settings, saved in %APPDATA%\\VideoDubbingV3\\settings.json (never in the
project folder, so API keys cannot end up in git).

Three API keys, one per job ("role"), plus an optional default key:

    analysis  transcription (Whisper)
    script    translation + rewrites (GPT)
    voice     text-to-speech

Key lookup order for a role: its key in Settings -> env OPENAI_API_KEY_<ROLE>
-> default key in Settings -> env OPENAI_API_KEY.

config.py stays the source of defaults; apply_settings() copies the user's
choices onto config at the start of each job.
"""
import copy
import json
import os
import re
import threading
from pathlib import Path

from . import config
from .languages import LANGUAGES

ROLES = ("analysis", "script", "voice")
KEY_FIELDS = ROLES + ("default",)
ENV_KEYS = {"analysis": "OPENAI_API_KEY_ANALYSIS", "script": "OPENAI_API_KEY_SCRIPT",
            "voice": "OPENAI_API_KEY_VOICE", "default": "OPENAI_API_KEY"}

# Defaults captured once from config.py (before any settings are applied)
_DEFAULTS = {
    "models": {"analysis": config.TRANSCRIBE_MODEL, "script": config.TEXT_MODEL, "voice": config.TTS_MODEL,
               "rewrite": config.REWRITE_MODEL},
    "MAX_SPEED_UP": config.MAX_SPEED_UP,
    "MAX_SLOW_DOWN": config.MAX_SLOW_DOWN,
    "SAFETY_GAP": config.SAFETY_GAP,
    "REWRITE_TRIES": config.REWRITE_TRIES,
    "ORIGINAL_AUDIO_VOLUME": config.ORIGINAL_AUDIO_VOLUME,
    "ECONOMY_SPEEDUP_LIMIT": config.ECONOMY_SPEEDUP_LIMIT,
    "ECONOMY_SLOWDOWN_LIMIT": config.ECONOMY_SLOWDOWN_LIMIT,
    "CACHE_MAX_GB": config.CACHE_MAX_GB,
    "ECONOMY_MODE": config.ECONOMY_MODE,
    "CACHE_ENABLED": config.CACHE_ENABLED,
    "MAX_SPEND_PER_VIDEO": config.MAX_SPEND_PER_VIDEO,
    "OUTPUT_DIR": str(config.OUTPUT_DIR),
    "PRICING": copy.deepcopy(config.PRICING),
}

# name: (min, max, cast)
NUMBER_LIMITS = {
    "MAX_SPEED_UP": (1.0, 2.0, float),
    "MAX_SLOW_DOWN": (0.5, 1.0, float),
    "SAFETY_GAP": (0.0, 0.5, float),
    "REWRITE_TRIES": (0, 5, int),
    "ORIGINAL_AUDIO_VOLUME": (0.0, 1.0, float),
    "ECONOMY_SPEEDUP_LIMIT": (1.0, 2.0, float),
    "ECONOMY_SLOWDOWN_LIMIT": (0.5, 1.0, float),
    "CACHE_MAX_GB": (0.1, 100.0, float),
}
# on / off switches
BOOL_FIELDS = ("ECONOMY_MODE", "CACHE_ENABLED")
# "Max spend per video": empty = no cap
MAX_SPEND_LIMITS = (0.01, 10000.0)

_lock = threading.Lock()


def app_data_dir():
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "VideoDubbingV3"


def settings_path():
    return app_data_dir() / "settings.json"


def usage_path():
    return app_data_dir() / "usage.jsonl"


def default_settings():
    return {
        "keys": {k: "" for k in KEY_FIELDS},
        "models": dict(_DEFAULTS["models"]),
        "voices": {name: lang["voice"] for name, lang in LANGUAGES.items()},
        "MAX_SPEED_UP": _DEFAULTS["MAX_SPEED_UP"],
        "MAX_SLOW_DOWN": _DEFAULTS["MAX_SLOW_DOWN"],
        "SAFETY_GAP": _DEFAULTS["SAFETY_GAP"],
        "REWRITE_TRIES": _DEFAULTS["REWRITE_TRIES"],
        "ORIGINAL_AUDIO_VOLUME": _DEFAULTS["ORIGINAL_AUDIO_VOLUME"],
        "ECONOMY_MODE": _DEFAULTS["ECONOMY_MODE"],
        "ECONOMY_SPEEDUP_LIMIT": _DEFAULTS["ECONOMY_SPEEDUP_LIMIT"],
        "ECONOMY_SLOWDOWN_LIMIT": _DEFAULTS["ECONOMY_SLOWDOWN_LIMIT"],
        "MAX_SPEND_PER_VIDEO": _DEFAULTS["MAX_SPEND_PER_VIDEO"],   # None = no cap
        "CACHE_ENABLED": _DEFAULTS["CACHE_ENABLED"],
        "CACHE_MAX_GB": _DEFAULTS["CACHE_MAX_GB"],
        "output_dir": "",                 # empty = default (project\output)
        "pricing": {},                    # user overrides of config.PRICING
        "pricing_removed": [],            # config.PRICING models the user deleted
        "ui_lang": "en",
        "last_language": "",
    }


def _merge(base, data):
    for key, value in (data or {}).items():
        if key not in base:
            continue
        if isinstance(base[key], dict) and isinstance(value, dict) and key != "pricing":
            for k, v in value.items():
                if k in base[key] or key == "voices":
                    base[key][k] = v
        else:
            base[key] = value
    return base


def load_settings():
    s = default_settings()
    path = settings_path()
    if path.exists():
        try:
            _merge(s, json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass                          # a broken file falls back to defaults
    return s


def save_settings(s):
    path = settings_path()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    return path


# --------------------------------------------------------------------------
# Keys
# --------------------------------------------------------------------------
def mask_key(key):
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= 10:
        return "*" * len(key)
    return f"{key[:3]}...{key[-4:]}"


_KEY_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


def mask_secrets(text):
    """Hide anything that looks like an OpenAI key (used on every log line)."""
    return _KEY_RE.sub(lambda m: mask_key(m.group(0)), str(text))


def resolve_key(s, role):
    """(key, source) for a role. source: settings / env name / default / missing."""
    keys = s.get("keys", {})
    if (keys.get(role) or "").strip():
        return keys[role].strip(), "settings"
    env = os.environ.get(ENV_KEYS[role], "").strip()
    if env:
        return env, ENV_KEYS[role]
    if role != "default":
        if (keys.get("default") or "").strip():
            return keys["default"].strip(), "default key"
        env = os.environ.get("OPENAI_API_KEY", "").strip()
        if env:
            return env, "OPENAI_API_KEY"
    return "", "missing"


def resolve_keys(s):
    return {role: resolve_key(s, role)[0] for role in ROLES}


def public_settings(s):
    """Settings as sent to the browser: keys masked, never in clear."""
    out = copy.deepcopy(s)
    out["keys"] = {k: mask_key(v) for k, v in s.get("keys", {}).items()}
    out["key_sources"] = {}
    for role in KEY_FIELDS:
        key, source = resolve_key(s, role)
        out["key_sources"][role] = {"source": source, "masked": mask_key(key)}
    out["defaults"] = {"models": _DEFAULTS["models"], "output_dir": _DEFAULTS["OUTPUT_DIR"],
                       **{k: _DEFAULTS[k] for k in NUMBER_LIMITS}, **{k: _DEFAULTS[k] for k in BOOL_FIELDS},
                       "MAX_SPEND_PER_VIDEO": _DEFAULTS["MAX_SPEND_PER_VIDEO"]}
    out["pricing_effective"] = effective_pricing(s)
    out["price_fields"] = list(config.PRICE_FIELDS)
    out["limits"] = {k: [lo, hi] for k, (lo, hi, _) in NUMBER_LIMITS.items()}
    out["limits"]["MAX_SPEND_PER_VIDEO"] = list(MAX_SPEND_LIMITS)
    out["voices_available"] = list(config.OPENAI_VOICES)
    out["paths"] = {"settings": str(settings_path()), "usage": str(usage_path()),
                    "cache": str(app_data_dir() / "cache")}
    return out


class SettingsError(ValueError):
    pass


def update_settings(s, data):
    """Apply a change request from the UI. Keys are only replaced when a new value is typed."""
    s = copy.deepcopy(s)
    for role, value in (data.get("keys") or {}).items():
        if role in KEY_FIELDS and isinstance(value, str) and value.strip():
            value = value.strip()
            if any(c.isspace() for c in value):
                raise SettingsError("An API key cannot contain spaces.")
            s["keys"][role] = value
    for role in data.get("clear_keys") or []:
        if role in KEY_FIELDS:
            s["keys"][role] = ""
    for role, value in (data.get("models") or {}).items():
        if role in ROLES and isinstance(value, str):
            s["models"][role] = value.strip() or _DEFAULTS["models"][role]
        elif role == "rewrite" and isinstance(value, str):     # empty = same as the script model
            s["models"]["rewrite"] = value.strip()
    for name in BOOL_FIELDS:
        if name in data and data[name] is not None:
            s[name] = data[name] in (True, 1, "1", "true", "on")
    if "MAX_SPEND_PER_VIDEO" in data:
        value = data["MAX_SPEND_PER_VIDEO"]
        if value in (None, "") or (isinstance(value, str) and not value.strip()):
            s["MAX_SPEND_PER_VIDEO"] = None
        else:
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise SettingsError("Max spend per video must be a number (or empty for no limit).") from None
            lo, hi = MAX_SPEND_LIMITS
            if not lo <= value <= hi:
                raise SettingsError(f"Max spend per video must be between {lo} and {hi} (or empty).")
            s["MAX_SPEND_PER_VIDEO"] = value
    for lang, voice in (data.get("voices") or {}).items():
        if lang in LANGUAGES:
            if voice not in config.OPENAI_VOICES:
                raise SettingsError(f"Unknown voice: {voice}")
            s["voices"][lang] = voice
    for name, (lo, hi, cast) in NUMBER_LIMITS.items():
        if name in data and data[name] not in (None, ""):
            try:
                value = cast(float(data[name]))
            except (TypeError, ValueError):
                raise SettingsError(f"{name} must be a number.") from None
            if not lo <= value <= hi:
                raise SettingsError(f"{name} must be between {lo} and {hi}.")
            s[name] = value
    if "output_dir" in data:
        s["output_dir"] = (data.get("output_dir") or "").strip()
    if "pricing" in data and isinstance(data["pricing"], dict):
        pricing = {}
        for model, fields in data["pricing"].items():
            model = str(model).strip()
            if not model or not isinstance(fields, dict):
                continue
            entry = {}
            for f, v in fields.items():
                if f in config.PRICE_FIELDS and v not in (None, ""):
                    try:
                        v = float(v)
                    except (TypeError, ValueError):
                        raise SettingsError(f"Price for {model} must be a number.") from None
                    if v < 0:
                        raise SettingsError(f"Price for {model} cannot be negative.")
                    entry[f] = v
            pricing[model] = entry
        # store only the differences from config.py
        s["pricing"] = {m: e for m, e in pricing.items() if _DEFAULTS["PRICING"].get(m) != e}
        s["pricing_removed"] = [m for m in _DEFAULTS["PRICING"] if m not in pricing]
    for k in ("ui_lang", "last_language"):
        if isinstance(data.get(k), str):
            s[k] = data[k]
    return s


def effective_pricing(s):
    pricing = copy.deepcopy(_DEFAULTS["PRICING"])
    for m in s.get("pricing_removed") or []:
        pricing.pop(m, None)
    for model, entry in (s.get("pricing") or {}).items():
        pricing[model] = dict(entry)
    return pricing


def apply_settings(s):
    """Copy the user's settings onto config (called at the start of each job)."""
    models = s.get("models", {})
    config.TRANSCRIBE_MODEL = models.get("analysis") or _DEFAULTS["models"]["analysis"]
    config.TEXT_MODEL = models.get("script") or _DEFAULTS["models"]["script"]
    config.TTS_MODEL = models.get("voice") or _DEFAULTS["models"]["voice"]
    config.REWRITE_MODEL = (models.get("rewrite") or "").strip()
    for name in NUMBER_LIMITS:
        setattr(config, name, s.get(name, _DEFAULTS[name]))
    for name in BOOL_FIELDS:
        setattr(config, name, bool(s.get(name, _DEFAULTS[name])))
    config.MAX_SPEND_PER_VIDEO = s.get("MAX_SPEND_PER_VIDEO")
    out = (s.get("output_dir") or "").strip()
    config.OUTPUT_DIR = Path(out) if out else Path(_DEFAULTS["OUTPUT_DIR"])
    config.LOG_DIR = config.OUTPUT_DIR / "logs"
    config.VOICE_OVERRIDES = {k: v for k, v in (s.get("voices") or {}).items() if v in config.OPENAI_VOICES}
    config.PRICING = effective_pricing(s)
    return config
