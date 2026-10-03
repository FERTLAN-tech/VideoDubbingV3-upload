"""Speech segment data model and transcript clean-up."""
import re
from dataclasses import dataclass, field

from . import config

# "[Music]", "(applause)", "♪ ... ♪", "*laughs*": sound descriptions, not speech
_SOUND_TAG = re.compile(r"\[[^\]]*\]|\([^)]*\)|♪[^♪]*♪|\*[^*]*\*")
_NON_SPEECH_WORDS = {"music", "musique", "música", "musik", "musica", "applause", "applaudissements",
                     "laughter", "laughs", "rires", "silence", "inaudible", "noise", "bruit"}


def is_speech_text(text):
    """False for text that must not be voiced: empty, punctuation / symbols only,
    or only sound tags such as "[Music]", "(Applause)", "♪♪"."""
    text = (text or "").strip()
    if not text:
        return False
    rest = _SOUND_TAG.sub(" ", text)
    words = re.findall(r"\w+", rest)
    if not words:
        return False
    return not all(w.lower() in _NON_SPEECH_WORDS for w in words)


@dataclass
class Segment:
    index: int
    start: float
    end: float
    source_text: str
    slot_end: float = 0.0          # filled by calculate_segment_slots()

    @property
    def duration(self):
        return self.end - self.start

    @property
    def slot_duration(self):
        return self.slot_end - self.start

    @property
    def natural_duration(self):
        """How long the original speaker actually spoke, limited to the slot."""
        return max(0.05, min(self.end, self.slot_end) - self.start)


@dataclass
class SegmentResult:
    segment: Segment
    translated_text: str
    audio_path: str | None
    tts_duration: float            # duration of the raw TTS clip that was kept
    final_duration: float          # duration placed on the timeline
    stretch_factor: float = 1.0
    timing_mode: str = "normal"
    steps: list = field(default_factory=list)
    attempts: int = 1
    warnings: list = field(default_factory=list)

    @property
    def voice_end(self):
        return self.segment.start + self.final_duration


def clean_segments(raw, total_duration):
    """Sort, de-overlap, clamp and merge tiny fragments. raw = [(start, end, text)]."""
    items = []
    for start, end, text in sorted(raw, key=lambda r: r[0]):
        text = " ".join((text or "").split())
        if not text:
            continue
        start = max(0.0, min(float(start), total_duration))
        end = max(start, min(float(end), total_duration))
        if end - start < 0.05:
            continue
        if items and start < items[-1][1]:          # overlap with previous segment
            items[-1][1] = start
            if items[-1][1] - items[-1][0] < 0.05:  # previous one vanished: absorb it
                prev = items.pop()
                start, text = prev[0], prev[2] + " " + text
        items.append([start, end, text])

    merged = []
    for start, end, text in items:
        if merged:
            p = merged[-1]
            gap = start - p[1]
            short = (p[1] - p[0] < config.MIN_SEGMENT_SECONDS) or (end - start < config.MIN_SEGMENT_SECONDS)
            if short and gap <= config.MERGE_MAX_GAP and end - p[0] <= config.MERGE_MAX_SECONDS:
                p[1], p[2] = end, p[2] + " " + text
                continue
        merged.append([start, end, text])

    return [Segment(i + 1, s, e, t) for i, (s, e, t) in enumerate(merged)]


def calculate_segment_slots(segments, video_duration, safety_gap=None):
    """
    Each segment may use the time until the NEXT segment starts (minus a safety
    gap), not just until its own end. The last one may run to the end of the video.
    """
    gap = config.SAFETY_GAP if safety_gap is None else safety_gap
    for i, seg in enumerate(segments):
        if i + 1 < len(segments):
            boundary = segments[i + 1].start
            slot_end = boundary - gap
        else:
            boundary = video_duration
            slot_end = video_duration
        # degenerate data: never let the slot vanish or reach past the boundary
        seg.slot_end = min(boundary, max(slot_end, seg.start + 0.05))
    return segments
