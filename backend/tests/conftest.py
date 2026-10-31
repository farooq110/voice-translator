"""Shared test fixtures.

The WS flow tests use a deterministic FakeSTT provider (injected via
create_app's dependency parameter) — protocol/session logic must be testable
without model downloads. Real-model behavior is covered separately in
test_stt_real.py (marked `stt_real`, NOT mocked).

Phase 2: the VAD pipeline is likewise injectable. The default fixtures use a
pass-through planner so protocol tests keep their pre-VAD semantics; the VAD
gate/trim/split behavior itself is covered in test_ws_vad_backpressure.py
(fakes) and test_vad_silero.py (real Silero, no Whisper needed).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.audio.vad import UtterancePlan
from app.main import create_app
from app.stt.base import STTProvider, TranscriptionResult


class FakeSTT(STTProvider):
    name = "fake-stt"

    def __init__(self, *, empty_text: bool = False) -> None:
        super().__init__(model_id="fake-model", device="cpu", low_confidence_threshold=0.60)
        self.calls = 0
        self.last_language_hint: str | None = None
        self.last_audio_bytes: bytes = b""
        self.empty_text = empty_text

    def ensure_loaded(self) -> None:
        return None

    @property
    def loaded(self) -> bool:
        return True

    def transcribe_pcm(
        self,
        pcm16: bytes,
        *,
        sample_rate: int,
        channels: int = 1,
        language_hint: str | None = None,
    ) -> TranscriptionResult:
        self.calls += 1
        self.last_language_hint = language_hint
        self.last_audio_bytes = pcm16
        duration = len(pcm16) / 2 / sample_rate
        return TranscriptionResult(
            # Mirrors the real provider: the configured hint is authoritative
            # and is reported back as the result language.
            text="" if self.empty_text else f"fake transcription {self.calls}",
            language=language_hint if language_hint else "en",
            language_probability=0.97,
            audio_duration_s=round(duration, 3),
        )


class PassThroughVAD:
    """Planner double: everything is speech, nothing trimmed or split —
    reproduces the Phase 1 pipeline for protocol tests."""

    name = "pass-through-vad"
    enabled = True

    def ensure_loaded(self) -> None:
        return None

    def describe(self) -> dict[str, object]:
        return {"provider": self.name, "enabled": True, "loaded": True}

    def plan(self, pcm16: bytes, *, sample_rate: int) -> UtterancePlan:
        duration = len(pcm16) / 2 / sample_rate if sample_rate else 0.0
        return UtterancePlan(
            chunks=(pcm16,) if pcm16 else (),
            source_duration_s=duration,
            speech_s=duration,
            dropped_s=0.0,
            split=False,
        )


class SilentVAD:
    """Planner double: never detects speech → every flush is gated out."""

    name = "silent-vad"
    enabled = True

    def ensure_loaded(self) -> None:
        return None

    def describe(self) -> dict[str, object]:
        return {"provider": self.name, "enabled": True, "loaded": True}

    def plan(self, pcm16: bytes, *, sample_rate: int) -> UtterancePlan:
        duration = len(pcm16) / 2 / sample_rate if sample_rate else 0.0
        return UtterancePlan(
            chunks=(), source_duration_s=duration, speech_s=0.0, dropped_s=duration, split=False
        )


class TrimVAD(PassThroughVAD):
    """Planner double: keeps only the first `keep_s` seconds of the buffer
    (simulates edge trimming) — lets tests assert what STT receives."""

    name = "trim-vad"

    def __init__(self, keep_s: float = 0.2) -> None:
        self.keep_s = keep_s

    def plan(self, pcm16: bytes, *, sample_rate: int) -> UtterancePlan:
        keep_bytes = int(self.keep_s * sample_rate) * 2
        trimmed = pcm16[:keep_bytes]
        duration = len(pcm16) / 2 / sample_rate if sample_rate else 0.0
        return UtterancePlan(
            chunks=(trimmed,) if trimmed else (),
            source_duration_s=duration,
            speech_s=self.keep_s,
            dropped_s=max(0.0, duration - self.keep_s),
            split=False,
        )


class SplitVAD(PassThroughVAD):
    """Planner double: splits the buffer into two equal halves — simulates
    segmentation; the texts must be joined in order."""

    def plan(self, pcm16: bytes, *, sample_rate: int) -> UtterancePlan:
        half = (len(pcm16) // 4) * 2  # sample-aligned
        duration = len(pcm16) / 2 / sample_rate if sample_rate else 0.0
        return UtterancePlan(
            chunks=(pcm16[:half], pcm16[half:]),
            source_duration_s=duration,
            speech_s=duration,
            dropped_s=0.0,
            split=True,
        )


@pytest.fixture()
def fake_stt() -> FakeSTT:
    return FakeSTT()


@pytest.fixture()
def client(fake_stt: FakeSTT) -> Iterator[TestClient]:
    app = create_app(stt=fake_stt, vad=PassThroughVAD())
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def empty_stt() -> FakeSTT:
    """Provider whose decodes are pure hallucination → empty text after filter."""
    return FakeSTT(empty_text=True)


@pytest.fixture()
def client_empty(empty_stt: FakeSTT) -> Iterator[TestClient]:
    app = create_app(stt=empty_stt, vad=PassThroughVAD())
    with TestClient(app) as test_client:
        yield test_client


def silence_pcm(seconds: float, sample_rate: int = 16_000) -> bytes:
    """PCM16 mono silence of the requested duration."""
    return b"\x00\x00" * int(sample_rate * seconds)
