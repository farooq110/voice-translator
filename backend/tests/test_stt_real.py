"""Real-STT validation with actual audio fixtures — NOT mocked.

Runs the genuine faster-whisper model (CPU, int8) on speech fixtures and checks
that language detection works and transcription is produced. Fixtures are
TTS-generated speech (documented honestly in docs/testing.md); real-device
microphone tests are a Phase 2/6 activity.

Skipped automatically when the model or fixtures are unavailable (e.g. an
offline CI runner) so unit suites always run.
"""

from __future__ import annotations

import os
import wave
from pathlib import Path

import pytest

from app.stt.faster_whisper_stt import FasterWhisperSTT

FIXTURES = Path(__file__).parent / "fixtures"
MODEL = os.environ.get("STT_MODEL", "base")

pytestmark = pytest.mark.stt_real


def _fixtures_present() -> bool:
    return FIXTURES.is_dir() and any(FIXTURES.glob("*.wav"))


@pytest.fixture(scope="module")
def provider() -> FasterWhisperSTT:
    instance = FasterWhisperSTT(model_id=MODEL, device="cpu", compute_type="int8")
    instance.ensure_loaded()
    return instance


def _read_wav(path: Path) -> tuple[bytes, int, int]:
    with wave.open(str(path), "rb") as wav:
        return wav.readframes(wav.getnframes()), wav.getframerate(), wav.getnchannels()


@pytest.mark.skipif(not _fixtures_present(), reason="audio fixtures not present")
@pytest.mark.parametrize(
    "filename,expected_language",
    [
        ("en_hello.wav", "en"),
        ("ur_greeting.wav", "ur"),
        ("es_hola.wav", "es"),
    ],
)
def test_language_detection_and_transcription(
    provider: FasterWhisperSTT, filename: str, expected_language: str
) -> None:
    path = FIXTURES / filename
    pcm, rate, channels = _read_wav(path)
    result = provider.transcribe_pcm(pcm, sample_rate=rate, channels=channels)
    print(
        f"\n{filename}: lang={result.language} p={result.language_probability:.2f} "
        f"dur={result.audio_duration_s:.2f}s text={result.text!r}"
    )
    assert result.language == expected_language, (
        f"expected language {expected_language}, got {result.language} (text={result.text!r})"
    )
    assert result.text.strip(), "transcription must not be empty"
    assert 0.0 <= result.language_probability <= 1.0
