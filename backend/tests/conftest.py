"""Shared test fixtures.

The WS flow tests use a deterministic FakeSTT provider (injected via
create_app's dependency parameter) — protocol/session logic must be testable
without model downloads. Real-model behavior is covered separately in
test_stt_real.py (marked `stt_real`, NOT mocked).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.stt.base import STTProvider, TranscriptionResult


class FakeSTT(STTProvider):
    name = "fake-stt"

    def __init__(self) -> None:
        super().__init__(model_id="fake-model", device="cpu", low_confidence_threshold=0.60)
        self.calls = 0

    def ensure_loaded(self) -> None:
        return None

    @property
    def loaded(self) -> bool:
        return True

    def transcribe_pcm(
        self, pcm16: bytes, *, sample_rate: int, channels: int = 1
    ) -> TranscriptionResult:
        self.calls += 1
        duration = len(pcm16) / 2 / sample_rate
        return TranscriptionResult(
            text=f"fake transcription {self.calls}",
            language="en",
            language_probability=0.97,
            audio_duration_s=round(duration, 3),
        )


@pytest.fixture()
def fake_stt() -> FakeSTT:
    return FakeSTT()


@pytest.fixture()
def client(fake_stt: FakeSTT) -> Iterator[TestClient]:
    app = create_app(stt=fake_stt)
    with TestClient(app) as test_client:
        yield test_client


def silence_pcm(seconds: float, sample_rate: int = 16_000) -> bytes:
    """PCM16 mono silence of the requested duration."""
    return b"\x00\x00" * int(sample_rate * seconds)
