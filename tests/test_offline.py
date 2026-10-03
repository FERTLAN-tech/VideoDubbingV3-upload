"""
Offline end-to-end test (no internet, no API key).

A fake engine replaces OpenAI: its "TTS" is a tone whose length depends on the
text length, so we can force segments that are too long, too short or fine and
check the bidirectional timing, the assembly, the MP4, the SRT and the JSON.

Run:  .venv\\Scripts\\python.exe -m tests.test_offline
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# the persistent cache and settings go to a temporary APPDATA (your real cache is untouched)
APPDATA_TMP = Path(tempfile.mkdtemp(prefix="vdub_offline_appdata_"))
os.environ["APPDATA"] = str(APPDATA_TMP)

from dubbing import config  # noqa: E402
from dubbing.ffmpeg_utils import ffmpeg, measure_audio_duration, prepare_path  # noqa: E402
from dubbing.pipeline import JobCancelled, PipelineHooks, dub_video_file, safe_filename  # noqa: E402
from dubbing.segments import Segment, calculate_segment_slots, clean_segments  # noqa: E402

CHARS_PER_SECOND = 15.0


class FakeEngine:
    """Translation length = ratio x natural length; ratio is encoded in the source text."""

    def transcribe(self, wav, work_dir, duration):
        raise AssertionError("not used")

    def _chars(self, seconds, ratio):
        return max(3, int(seconds * CHARS_PER_SECOND * ratio))

    def translate_segment(self, segment, language, prev_text="", next_text=""):
        ratio = float(segment.source_text.split("ratio=")[1].split()[0])
        return "é" * self._chars(segment.natural_duration, ratio)   # Unicode on purpose

    def rewrite_concise(self, segment, text, language, target_s, current_s, attempt):
        return text[: int(len(text) * 0.85)]

    def rewrite_expanded(self, segment, text, language, target_s, current_s, attempt):
        return text + "é" * max(1, int(len(text) * 0.08))

    def generate_tts(self, text, language, out_wav):
        seconds = len(text) / CHARS_PER_SECOND
        ffmpeg(["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds:.4f}:sample_rate=24000",
                "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)], "fake tts")
        return Path(out_wav)


class Recorder(PipelineHooks):
    """Records what an interface would see."""

    def __init__(self):
        self.stages, self.updates, self.segments, self.info = [], [], None, None

    def stage(self, stage, status, message=""):
        self.stages.append((stage, status))

    def video_info(self, info):
        self.info = info

    def segments_ready(self, segments):
        self.segments = list(segments)

    def segment_update(self, index, **fields):
        self.updates.append((index, sorted(fields)))


def make_video(path, seconds):
    ffmpeg(["-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], "test video")


def test_slots_and_cleanup():
    segs = [Segment(1, 10.0, 13.0, "a"), Segment(2, 16.0, 20.5, "b")]
    calculate_segment_slots(segs, 30.0)
    assert abs(segs[0].slot_end - 15.96) < 1e-9, segs[0].slot_end
    assert segs[1].slot_end == 30.0
    cleaned = clean_segments([(0, 0.5, "Hi"), (0.6, 3, "there friend"), (2.5, 5, "overlap"), (6, 6, "  ")], 10)
    assert [s.source_text for s in cleaned] == ["Hi there friend", "overlap"], cleaned
    assert cleaned[0].end <= cleaned[1].start
    assert safe_filename('a<b>:"c"/d\\e|f?g*') == "a b c d e f g"
    assert safe_filename("CON") == "video"
    print("OK  slots / cleanup / file names")


def test_full_dub():
    """The original timing spec: economy mode OFF (rewrite first whenever a line does not fit)."""
    prepare_path()
    tmp = Path(tempfile.mkdtemp(prefix="vdub_test_"))
    out_root = tmp / "out with spaces é"
    config.OUTPUT_DIR = out_root
    config.LOG_DIR = out_root / "logs"
    saved_economy = config.ECONOMY_MODE
    config.ECONOMY_MODE = False
    try:
        video = tmp / "source video é.mp4"
        make_video(video, 40)
        # (start, end, ratio): ratio > 1 = translation too long, < 1 = too short
        plan = [
            (0.5, 4.0, 1.0),    # fits
            (4.5, 8.0, 1.25),   # slightly long -> concise rewrite
            (8.2, 11.0, 2.2),   # far too long, no pause -> rewrites + 1.35x + last-resort fade
            (11.1, 16.0, 0.5),  # very short -> expand + slow down + padding
            (16.5, 19.0, 0.8),  # a bit short -> expansion
            (25.0, 28.0, 1.0),  # pause before it
            (28.05, 31.0, 1.6), # long, little room -> rewrites + moderate speed up
            (31.2, 35.0, 1.1),  # last segment: may use the end of the video
        ]
        segments = [Segment(i + 1, s, e, f"text ratio={r} end") for i, (s, e, r) in enumerate(plan)]
        hooks = Recorder()
        result = dub_video_file(video, "Test: video/é?", "French", FakeEngine(), tmp / "work",
                                on_progress=lambda p, m: None, segments=segments, hooks=hooks)

        # the interface hooks saw every stage, in order, and every segment 3 times
        expected = ["probe", "translate", "voice", "timing", "assemble", "export", "validate"]
        assert [s for s, st in hooks.stages if st == "running"] == expected, hooks.stages
        assert [s for s, st in hooks.stages if st == "done"] == expected, hooks.stages
        assert hooks.info.width == 640 and len(hooks.segments) == len(plan)
        for kind in ("translated_text", "tts", "result"):
            assert sorted(i for i, f in hooks.updates if f == [kind]) == list(range(len(plan))), kind

        report = json.loads(result.json_path.read_text(encoding="utf-8"))
        rows = report["segments"]
        for row in rows:
            print(f"  seg {row['index']}: slot {row['slot_duration']:.2f}s natural {row['natural_duration']:.2f}s "
                  f"tts {row['tts_duration']:.2f}s x{row['stretch_factor']:.3f} -> {row['final_duration']:.2f}s "
                  f"[{row['timing_mode']}] {row['steps']}")
            assert row["voice_end"] <= row["slot_end"] + 1e-3, row
            assert config.MAX_SLOW_DOWN - 1e-3 <= row["stretch_factor"] <= config.MAX_SPEED_UP + 1e-3
        for a, b in zip(rows, rows[1:]):
            assert a["voice_end"] <= b["start"], (a, b)

        modes = {r["index"]: r for r in rows}
        assert modes[1]["timing_mode"] == "normal"
        assert "concise_rewrite" in modes[2]["steps"]
        assert modes[3]["timing_mode"] == "speed_up" and modes[3]["stretch_factor"] == config.MAX_SPEED_UP
        assert modes[3]["warnings"], "segment 3 cannot fit even at max speed: expect a warning"
        assert "slow_down" in modes[4]["steps"] and modes[4]["timing_mode"] in ("slow_down", "padded")
        assert "expanded_rewrite" in modes[5]["steps"]
        assert modes[7]["timing_mode"] == "speed_up" and not modes[7]["warnings"]
        assert 1.0 < modes[7]["stretch_factor"] < config.MAX_SPEED_UP

        dub_len = measure_audio_duration(tmp / "work" / "dub_track.wav")
        assert abs(dub_len - 40) < 0.05, dub_len
        srt = result.srt_path.read_text(encoding="utf-8")
        assert srt.startswith("1\n00:00:00,500 --> ") and "é" in srt
        assert result.video_path.exists() and result.video_path.name == "Test video é [FR].mp4"
        print("  checks:", *result.checks, sep="\n    ")
        print("OK  full offline dub ->", result.video_path)
    finally:
        config.ECONOMY_MODE = saved_economy
        shutil.rmtree(tmp, ignore_errors=True)


def test_cancel():
    """A cancelled job stops before doing any work and reports the stage as failed."""
    import threading
    prepare_path()
    tmp = Path(tempfile.mkdtemp(prefix="vdub_test_"))
    try:
        video = tmp / "v.mp4"
        make_video(video, 5)
        cancel = threading.Event()
        hooks = Recorder()

        class CancelOnFirstTranslation(FakeEngine):
            def translate_segment(self, *a, **k):
                cancel.set()                        # the user clicks Cancel during translation
                return super().translate_segment(*a, **k)

        segments = [Segment(i + 1, s, s + 1.5, "x ratio=1.0 end") for i, s in enumerate((0.2, 2.0, 3.5))]
        try:
            dub_video_file(video, "t", "French", CancelOnFirstTranslation(), tmp / "work",
                           segments=segments, hooks=hooks, cancel_event=cancel)
        except JobCancelled:
            pass
        else:
            raise AssertionError("the job was not cancelled")
        assert ("translate", "failed") in hooks.stages, hooks.stages
        assert not any(s == "voice" for s, _ in hooks.stages), "voice must not start after cancel"
        print("OK  cancel stops the job between segments")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_slots_and_cleanup()
    test_full_dub()
    test_cancel()
    print("ALL TESTS PASSED")
