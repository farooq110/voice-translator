"""UtteranceSegmenter tests — meeting-audio endpointing (Phase 7, ADR-0019)."""

from __future__ import annotations

import math
import struct

from app.zoom.bridge import UtteranceSegmenter


def _sine_pcm(freq: int = 440, seconds: float = 0.1, rate: int = 16000, amp: int = 8000) -> bytes:
    frames = int(rate * seconds)
    return b"".join(
        struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(frames)
    )


def _silence_pcm(seconds: float = 0.1, rate: int = 16000) -> bytes:
    return b"\x00\x00" * int(rate * seconds)


def test_speech_then_pause_flushes_segment():
    seg = UtteranceSegmenter(pause_ms=300, soft_cap_s=100.0, rms_threshold=300, min_segment_s=0.2)
    assert seg.feed(_sine_pcm(seconds=0.5)) == []  # speech buffers
    assert seg.feed(_silence_pcm(seconds=0.1)) == []  # pause building up
    segments = seg.feed(_silence_pcm(seconds=0.5))  # pause threshold crossed
    assert len(segments) == 1
    duration = len(segments[0]) / 2 / 16000
    assert 0.9 <= duration <= 1.3  # 0.5 s speech + ~0.6 s silence (padded)


def test_soft_cap_flushes_mid_speech():
    seg = UtteranceSegmenter(pause_ms=10_000, soft_cap_s=1.0, rms_threshold=300)
    out: list[bytes] = []
    for _ in range(11):  # 11 × 0.1 s of continuous speech
        out.extend(seg.feed(_sine_pcm(seconds=0.1)))
    assert len(out) == 1
    assert len(out[0]) / 2 / 16000 >= 1.0


def test_silence_only_never_flushes():
    seg = UtteranceSegmenter(pause_ms=100, soft_cap_s=100.0, rms_threshold=300, min_segment_s=0.1)
    for _ in range(50):
        assert seg.feed(_silence_pcm(seconds=0.1)) == []
    assert seg.tail() != b""  # stays buffered for the stop-time flush


def test_tail_returns_remainder_and_resets():
    seg = UtteranceSegmenter(pause_ms=10_000, soft_cap_s=100.0, rms_threshold=300)
    seg.feed(_sine_pcm(seconds=0.3))
    tail = seg.tail()
    assert len(tail) == int(0.3 * 16000) * 2
    assert seg.tail() == b""  # buffer cleared
    # After a tail, the next utterance starts fresh.
    assert seg.feed(_sine_pcm(seconds=0.3)) == []


def test_odd_byte_frames_are_tolerated():
    seg = UtteranceSegmenter(pause_ms=100, soft_cap_s=100.0, rms_threshold=300, min_segment_s=0.1)
    seg.feed(_sine_pcm(seconds=0.5)[:-1])  # simulate an odd-sized frame
    # No crash; segment still flushes once silence follows.
    assert len(seg.feed(_silence_pcm(seconds=0.3))) == 1


def test_below_min_segment_silence_does_not_flush():
    seg = UtteranceSegmenter(pause_ms=100, soft_cap_s=100.0, rms_threshold=300, min_segment_s=2.0)
    seg.feed(_sine_pcm(seconds=0.2))
    for _ in range(4):
        assert seg.feed(_silence_pcm(seconds=0.2)) == []  # buffer ≈ 1.0 s < min_segment_s
    # …but the flush fires as soon as the buffered audio crosses the minimum.
    segments = seg.feed(_silence_pcm(seconds=1.2))
    assert len(segments) == 1
