"""Real Silero VAD tests — the bundled ONNX model, NOT mocked.

faster-whisper ships the Silero v6 ONNX inside its package assets, so these
run offline without downloading anything (and without loading Whisper).
They pin the production VAD behavior: speech fixtures are detected and
trimmed sensibly; silence and near-silence are gated out; two bursts
separated by a real pause are segmented.

The WebSocket layer runs this exact pipeline in the STT executor.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from app.audio.vad import SileroVAD

FIXTURES = Path(__file__).parent / "fixtures"
RATE = 16_000


def _read_wav(name: str) -> bytes:
    with wave.open(str(FIXTURES / name), "rb") as wav:
        assert wav.getframerate() == RATE and wav.getnchannels() == 1
        return wav.readframes(wav.getnframes())


@pytest.fixture(scope="module")
def vad() -> SileroVAD:
    instance = SileroVAD()
    return instance


def test_speech_fixture_is_detected_and_trimmed(vad: SileroVAD) -> None:
    pcm = _read_wav("en_hello.wav")
    analysis = vad.analyze(pcm, sample_rate=RATE)
    assert analysis.has_speech, "real speech must be detected"
    assert analysis.speech_s >= 1.0
    plan = vad.plan(pcm, sample_rate=RATE)
    assert len(plan.chunks) >= 1
    assert plan.source_duration_s == pytest.approx(len(pcm) / 2 / RATE, abs=0.01)
    # trimming removes the non-speech edges but keeps (padded) speech
    assert plan.dropped_s < 2.0
    kept_s = sum(len(c) / 2 / RATE for c in plan.chunks)
    assert kept_s >= analysis.speech_s  # pad keeps everything detected
    print(
        f"\nen_hello: duration={analysis.duration_s:.2f}s speech={analysis.speech_s:.2f}s "
        f"regions={len(analysis.regions)} chunks={len(plan.chunks)} "
        f"kept={kept_s:.2f}s dropped={plan.dropped_s:.2f}s"
    )


def test_digital_silence_is_gated(vad: SileroVAD) -> None:
    plan = vad.plan(b"\x00\x00" * RATE * 2, sample_rate=RATE)
    assert plan.chunks == ()
    assert plan.dropped_s == pytest.approx(2.0, abs=0.01)


def test_near_silence_noise_is_gated(vad: SileroVAD) -> None:
    """Very low-level noise (-66 dBFS) must not be classified as speech —
    this is the class of audio the Phase 1 dead-audio gate could NOT catch
    (it only rejected pure digital silence)."""
    import numpy as np

    rng = np.random.default_rng(42)
    noise = (rng.standard_normal(RATE * 2) * 0.0005).astype(np.float32)
    pcm = (noise * 32_767).astype("<i2").tobytes()
    plan = vad.plan(pcm, sample_rate=RATE)
    assert plan.chunks == (), "low-level noise must be gated before STT"


def test_two_bursts_with_real_pause_are_segmented(vad: SileroVAD) -> None:
    """en_hello + 0.9 s of silence + en_hello → split at the pause."""
    burst = _read_wav("en_hello.wav")
    gap = b"\x00\x00" * int(RATE * 0.9)
    pcm = burst + gap + burst
    plan = vad.plan(pcm, sample_rate=RATE)
    assert plan.split is True
    assert len(plan.chunks) >= 2, "each burst should become its own decode"
    kept_s = sum(len(c) / 2 / RATE for c in plan.chunks)
    total_s = len(pcm) / 2 / RATE
    assert kept_s < total_s, "the 0.9 s gap must be largely trimmed"
    print(
        f"\ntwo bursts: duration={total_s:.2f}s chunks={len(plan.chunks)} "
        f"kept={kept_s:.2f}s dropped={plan.dropped_s:.2f}s"
    )


def test_disabled_vad_is_full_passthrough() -> None:
    """Kill switch: enabled=False must reproduce the Phase 1 behavior —
    no gating, no trimming, no model load."""
    instance = SileroVAD(enabled=False)
    pcm = _read_wav("en_hello.wav")
    assert instance.loaded is False
    plan = instance.plan(pcm, sample_rate=RATE)
    assert instance.loaded is False, "disabled VAD must not load the model"
    assert len(plan.chunks) == 1
    assert plan.chunks[0] == pcm
    assert plan.dropped_s == 0.0
    assert plan.source_duration_s == pytest.approx(len(pcm) / 2 / RATE, abs=0.01)
