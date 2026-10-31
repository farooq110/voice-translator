"""Unit tests for the pure utterance planner (gate / trim / segmentation).

These run against hand-built region data — no model, fully deterministic.
The real Silero model is covered in test_vad_silero.py, the WS integration
in test_ws_vad_backpressure.py.
"""

from __future__ import annotations

import pytest

from app.audio.vad import SpeechRegion, VadAnalysis, plan_from_regions

RATE = 16_000


def make_pcm(seconds: float) -> bytes:
    """Non-zero PCM16 (amplitude 1) so slices are observable by length."""
    return b"\x01\x00" * int(RATE * seconds)


def analysis(*regions: tuple[float, float], duration: float) -> VadAnalysis:
    return VadAnalysis(
        duration_s=duration,
        regions=tuple(SpeechRegion(start_s=a, end_s=b) for a, b in regions),
    )


def plan(analysis_: VadAnalysis, pcm: bytes, **overrides: float) -> object:
    kwargs: dict[str, float] = {
        "trim_pad_ms": 200,
        "split_silence_ms": 700,
        "split_max_seconds": 24.0,
        "min_speech_ms": 120,
        "max_chunks": 8,
    }
    kwargs.update(overrides)
    return plan_from_regions(analysis_, pcm, sample_rate=RATE, **kwargs)  # type: ignore[arg-type]


def test_empty_buffer_produces_empty_plan() -> None:
    result = plan(analysis(duration=0.0), b"")
    assert result.chunks == ()  # type: ignore[attr-defined]
    assert result.source_duration_s == 0.0  # type: ignore[attr-defined]


def test_no_speech_regions_is_gated() -> None:
    result = plan(analysis(duration=3.0), make_pcm(3.0))
    assert result.chunks == ()  # type: ignore[attr-defined]
    assert result.speech_s == 0.0  # type: ignore[attr-defined]
    assert result.dropped_s == 3.0  # type: ignore[attr-defined]


def test_too_short_speech_is_gated() -> None:
    """A 60 ms blip (< min_speech_ms=120) is noise, not speech."""
    result = plan(analysis((1.0, 1.06), duration=3.0), make_pcm(3.0))
    assert result.chunks == ()  # type: ignore[attr-defined]


def test_single_region_is_trimmed_with_pad() -> None:
    """Speech 0.5–2.5 s in a 3 s buffer → keep 0.3–2.7 s (±200 ms pad)."""
    result = plan(analysis((0.5, 2.5), duration=3.0), make_pcm(3.0))
    assert len(result.chunks) == 1  # type: ignore[attr-defined]
    chunk = result.chunks[0]  # type: ignore[attr-defined]
    assert abs(len(chunk) / 2 - 2.4 * RATE) <= 2  # the padded span
    assert result.dropped_s == pytest.approx(0.6)  # type: ignore[attr-defined]
    assert result.split is False  # type: ignore[attr-defined]


def test_trim_pad_is_clamped_to_buffer_edges() -> None:
    """Speech starting at 0.1 s: pad cannot go below the buffer start."""
    result = plan(analysis((0.1, 1.5), duration=2.0), make_pcm(2.0))
    chunk = result.chunks[0]  # type: ignore[attr-defined]
    assert abs(len(chunk) / 2 - 1.7 * RATE) <= 2  # 0.0 → 1.7 s
    assert result.dropped_s == pytest.approx(0.3)  # type: ignore[attr-defined]


def test_gap_below_split_silence_is_not_split() -> None:
    """0.5 s pause between regions < 700 ms → ONE chunk (no over-segmentation)."""
    result = plan(
        analysis((0.5, 2.5), (3.0, 5.0), duration=6.0), make_pcm(6.0)
    )
    assert len(result.chunks) == 1  # type: ignore[attr-defined]
    assert result.split is False  # type: ignore[attr-defined]


def test_gap_above_split_silence_splits_into_two() -> None:
    """0.9 s pause ≥ 700 ms → two chunks, ordered, text boundary clean."""
    result = plan(
        analysis((0.5, 2.5), (3.4, 5.0), duration=6.0), make_pcm(6.0)
    )
    assert len(result.chunks) == 2  # type: ignore[attr-defined]
    assert result.split is True  # type: ignore[attr-defined]
    first, second = result.chunks  # type: ignore[attr-defined]
    # each chunk covers its own region + pad (±2 samples for float rounding)
    assert abs(len(first) / 2 - 2.4 * RATE) <= 2  # 0.3 → 2.7 s
    assert abs(len(second) / 2 - 2.0 * RATE) <= 2  # 3.2 → 5.2 s


def test_span_cap_forces_split_even_without_long_pauses() -> None:
    """Regions flowing continuously but spanning > split_max_seconds must
    still be split (bounded decode calls)."""
    result = plan(
        analysis((0.0, 10.0), (10.1, 20.1), (20.2, 30.0), duration=30.0),
        make_pcm(30.0),
        split_max_seconds=10.0,
    )
    assert len(result.chunks) == 3  # type: ignore[attr-defined]
    assert result.split is True  # type: ignore[attr-defined]


def test_chunk_count_is_bounded_by_max_chunks() -> None:
    """10 tiny utterances with 0.8 s pauses merge down to max_chunks=8."""
    regions = tuple((float(i), float(i) + 0.2) for i in range(10))
    result = plan(analysis(*regions, duration=10.2), make_pcm(10.2), max_chunks=8)
    assert len(result.chunks) == 8  # type: ignore[attr-defined]


def test_split_plan_reports_source_duration_not_trimmed() -> None:
    """The plan carries the PRE-trim duration — the protocol reports what the
    user actually spoke."""
    result = plan(analysis((1.0, 2.0), duration=4.0), make_pcm(4.0))
    assert result.source_duration_s == 4.0  # type: ignore[attr-defined]
