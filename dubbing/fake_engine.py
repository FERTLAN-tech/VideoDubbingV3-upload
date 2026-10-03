"""
Offline stand-in for OpenAI, used by tests and by the app when the environment
variable DUBBING_FAKE_ENGINE=1 is set (no API key, no internet, no YouTube):

* the "download" generates a local test video with FFmpeg,
* "transcription" returns a fixed plan of segments,
* "translation" length is controlled by a ratio hidden in the source text,
* "TTS" is a tone whose length depends on the text length.

It reports usage to the cost tracker like the real engine, so the spend
dashboard can be tested too, and counts its "API calls" in `calls`.
It also has the batch methods (answers go through the real JSON parser), and
can be told to break a batch answer (`break_batches`, `drop_ids`) for tests.
"""
import json
import threading
import time
from collections import Counter
from pathlib import Path

from .engine import parse_batch_items
from .ffmpeg_utils import ffmpeg

CHARS_PER_SECOND = 15.0
FAKE_VIDEO_SECONDS = 40

# (start, end, ratio): ratio > 1 = translation too long, < 1 = too short
FAKE_PLAN = [
    (0.5, 4.0, 1.0), (4.5, 8.0, 1.25), (8.2, 11.0, 2.2), (11.1, 16.0, 0.5),
    (16.5, 19.0, 0.8), (25.0, 28.0, 1.0), (28.05, 31.0, 1.6), (31.2, 35.0, 1.1),
]


def make_test_video(path, seconds=FAKE_VIDEO_SECONDS):
    ffmpeg(["-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], "test video")
    return Path(path)


def fake_download(url, work_dir, on_progress=None):
    """Replaces download_video(): builds a local test video instead of using YouTube."""
    for f in (0.25, 0.5, 0.75):
        if on_progress:
            on_progress(f)
        time.sleep(0.05)
    path = make_test_video(Path(work_dir) / "source.mp4")
    if on_progress:
        on_progress(1.0)
    return path, "Offline test video"


class FakeEngine:
    cache_namespace = "fake"        # never mixed with real OpenAI results in the cache

    def __init__(self, tracker=None, delay=0.0, plan=None):
        self.tracker = tracker
        self.delay = delay          # seconds per call, to watch the UI live
        self.plan = plan or FAKE_PLAN
        self.calls = Counter()      # "API calls" per method
        self.batch_sizes = []
        self.break_batches = 0      # the next N batch answers are invalid JSON
        self.drop_ids = set()       # ids left out of the next batch answer (once each)
        self.never_answer = set()   # ids always left out of batch answers
        self.lock = threading.Lock()

    def _wait(self, kind):
        with self.lock:
            self.calls[kind] += 1
        if self.delay:
            time.sleep(self.delay)

    def transcribe(self, wav, work_dir, duration):
        self._wait("transcribe")
        if self.tracker:
            self.tracker.transcription("whisper-1", duration)
        out = []
        for i, p in enumerate(self.plan):          # (start, end, ratio[, exact text])
            if p[1] <= duration:
                out.append((p[0], p[1], p[3] if len(p) > 3 else f"Sample sentence number {i + 1}. ratio={p[2]} end"))
        return out

    def _chars(self, seconds, ratio):
        return max(3, int(seconds * CHARS_PER_SECOND * ratio))

    def _chat_usage(self, text, prompt_tokens=180):
        if self.tracker:
            self.tracker.chat("gpt-4.1-mini", {"prompt_tokens": prompt_tokens,
                                               "completion_tokens": max(1, len(text) // 4)})

    def _translate(self, source, seconds):
        ratio = float(source.split("ratio=")[1].split()[0]) if "ratio=" in source else 1.0
        return "é" * self._chars(seconds, ratio)

    def translate_segment(self, segment, language, prev_text="", next_text=""):
        self._wait("translate")
        text = self._translate(segment.source_text, segment.natural_duration)
        self._chat_usage(text)
        return text

    @staticmethod
    def _shorter(text):
        return text[: int(len(text) * 0.85)]

    @staticmethod
    def _longer(text):
        return text + "é" * max(1, int(len(text) * 0.08))

    def rewrite_concise(self, segment, text, language, target_s, current_s, attempt):
        self._wait("rewrite")
        out = self._shorter(text)
        self._chat_usage(out)
        return out

    def rewrite_expanded(self, segment, text, language, target_s, current_s, attempt):
        self._wait("rewrite")
        out = self._longer(text)
        self._chat_usage(out)
        return out

    # ---- batch methods: the answer is JSON text parsed by the real parser ----
    def _batch_answer(self, kind, key, rows):
        self._wait(kind)
        with self.lock:
            self.batch_sizes.append(len(rows))
            broken = self.break_batches > 0
            if broken:
                self.break_batches -= 1
            dropped = {r["i"] for r in rows} & self.drop_ids
            self.drop_ids -= dropped
            dropped |= {r["i"] for r in rows} & self.never_answer
        content = json.dumps({key: [r for r in rows if r["i"] not in dropped]}, ensure_ascii=False)
        if broken:
            content = content[: len(content) // 2]          # truncated, invalid JSON
        self._chat_usage(content, prompt_tokens=250 + len(rows) * 30)
        return parse_batch_items(content, key)

    def translate_batch(self, items, language):
        rows = [{"i": it["i"], "text": self._translate(it["text"], it["seconds"])} for it in items]
        return self._batch_answer("translate_batch", "translations", rows)

    def rewrite_batch(self, items, language):
        rows = [{"i": it["i"], "text": self._shorter(it["text"]) if it["mode"] == "shorter"
                 else self._longer(it["text"])} for it in items]
        return self._batch_answer("rewrite_batch", "lines", rows)

    def generate_tts(self, text, language, out_wav):
        self._wait("tts")
        seconds = len(text) / CHARS_PER_SECOND
        ffmpeg(["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds:.4f}:sample_rate=24000",
                "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)], "fake tts")
        if self.tracker:
            self.tracker.tts("gpt-4o-mini-tts", text, seconds)
        return Path(out_wav)
