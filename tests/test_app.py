"""
Offline tests for the interface layer (no internet, no API key):

* spend tracking: price math, thread safety, ledger persistence, reset
* settings: save / load, key masking, key lookup order, validation
* end-to-end: the Flask backend with the fake engine (DUBBING_FAKE_ENGINE=1),
  driven over HTTP exactly like the browser does (review pause, live events,
  downloads, clip playback, spend endpoints, cancel, clean-up).

Everything uses a temporary APPDATA folder, so your real settings are untouched.

Run:  .venv\\Scripts\\python.exe -m tests.test_app
"""
import json
import logging
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TMP = Path(tempfile.mkdtemp(prefix="vdub_apptest_"))
os.environ["APPDATA"] = str(TMP / "appdata")
for _name in ("OPENAI_API_KEY", "OPENAI_API_KEY_ANALYSIS", "OPENAI_API_KEY_SCRIPT", "OPENAI_API_KEY_VOICE"):
    os.environ.pop(_name, None)

from dubbing import config  # noqa: E402
from dubbing import settings as st  # noqa: E402
from dubbing.costs import CostTracker, UsageLedger, estimate_cost  # noqa: E402
from dubbing.ffmpeg_utils import prepare_path  # noqa: E402


def close(a, b, eps=1e-9):
    return abs(a - b) <= eps


# ---------------------------------------------------------------- costs
def test_cost_math():
    assert close(estimate_cost("whisper-1", {"audio_minutes": 10}), 0.06)
    assert close(estimate_cost("gpt-4.1-mini", {"input_tokens": 1_000_000, "output_tokens": 1_000_000}), 2.0)
    assert close(estimate_cost("gpt-4.1-mini", {"input_tokens": 2500, "output_tokens": 500}), 0.0018)
    # TTS: 1 minute of audio + 4000 characters (~1000 tokens)
    assert close(estimate_cost("gpt-4o-mini-tts", {"audio_minutes": 1, "input_tokens": 1000, "chars": 4000}), 0.0156)
    assert close(estimate_cost("tts-1", {"chars": 1_000_000}), 15.0)
    assert estimate_cost("my-unknown-model", {"input_tokens": 100}) is None
    # dated variants use the base price; the longest name wins
    assert close(estimate_cost("gpt-4.1-mini-2025-04-14", {"input_tokens": 1_000_000}), 0.40)
    assert close(estimate_cost("gpt-4o-mini-tts-2025-03-20", {"audio_minutes": 2}), 0.03)

    tr = CostTracker(None, "job1")
    tr.transcription("whisper-1", 90)                       # 1.5 min -> $0.009
    tr.chat("gpt-4.1-mini", {"prompt_tokens": 1000, "completion_tokens": 250})   # 0.0004 + 0.0004
    tr.tts("gpt-4o-mini-tts", "a" * 400, 30)                # 0.0075 + 100 tok * 0.6/1M
    tr.chat("mystery-model", {"prompt_tokens": 10, "completion_tokens": 10})
    s = tr.job_summary()
    assert close(s["analysis"]["cost"], 0.009, 1e-6), s
    assert close(s["script"]["cost"], 0.0008, 1e-6) and s["script"]["unknown"] == 1 and s["script"]["calls"] == 2
    assert close(s["voice"]["cost"], 0.00756, 1e-6), s
    assert close(s["total"]["cost"], 0.009 + 0.0008 + 0.00756, 1e-6) and s["total"]["unknown"] == 1
    print("OK  cost math")


def test_ledger_persistence_and_threads():
    path = TMP / "ledger_test" / "usage.jsonl"
    ledger = UsageLedger(path)
    seen = []
    tracker = CostTracker(ledger, "jobA", on_record=seen.append)

    def worker(n):
        for _ in range(50):
            tracker.chat("gpt-4.1-mini", {"prompt_tokens": 1000, "completion_tokens": 1000})
            tracker.tts("gpt-4o-mini-tts", "x" * 40, 6.0)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(seen) == 800 and len(tracker.entries) == 800

    # an entry from another month and a broken line must be handled
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "2020-01-05T10:00:00", "job_id": "old", "role": "analysis",
                             "model": "whisper-1", "units": {"audio_minutes": 100}, "cost": 0.6}) + "\n")
        fh.write("{broken json\n")

    reloaded = UsageLedger(path)
    assert len(reloaded.entries) == 801, len(reloaded.entries)
    s = reloaded.summary("jobA")
    script = 400 * (1000 * 0.40 + 1000 * 1.60) / 1e6
    voice = 400 * (0.1 * 0.015 + 10 * 0.60 / 1e6)
    assert close(s["job"]["script"]["cost"], script, 1e-6), s["job"]
    assert close(s["job"]["voice"]["cost"], voice, 1e-6), s["job"]
    assert s["job"]["analysis"]["calls"] == 0
    assert close(s["all"]["analysis"]["cost"], 0.6, 1e-6)
    if datetime.now().strftime("%Y-%m") != "2020-01":
        assert s["month"]["analysis"]["calls"] == 0
    assert close(s["all"]["total"]["cost"], script + voice + 0.6, 1e-5)

    reloaded.reset()
    assert reloaded.summary("jobA")["all"]["total"]["calls"] == 0
    assert not path.exists() and list(path.parent.glob("usage-reset-*.jsonl.bak"))
    assert len(UsageLedger(path).entries) == 0
    print("OK  ledger: 800 parallel records, reload, month filter, reset")


# ---------------------------------------------------------------- settings
def test_settings():
    path = st.settings_path()
    assert str(path).startswith(str(TMP)), path
    assert "VideoDubbingV3" in str(path) and config.BASE_DIR not in path.parents
    s = st.load_settings()
    assert s["models"]["voice"] == "gpt-4o-mini-tts" and s["keys"]["voice"] == ""
    assert st.resolve_key(s, "voice") == ("", "missing")

    key_v, key_d = "sk-proj-VOICE1234567890abcd", "sk-DEFAULTkey0987654321wxyz"
    s = st.update_settings(s, {"keys": {"voice": key_v, "default": key_d, "script": "   "},
                               "models": {"script": "gpt-4.1"}, "voices": {"French": "nova"},
                               "MAX_SPEED_UP": "1.4", "REWRITE_TRIES": 3})
    st.save_settings(s)
    s2 = st.load_settings()
    assert s2["keys"]["voice"] == key_v and s2["keys"]["script"] == ""
    assert s2["MAX_SPEED_UP"] == 1.4 and s2["REWRITE_TRIES"] == 3 and s2["voices"]["French"] == "nova"

    # key lookup order
    assert st.resolve_key(s2, "voice") == (key_v, "settings")
    assert st.resolve_key(s2, "script") == (key_d, "default key")
    os.environ["OPENAI_API_KEY_SCRIPT"] = "sk-ENVSCRIPT11112222333344"
    try:
        assert st.resolve_key(s2, "script") == ("sk-ENVSCRIPT11112222333344", "OPENAI_API_KEY_SCRIPT")
    finally:
        del os.environ["OPENAI_API_KEY_SCRIPT"]
    assert st.resolve_keys(s2) == {"analysis": key_d, "script": key_d, "voice": key_v}

    # masking: the browser never receives a full key
    pub = st.public_settings(s2)
    dumped = json.dumps(pub)
    assert key_v not in dumped and key_d not in dumped
    assert pub["keys"]["voice"] == "sk-...abcd" and pub["key_sources"]["analysis"]["source"] == "default key"
    assert st.mask_key("short") == "*****" and st.mask_key("") == ""
    assert st.mask_secrets(f"error with {key_v} inside") == "error with sk-...abcd inside"

    # an empty value does not erase a saved key; clear_keys does
    s3 = st.update_settings(s2, {"keys": {"voice": ""}})
    assert s3["keys"]["voice"] == key_v
    s3 = st.update_settings(s3, {"clear_keys": ["voice"]})
    assert s3["keys"]["voice"] == ""

    for bad in ({"MAX_SPEED_UP": 3}, {"MAX_SLOW_DOWN": "abc"}, {"voices": {"French": "robot"}},
                {"keys": {"voice": "sk-with space"}}, {"pricing": {"x": {"per_minute": -1}}}):
        try:
            st.update_settings(s2, bad)
        except st.SettingsError:
            pass
        else:
            raise AssertionError(f"accepted invalid settings {bad}")

    # pricing: override, add, remove
    eff = st.effective_pricing(s2)
    eff["gpt-4.1-mini"] = {"input_per_1m": 0.5, "output_per_1m": 2.0}
    eff["my-model"] = {"input_per_1m": 1}
    del eff["tts-1-hd"]
    s4 = st.update_settings(s2, {"pricing": eff})
    assert set(s4["pricing"]) == {"gpt-4.1-mini", "my-model"} and s4["pricing_removed"] == ["tts-1-hd"]
    eff4 = st.effective_pricing(s4)
    assert eff4["gpt-4.1-mini"]["input_per_1m"] == 0.5 and "tts-1-hd" not in eff4 and "whisper-1" in eff4

    # money-saving settings: switches, optional budget, rewrite model
    assert s2["ECONOMY_MODE"] is True and s2["CACHE_ENABLED"] is True and s2["MAX_SPEND_PER_VIDEO"] is None
    s5 = st.update_settings(s4, {"ECONOMY_MODE": False, "CACHE_ENABLED": False, "MAX_SPEND_PER_VIDEO": "2.5",
                                 "ECONOMY_SPEEDUP_LIMIT": "1.2", "CACHE_MAX_GB": 5,
                                 "models": {"rewrite": "gpt-4.1-nano"}})
    assert s5["ECONOMY_MODE"] is False and s5["CACHE_ENABLED"] is False and s5["MAX_SPEND_PER_VIDEO"] == 2.5
    assert s5["models"]["rewrite"] == "gpt-4.1-nano" and s5["ECONOMY_SPEEDUP_LIMIT"] == 1.2
    assert st.update_settings(s5, {"MAX_SPEND_PER_VIDEO": ""})["MAX_SPEND_PER_VIDEO"] is None
    for bad in ({"MAX_SPEND_PER_VIDEO": "abc"}, {"MAX_SPEND_PER_VIDEO": 0}, {"ECONOMY_SLOWDOWN_LIMIT": 1.5}):
        try:
            st.update_settings(s5, bad)
        except st.SettingsError:
            pass
        else:
            raise AssertionError(f"accepted invalid settings {bad}")
    pub5 = st.public_settings(s5)
    assert pub5["limits"]["MAX_SPEND_PER_VIDEO"] and pub5["defaults"]["ECONOMY_MODE"] is True

    # applied to config at job start; voices reach get_language
    saved = (config.TEXT_MODEL, config.MAX_SPEED_UP, config.OUTPUT_DIR, dict(config.PRICING))
    try:
        st.apply_settings(s5)
        assert config.ECONOMY_MODE is False and config.CACHE_ENABLED is False
        assert config.MAX_SPEND_PER_VIDEO == 2.5 and config.REWRITE_MODEL == "gpt-4.1-nano"
        assert config.ECONOMY_SPEEDUP_LIMIT == 1.2 and config.CACHE_MAX_GB == 5
        st.apply_settings(st.update_settings(s4, {"output_dir": str(TMP / "outdir")}))
        from dubbing.languages import get_language
        assert config.TEXT_MODEL == "gpt-4.1" and config.MAX_SPEED_UP == 1.4
        assert config.ECONOMY_MODE is True and config.MAX_SPEND_PER_VIDEO is None and config.REWRITE_MODEL == ""
        assert config.OUTPUT_DIR == TMP / "outdir" and config.LOG_DIR == TMP / "outdir" / "logs"
        assert get_language("French")["voice"] == "nova" and get_language("German")["voice"] == "alloy"
        assert config.PRICING["gpt-4.1-mini"]["input_per_1m"] == 0.5
    finally:
        st.apply_settings(st.default_settings())
        config.TEXT_MODEL, config.MAX_SPEED_UP, config.OUTPUT_DIR, config.PRICING = saved
    print("OK  settings: save/load, key order, masking, validation, pricing, apply")


def test_log_masking():
    from dubbing.logging_setup import setup_logging
    old = config.LOG_DIR
    config.LOG_DIR = TMP / "logs"
    try:
        path = setup_logging()
        logging.getLogger("dubbing").info("using key %s now", "sk-SECRETsecret1234567890zz")
        logging.getLogger("dubbing").handlers[0].flush()
        text = path.read_text(encoding="utf-8")
        assert "SECRETsecret" not in text and "sk-...90zz" in text, text
    finally:
        for h in list(logging.getLogger("dubbing").handlers):
            logging.getLogger("dubbing").removeHandler(h)
            h.close()
        config.LOG_DIR = old
    print("OK  API keys are masked in logs")


# ---------------------------------------------------------------- HTTP
class Http:
    def __init__(self, base):
        self.base = base

    def req(self, method, path, body=None, header=True, raw=False):
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(self.base + path, data=data, method=method)
        if header:
            r.add_header("X-Dubbing", "1")
        if body is not None:
            r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                content = resp.read()
                return resp.status, (content, dict(resp.headers)) if raw else json.loads(content or b"{}")
        except urllib.error.HTTPError as e:
            content = e.read()
            try:
                return e.code, json.loads(content)
            except ValueError:
                return e.code, content

    def wait_status(self, wanted, timeout=180):
        end = time.time() + timeout
        while time.time() < end:
            _, s = self.req("GET", "/api/state")
            job = s["job"]
            if job and job["status"] in wanted:
                return job
            if job and job["status"] == "failed":
                raise AssertionError("job failed: " + job["error"])
            time.sleep(0.25)
        raise AssertionError(f"timeout waiting for {wanted}")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SseReader(threading.Thread):
    """Reads /api/events like the browser and records the event types."""

    def __init__(self, url):
        super().__init__(daemon=True)
        self.url = url
        self.events = []
        self.stop = False

    def run(self):
        try:
            with urllib.request.urlopen(self.url, timeout=60) as resp:
                kind = None
                for raw in resp:
                    line = raw.decode("utf-8").rstrip("\n").rstrip("\r")
                    if line.startswith("event: "):
                        kind = line[7:]
                    elif line.startswith("data: ") and kind:
                        self.events.append((kind, json.loads(line[6:])))
                        if kind == "status" and self.events[-1][1]["status"] == "done":
                            return
                    if self.stop:
                        return
        except Exception as exc:  # noqa: BLE001
            self.events.append(("error", str(exc)))


def test_backend_end_to_end():
    os.environ["DUBBING_FAKE_ENGINE"] = "1"
    prepare_path()
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    from werkzeug.serving import make_server

    from dubbing.jobs import JobManager
    from dubbing.web import create_app

    out_dir = TMP / "output dir é"
    s = st.default_settings()
    s["output_dir"] = str(out_dir)
    st.save_settings(s)

    manager = JobManager()
    assert manager.fake
    app = create_app(manager)
    port = _free_port()
    server = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    http = Http(f"http://127.0.0.1:{port}")
    try:
        # ---- page + meta + protections
        code, (page, _) = http.req("GET", "/", raw=True)
        assert code == 200 and b"Video Dubbing" in page and b"/static/app.js" in page
        for asset in ("/static/app.js", "/static/app.css"):
            assert http.req("GET", asset, raw=True)[0] == 200
        code, meta = http.req("GET", "/api/meta")
        assert code == 200 and meta["fake"] is True and "French" in meta["languages"]
        assert http.req("POST", "/api/job", {"url": "x"}, header=False)[0] == 403
        code, err = http.req("POST", "/api/job", {"url": "not a url", "language": "French"})
        assert code == 400 and "YouTube" in err["error"], err
        code, spend0 = http.req("GET", "/api/spend")
        assert code == 200 and spend0["all"]["total"]["calls"] == 0

        # ---- job with the review pause
        sse = SseReader(http.base + "/api/events")
        sse.start()
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        code, res = http.req("POST", "/api/job", {"url": url, "language": "French", "review": True})
        assert code == 200 and res["ok"], res
        assert http.req("POST", "/api/job", {"url": url, "language": "French"})[0] == 400   # one at a time

        job = http.wait_status({"review"})
        segs = job["segments"]
        assert len(segs) == 8 and all(r["translated_text"] for r in segs), segs
        stages = {x["key"]: x["status"] for x in job["stages"]}
        assert all(stages[k] == "done" for k in ("download", "probe", "extract", "transcribe")), stages
        assert job["video"]["width"] == 640 and abs(job["video"]["duration"] - 40) < 0.1
        edited = "é" * 52                                   # ~3.5 s: a perfect fit for segment 1
        code, _ = http.req("POST", "/api/job/review", {"translations": {"0": edited}})
        assert code == 200

        job = http.wait_status({"done"})
        sse.join(timeout=30)
        assert all(x["status"] == "done" for x in job["stages"]), job["stages"]
        assert all(x["duration"] is not None for x in job["stages"])
        segs = job["segments"]
        assert segs[0]["translated_text"] == edited
        assert all(r["status"] == "done" and r["timing_mode"] for r in segs)
        modes = {r["index"]: r["timing_mode"] for r in segs}
        assert modes[3] == "speed_up" and segs[2]["warnings"], modes
        assert any("FINAL VIDEO READY" in e["line"] for e in job["logs"])
        result = job["result"]
        assert Path(result["video_path"]).exists() and out_dir in Path(result["video_path"]).parents

        kinds = [k for k, _ in sse.events]
        assert kinds and kinds[0] == "snapshot", kinds[:5]
        for k in ("stage", "progress", "log", "video", "segments", "segment", "spend", "status"):
            assert k in kinds, (k, sorted(set(kinds)))

        # ---- downloads
        for kind, magic in (("mp4", b"ftyp"), ("srt", b"-->"), ("json", b"segments")):
            code, (content, headers) = http.req("GET", f"/api/job/file/{kind}", raw=True)
            assert code == 200 and magic in content[:4000], kind
            assert headers.get("Content-Disposition", "").startswith("attachment"), headers
        code, (_, headers) = http.req("GET", "/api/job/file/mp4?inline=1", raw=True)
        assert "attachment" not in headers.get("Content-Disposition", "")
        report = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))
        cost = report["cost_estimate_usd"]
        assert cost["total"]["cost"] > 0 and cost["voice"]["calls"] > 0, cost
        # money-saving info: estimate after the probe, calls / hits / savings in the result and JSON
        assert job["estimate"] and job["estimate"]["total"] > 0 and "estimate" in kinds, job["estimate"]
        assert cost["api_calls"] == cost["total"]["calls"] and "cache_hits" in cost and "saved_usd" in cost
        usage = report["api_usage"]
        assert usage["api_calls"]["transcribe"] == 1 and usage["api_calls"]["translate"] >= 1, usage
        assert usage["api_calls"]["tts"] >= 8 and result["usage"] == usage
        assert report["summary"]["api_calls"] == sum(usage["api_calls"].values())
        assert report["settings"]["ECONOMY_MODE"] is True

        # ---- clip playback (generated voice + original speech)
        code, (wav, _) = http.req("GET", "/api/job/segment/0/audio", raw=True)
        assert code == 200 and wav[:4] == b"RIFF"
        code, (wav, _) = http.req("GET", "/api/job/segment/0/audio?which=original", raw=True)
        assert code == 200 and wav[:4] == b"RIFF" and len(wav) > 16000
        assert http.req("GET", "/api/job/segment/99/audio")[0] == 400

        # ---- spend endpoints + ledger file
        code, spend = http.req("GET", "/api/spend")
        for role in ("analysis", "script", "voice"):
            assert spend["job"][role]["cost"] > 0 and spend["job"][role]["unknown"] == 0, spend["job"]
        assert close(spend["job"]["total"]["cost"], cost["total"]["cost"], 1e-6)
        assert spend["savings"]["api_calls"] == spend["job"]["total"]["calls"], spend["savings"]
        code, cache = http.req("GET", "/api/cache")
        assert code == 200 and cache["bytes"] > 0 and cache["entries"] > 0 and cache["enabled"], cache
        assert spend["month"]["total"]["cost"] >= spend["job"]["total"]["cost"]
        ledger_lines = st.usage_path().read_text(encoding="utf-8").strip().splitlines()
        assert len(ledger_lines) == spend["all"]["total"]["calls"] > 0

        # ---- settings over HTTP: keys go in, only masked keys come out
        secret = "sk-test-ABCDEFGH12345678wxyz"
        code, res = http.req("POST", "/api/settings", {"keys": {"voice": secret}, "MAX_SPEED_UP": 1.3})
        assert code == 200 and res["settings"]["keys"]["voice"] == "sk-...wxyz"
        code, (body, _) = http.req("GET", "/api/settings", raw=True)
        assert secret.encode() not in body and b"sk-...wxyz" in body
        assert secret in st.settings_path().read_text(encoding="utf-8")
        code, res = http.req("POST", "/api/settings/test-key", {"role": "analysis"})
        assert code == 400 and "No key" in res["error"], res
        assert http.req("POST", "/api/settings", {"MAX_SPEED_UP": 9})[0] == 400

        # ---- close the job: temporary files are deleted, outputs stay
        work_dir = manager.job.work_dir
        assert work_dir.exists()
        assert http.req("DELETE", "/api/job")[0] == 200
        assert not work_dir.exists() and Path(result["video_path"]).exists()
        assert http.req("GET", "/api/job/file/mp4")[0] == 400

        # ---- cancel
        code, _ = http.req("POST", "/api/job", {"url": url, "language": "Spanish", "review": True})
        assert code == 200
        http.wait_status({"review"})
        # same video, other language: the transcript comes from the cache (no new analysis spend)
        code, spend = http.req("GET", "/api/spend")
        assert spend["savings"]["hits_by_kind"].get("transcribe") == 1, spend["savings"]
        assert spend["job"]["analysis"]["calls"] == 0, spend["job"]
        assert http.req("POST", "/api/job/cancel")[0] == 200
        job = http.wait_status({"cancelled"})
        assert job["error"] and any(x["status"] == "failed" for x in job["stages"])
        assert http.req("POST", "/api/job/cancel")[0] == 400

        # ---- budget cap: a job that would cost more than "Max spend per video" stops cleanly
        _, cur = http.req("GET", "/api/settings")
        pricing = cur["pricing_effective"]
        pricing["whisper-1"] = {"per_minute": 1.0}           # transcription of 40 s = $0.67 > $0.05
        code, _ = http.req("POST", "/api/settings", {"MAX_SPEND_PER_VIDEO": "0.05", "CACHE_ENABLED": False,
                                                     "pricing": pricing})
        assert code == 200
        code, _ = http.req("POST", "/api/job", {"url": url, "language": "German"})
        assert code == 200
        end = time.time() + 120
        while time.time() < end:
            _, s = http.req("GET", "/api/state")
            if s["job"]["status"] in ("failed", "done", "cancelled"):
                break
            time.sleep(0.25)
        assert s["job"]["status"] == "failed" and "spending limit" in s["job"]["error"], s["job"]["error"]
        assert s["job"]["result"] is None and not list(out_dir.glob("*[[]DE[]]*")), "no output for a stopped job"
        assert s["spend"]["job"]["script"]["calls"] == 0 and s["spend"]["job"]["voice"]["calls"] == 0

        # ---- clear the cache (Settings button)
        code, res = http.req("POST", "/api/cache/clear")
        assert code == 200 and res["bytes"] == 0, res
        pricing["whisper-1"] = {"per_minute": 0.006}
        http.req("POST", "/api/settings", {"MAX_SPEND_PER_VIDEO": "", "CACHE_ENABLED": True, "pricing": pricing})
        print("OK  backend end-to-end: review, live events, downloads, clips, spend, settings, close, cancel, "
              "estimate, savings, budget cap, cache")
    finally:
        manager.shutdown()
        server.shutdown()
        os.environ.pop("DUBBING_FAKE_ENGINE", None)


if __name__ == "__main__":
    try:
        test_cost_math()
        test_ledger_persistence_and_threads()
        test_settings()
        test_log_masking()
        test_backend_end_to_end()
        print("ALL APP TESTS PASSED")
    finally:
        for h in list(logging.getLogger("dubbing").handlers):
            logging.getLogger("dubbing").removeHandler(h)
            h.close()
        shutil.rmtree(TMP, ignore_errors=True)
