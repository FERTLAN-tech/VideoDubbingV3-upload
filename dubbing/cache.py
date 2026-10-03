"""
Persistent, content-addressed cache of everything that costs money:

    transcribe   raw transcript segments of a video (YouTube id or audio hash)
    translate    translation of one segment (source text + context + language + model + prompt)
    rewrite      length rewrite of one line
    tts          normalized voice clip (WAV) of one line (text + voice + model + style)

Folder: %APPDATA%\\VideoDubbingV3\\cache\\<kind>\\<2 first chars>\\<key>.json (+ .wav).
A key is the SHA-256 of every input that changes the result, so a different
model, voice, language or prompt version never reuses a wrong result.

Each entry also remembers what producing it cost (model + units), so a cache
hit can report the money it saved. The folder is capped (CACHE_MAX_GB): when it
grows past the cap, the least recently used entries are deleted.
"""
import hashlib
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path

from . import config

log = logging.getLogger("dubbing")


def make_key(*parts):
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def file_hash(path, block=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_dir():
    from .settings import app_data_dir
    return app_data_dir() / "cache"


class Cache:
    """Thread-safe disk cache. Every method swallows disk errors: the cache may
    make a job cheaper, never make it fail."""

    def __init__(self, root=None, max_bytes=None):
        self.root = Path(root) if root else cache_dir()
        self.max_bytes = int(config.CACHE_MAX_GB * 1e9 if max_bytes is None else max_bytes)
        self.lock = threading.Lock()
        self._size = None              # computed lazily, then kept up to date

    # ------------------------------------------------------------ paths
    def _base(self, kind, key):
        return self.root / kind / key[:2] / key

    @staticmethod
    def _touch(*paths):
        now = time.time()
        for p in paths:
            try:
                os.utime(p, (now, now))
            except OSError:
                pass

    def _write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return path.stat().st_size

    # ------------------------------------------------------------ values
    def get(self, kind, key):
        """{"value": ..., "meta": {...}} or None."""
        path = self._base(kind, key).with_suffix(".json")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or "value" not in data:
            return None
        self._touch(path)
        return data

    def put(self, kind, key, value, meta=None):
        try:
            size = self._write_json(self._base(kind, key).with_suffix(".json"),
                                    {"value": value, "meta": meta or {}, "created": time.time()})
            self._grew(size)
        except OSError as exc:
            log.debug("cache write failed: %s", exc)

    # ------------------------------------------------------------ files
    def get_file(self, kind, key, dest, suffix=".wav"):
        """Copy a cached file to `dest`. Returns its meta dict, or None on a miss."""
        base = self._base(kind, key)
        data_path, meta_path = base.with_suffix(suffix), base.with_suffix(".json")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not data_path.exists() or data_path.stat().st_size < 45:
                return None
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(data_path, dest)
        except (OSError, ValueError):
            return None
        self._touch(data_path, meta_path)
        return meta.get("meta", {}) if isinstance(meta, dict) else {}

    def put_file(self, kind, key, src, meta=None, suffix=".wav"):
        base = self._base(kind, key)
        data_path = base.with_suffix(suffix)
        try:
            data_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = data_path.with_name(data_path.name + f".{threading.get_ident()}.tmp")
            shutil.copyfile(src, tmp)
            tmp.replace(data_path)
            size = data_path.stat().st_size
            size += self._write_json(base.with_suffix(".json"), {"value": data_path.name, "meta": meta or {},
                                                                 "created": time.time()})
            self._grew(size)
        except OSError as exc:
            log.debug("cache write failed: %s", exc)

    # ------------------------------------------------------------ size / cleanup
    def _files(self):
        if not self.root.exists():
            return []
        out = []
        for p in self.root.rglob("*"):
            try:
                if p.is_file():
                    st = p.stat()
                    out.append((st.st_mtime, st.st_size, p))
            except OSError:
                pass
        return out

    def size(self):
        with self.lock:
            self._size = sum(s for _, s, _ in self._files())
            return self._size

    def stats(self):
        files = self._files()
        return {"path": str(self.root), "bytes": sum(s for _, s, _ in files),
                "entries": sum(1 for _, _, p in files if p.suffix == ".json"),
                "max_bytes": self.max_bytes}

    def _grew(self, size):
        with self.lock:
            if self._size is None:
                self._size = sum(s for _, s, _ in self._files())
            else:
                self._size += size
            over = self._size > self.max_bytes
        if over:
            self.cleanup()

    def cleanup(self, target=0.9):
        """Delete the least recently used files until the cache is under target x cap."""
        with self.lock:
            files = sorted(self._files())                 # oldest access first
            total = sum(s for _, s, _ in files)
            limit = self.max_bytes * target
            removed = 0
            for _, size, path in files:
                if total <= limit:
                    break
                try:
                    path.unlink()
                    total -= size
                    removed += 1
                except OSError:
                    pass
            self._size = total
        if removed:
            log.info("Cache cleanup: %d old files removed (cache size %.0f MB)", removed, total / 1e6)
        return removed

    def clear(self):
        with self.lock:
            if self.root.exists():
                shutil.rmtree(self.root, ignore_errors=True)
            self._size = 0


def default_cache():
    """The persistent cache, or None when it is turned off in Settings."""
    if not config.CACHE_ENABLED:
        return None
    return Cache(cache_dir(), int(config.CACHE_MAX_GB * 1e9))
