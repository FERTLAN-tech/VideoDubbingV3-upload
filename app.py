"""
Video Dubbing V3: local web interface (Windows).

Starts a small server on 127.0.0.1 and opens it as an app window (Microsoft
Edge app mode, or the default browser). The app stops when you click "Quit",
when you close this console window, or a few minutes after the last app window
was closed (if no video is being processed).

Options:
  --no-browser   do not open a window (just print the address)
  --port N       preferred port (default 8765; a free one is used if taken)
"""
import argparse
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from dubbing import config
from dubbing.ffmpeg_utils import prepare_path
from dubbing.jobs import JobManager, cleanup_stale_work_dirs

IDLE_EXIT_SECONDS = 180


def free_port(preferred=8765):
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("No free network port.")


def find_edge():
    candidates = []
    for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            candidates.append(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    return next((p for p in candidates if p.exists()), None)


def open_window(url):
    edge = find_edge()
    if edge:
        try:
            subprocess.Popen([str(edge), f"--app={url}", "--window-size=1360,900"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "Microsoft Edge (app window)"
        except OSError:
            pass
    webbrowser.open(url)
    return "your default browser"


def main():
    parser = argparse.ArgumentParser(description=config.APP_NAME)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-idle-exit", action="store_true")
    args = parser.parse_args()

    prepare_path()
    cleanup_stale_work_dirs()
    logging.getLogger("werkzeug").setLevel(logging.WARNING)   # no line per request in the console
    from werkzeug.serving import make_server

    from dubbing.web import create_app

    manager = JobManager()
    stop = threading.Event()
    app = create_app(manager, on_quit=stop.set)
    port = free_port(args.port)
    server = make_server("127.0.0.1", port, app, threaded=True)
    url = f"http://127.0.0.1:{port}/"
    threading.Thread(target=server.serve_forever, name="web", daemon=True).start()

    print("=" * 60)
    print(f"  {config.APP_NAME}")
    print(f"  Address: {url}")
    if manager.fake:
        print("  OFFLINE TEST MODE (DUBBING_FAKE_ENGINE=1): no API calls.")
    if not args.no_browser:
        print(f"  Opening {open_window(url)}...")
    print("  Keep this window open while you use the app.")
    print("  To stop: click Quit in the app, or close this window.")
    print("=" * 60)

    try:
        while not stop.wait(2):
            idle = (manager.client_count() == 0 and not (manager.job and manager.job.running)
                    and time.time() - manager.last_client_seen > IDLE_EXIT_SECONDS)
            if idle and not args.no_idle_exit:
                print("No app window open for a while: stopping.")
                break
    except KeyboardInterrupt:
        pass
    print("Stopping...")
    manager.shutdown()
    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
