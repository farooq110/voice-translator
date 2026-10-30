"""Real-STT validation with actual audio fixtures — NOT mocked.

Runs the genuine faster-whisper model (CPU, int8) on speech fixtures and checks
that language detection works and transcription is produced.

Fixture provenance (documented honestly in docs/testing.md):
- TTS-generated speech via Microsoft edge-tts NEURAL voices (en-US-Aria,
  ur-PK-Asad, es-ES-Alvaro) — real speech, synthetic voices, not real humans.
- Known limitation (KI-002): Whisper confuses Urdu with Hindi on short
  utterances (shared spoken Hindustani). The Urdu transcription itself is
  correct (Arabic script); the *label* may be 'hi'. Covered by an xfail test
  plus a strict transcription-quality test.

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

ARABIC_SCRIPT_RANGE = (0x0600, 0x06FF)


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


def _arabic_script_ratio(text: str) -> float:
    if not text:
        return 0.0
    lo, hi = ARABIC_SCRIPT_RANGE
    arabic = sum(1 for ch in text if lo <= ord(ch) <= hi)
    letters = sum(1 for ch in text if ch.isalpha())
    return arabic / letters if letters else 0.0


@pytest.mark.skipif(not _fixtures_present(), reason="audio fixtures not present")
def test_english_detected_and_transcribed(provider: FasterWhisperSTT) -> None:
    pcm, rate, channels = _read_wav(FIXTURES / "en_hello.wav")
    result = provider.transcribe_pcm(pcm, sample_rate=rate, channels=channels)
    print(f"\nen: lang={result.language} p={result.language_probability:.2f} text={result.text!r}")
    assert result.language == "en"
    assert result.language_probability >= 0.5
    assert "how are you" in result.text.lower()


@pytest.mark.skipif(not _fixtures_present(), reason="audio fixtures not present")
def test_spanish_detected_and_transcribed(provider: FasterWhisperSTT) -> None:
    pcm, rate, channels = _read_wav(FIXTURES / "es_hola.wav")
    result = provider.transcribe_pcm(pcm, sample_rate=rate, channels=channels)
    print(f"\nes: lang={result.language} p={result.language_probability:.2f} text={result.text!r}")
    assert result.language == "es"
    assert "hola" in result.text.lower()


@pytest.mark.skipif(not _fixtures_present(), reason="audio fixtures not present")
def test_urdu_transcribed_in_arabic_script(provider: FasterWhisperSTT) -> None:
    """Urdu speech must be transcribed as Urdu text (Arabic script), even when
    the language *label* is confused with Hindi (KI-002)."""
    pcm, rate, channels = _read_wav(FIXTURES / "ur_greeting.wav")
    result = provider.transcribe_pcm(pcm, sample_rate=rate, channels=channels)
    print(f"\nur: lang={result.language} p={result.language_probability:.2f} text={result.text!r}")
    assert result.text.strip(), "Urdu transcription must not be empty"
    ratio = _arabic_script_ratio(result.text)
    assert ratio >= 0.8, (
        f"Urdu should be transcribed in Arabic script (ratio={ratio:.2f}, text={result.text!r})"
    )


@pytest.mark.skipif(not _fixtures_present(), reason="audio fixtures not present")
@pytest.mark.xfail(
    reason="KI-002: Whisper confuses Urdu with Hindi on short utterances; "
    "tracked in project-state/KNOWN_ISSUES.md",
    strict=False,
)
def test_urdu_language_label_is_ur(provider: FasterWhisperSTT) -> None:
    pcm, rate, channels = _read_wav(FIXTURES / "ur_greeting.wav")
    result = provider.transcribe_pcm(pcm, sample_rate=rate, channels=channels)
    assert result.language == "ur"
