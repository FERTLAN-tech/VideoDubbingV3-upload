"""
Local web interface (Flask). Serves the single-page app in static/ and a small
JSON API. Only reachable from this computer (127.0.0.1).

Live updates use Server-Sent Events on /api/events; /api/state is the same
data for a simple poll.
"""
import io
import logging
import os
import threading

from flask import Flask, Response, jsonify, request, send_file, send_from_directory, stream_with_context

from . import __version__, config
from .costs import ROLES
from .jobs import JobError, JobManager
from .languages import DEFAULT_LANGUAGE, LANGUAGES
from .pipeline import friendly_error
from .settings import (KEY_FIELDS, SettingsError, load_settings, mask_secrets, public_settings,
                       resolve_key, save_settings, update_settings)

log = logging.getLogger("dubbing")

STATIC_DIR = config.BASE_DIR / "static"
_ALLOWED_HOSTS = ("127.0.0.1", "localhost")


def create_app(manager=None, on_quit=None):
    app = Flask(__name__, static_folder=None)
    app.json.ensure_ascii = False
    manager = manager or JobManager()
    app.config["MANAGER"] = manager

    def error(message, status=400):
        return jsonify({"ok": False, "error": message}), status

    # ---------------------------------------------------------- protection
    @app.before_request
    def local_only():
        host = (request.host or "").rsplit(":", 1)[0].strip("[]")
        if host not in _ALLOWED_HOSTS:          # blocks DNS-rebinding tricks
            return error("Forbidden host.", 403)
        # State-changing calls must carry our header: a normal web page cannot
        # send it to this server without the browser asking first (CORS).
        if request.method in ("POST", "PUT", "DELETE") and request.headers.get("X-Dubbing") != "1":
            return error("Missing X-Dubbing header.", 403)
        return None

    @app.after_request
    def no_cache(resp):
        if request.path.startswith("/api/") and resp.mimetype == "application/json":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(JobError)
    @app.errorhandler(SettingsError)
    def known_error(exc):
        return error(str(exc))

    # ---------------------------------------------------------- pages
    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html", max_age=0)

    @app.get("/static/<path:name>")
    def static_file(name):
        return send_from_directory(STATIC_DIR, name, max_age=0)

    @app.get("/api/meta")
    def meta():
        s = load_settings()
        return jsonify({
            "app_name": config.APP_NAME, "version": __version__, "fake": manager.fake,
            "languages": list(LANGUAGES), "default_language": s.get("last_language") or DEFAULT_LANGUAGE,
            "voices": list(config.OPENAI_VOICES), "ui_lang": s.get("ui_lang", "en"), "roles": list(ROLES),
        })

    # ---------------------------------------------------------- live state
    @app.get("/api/state")
    def state():
        return jsonify(manager.snapshot())

    @app.get("/api/events")
    def events():
        q, snap = manager.subscribe()

        @stream_with_context
        def gen():
            import json
            import queue
            try:
                yield "retry: 2000\n"
                yield f"event: snapshot\ndata: {json.dumps(snap, ensure_ascii=False)}\n\n"
                while True:
                    try:
                        item = q.get(timeout=15)
                    except queue.Empty:
                        yield ": ping\n\n"
                        continue
                    if item is None:
                        break
                    kind, payload = item
                    yield f"event: {kind}\ndata: {payload}\n\n"
            finally:
                manager.unsubscribe(q)

        return Response(gen(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/spend")
    def spend():
        return jsonify(manager.spend())

    @app.post("/api/spend/reset")
    def spend_reset():
        manager.ledger.reset()
        data = manager.spend()
        manager.broadcast("spend", data)
        return jsonify({"ok": True, "spend": data})

    # ---------------------------------------------------------- job
    @app.post("/api/job")
    def start_job():
        data = request.get_json(silent=True) or {}
        try:
            job = manager.start(data.get("url", ""), data.get("language", DEFAULT_LANGUAGE),
                                bool(data.get("review")))
        except JobError:
            raise
        except Exception as exc:  # noqa: BLE001 (bad URL, missing key...)
            return error(friendly_error(exc))
        s = load_settings()
        s["last_language"] = job.language
        save_settings(s)
        return jsonify({"ok": True, "job_id": job.id})

    @app.post("/api/job/cancel")
    def cancel_job():
        manager.cancel()
        return jsonify({"ok": True})

    @app.post("/api/job/review")
    def review_job():
        data = request.get_json(silent=True) or {}
        manager.submit_review(data.get("translations") or {})
        return jsonify({"ok": True})

    @app.delete("/api/job")
    def close_job():
        manager.close_job()
        return jsonify({"ok": True})

    @app.get("/api/job/file/<kind>")
    def job_file(kind):
        path = manager.output_file(kind)
        if not path.exists():
            return error("The file was moved or deleted.", 404)
        inline = request.args.get("inline") == "1" and kind == "mp4"
        return send_file(path, as_attachment=not inline, download_name=path.name, conditional=True)

    @app.post("/api/job/open-folder")
    def open_folder():
        job = manager.current_job()
        if not job.result:
            raise JobError("The video is not ready yet.")
        _open_path(job.result.output_dir)
        return jsonify({"ok": True})

    @app.get("/api/job/segment/<int:i>/audio")
    def segment_audio(i):
        if request.args.get("which") == "original":
            return send_file(io.BytesIO(manager.source_audio_clip(i)), mimetype="audio/wav",
                             download_name=f"segment_{i + 1}_original.wav")
        return send_file(manager.segment_audio(i), mimetype="audio/wav", conditional=True)

    # ---------------------------------------------------------- settings
    @app.get("/api/settings")
    def get_settings():
        return jsonify(public_settings(load_settings()))

    @app.post("/api/settings")
    def post_settings():
        data = request.get_json(silent=True) or {}
        s = update_settings(load_settings(), data)
        save_settings(s)
        return jsonify({"ok": True, "settings": public_settings(s)})

    @app.post("/api/settings/test-key")
    def test_key():
        data = request.get_json(silent=True) or {}
        role = data.get("role")
        if role not in KEY_FIELDS:
            return error("Unknown key.")
        s = load_settings()
        key = (data.get("key") or "").strip() or resolve_key(s, role)[0]
        if not key:
            return error("No key to test. Paste a key first.")
        model = s["models"].get(role) if role in ROLES else None
        try:
            from .engine import test_api_key
            return jsonify({"ok": True, "message": test_api_key(key, model)})
        except Exception as exc:  # noqa: BLE001
            return error(mask_secrets(friendly_error(exc)))

    @app.get("/api/cache")
    def cache_info():
        from .cache import Cache
        s = load_settings()
        stats = Cache(max_bytes=float(s.get("CACHE_MAX_GB") or config.CACHE_MAX_GB) * 1e9).stats()
        return jsonify({"ok": True, **stats, "enabled": bool(s.get("CACHE_ENABLED", True))})

    @app.post("/api/cache/clear")
    def cache_clear():
        from .cache import Cache
        with manager.lock:
            if manager.job and manager.job.running:
                raise JobError("Wait for the current video to finish before clearing the cache.")
        cache = Cache()
        cache.clear()
        return jsonify({"ok": True, **cache.stats()})

    @app.post("/api/settings/browse-output")
    def browse_output():
        current = (request.get_json(silent=True) or {}).get("current") or str(config.OUTPUT_DIR)
        return jsonify({"ok": True, "path": _ask_directory(current)})

    @app.post("/api/settings/open-output")
    def open_output():
        s = load_settings()
        path = (s.get("output_dir") or "").strip() or str(config.BASE_DIR / "output")
        os.makedirs(path, exist_ok=True)
        _open_path(path)
        return jsonify({"ok": True})

    # ---------------------------------------------------------- quit
    @app.post("/api/quit")
    def quit_app():
        def later():
            manager.shutdown()
            if on_quit:
                on_quit()
        threading.Timer(0.3, later).start()
        return jsonify({"ok": True})

    return app


def _open_path(path):
    if hasattr(os, "startfile"):
        os.startfile(str(path))  # noqa: S606 (Windows only)
    else:
        raise JobError(f"Open this folder manually: {path}")


_dialog_lock = threading.Lock()


def _ask_directory(initial):
    """Native folder picker (runs on the server, which is this computer)."""
    if not _dialog_lock.acquire(blocking=False):
        return ""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(parent=root, initialdir=initial, title="Choose the output folder")
        root.destroy()
        return os.path.normpath(path) if path else ""
    except Exception:  # noqa: BLE001
        return ""
    finally:
        _dialog_lock.release()
