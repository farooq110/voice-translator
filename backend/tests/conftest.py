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

    def __init__(self, *, empty_text: bool = False) -> None:
        super().__init__(model_id="fake-model", device="cpu", low_confidence_threshold=0.60)
        self.calls = 0
        self.last_language_hint: str | None = None
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
        duration = len(pcm16) / 2 / sample_rate
        return TranscriptionResult(
            # Mirrors the real provider: the configured hint is authoritative
            # and is reported back as the result language.
            text="" if self.empty_text else f"fake transcription {self.calls}",
            language=language_hint if language_hint else "en",
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


@pytest.fixture()
def empty_stt() -> FakeSTT:
    """Provider whose decodes are pure hallucination → empty text after filter."""
    return FakeSTT(empty_text=True)


@pytest.fixture()
def client_empty(empty_stt: FakeSTT) -> Iterator[TestClient]:
    app = create_app(stt=empty_stt)
    with TestClient(app) as test_client:
        yield test_client


def silence_pcm(seconds: float, sample_rate: int = 16_000) -> bytes:
    """PCM16 mono silence of the requested duration."""
    return b"\x00\x00" * int(sample_rate * seconds)
