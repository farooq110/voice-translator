"""Real-STT validation with actual audio fixtures — NOT mocked.

Runs the genuine faster-whisper model (CPU, int8) on speech fixtures and checks
that transcription works, with BOTH language modes:

- forced hint (production path, KI-006 fix): the model is pinned to the
  configured native language — deterministic labels, correct script;
- automatic detection: kept to document the measured ur/hi label confusion
  (KI-002) that motivates the forced-hint design and Phase 3's decision
  engine for the *other* participant's audio.

Fixture provenance (documented honestly in docs/testing.md):
- TTS-generated speech via Microsoft edge-tts NEURAL voices (en-US-Aria,
  ur-PK-Asad, es-ES-Alvaro) — real speech, synthetic voices, not real humans.

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
MODEL = os.environ.get("STT_MODEL", "small")

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
def test_urdu_forced_language_transcribed_in_arabic_script(
    provider: FasterWhisperSTT,
) -> None:
    """Production path (KI-006 fix): the configured native language (ur) is
    forced — Urdu speech must come back as Urdu text in Arabic script."""
    pcm, rate, channels = _read_wav(FIXTURES / "ur_greeting.wav")
    result = provider.transcribe_pcm(
        pcm, sample_rate=rate, channels=channels, language_hint="ur"
    )
    print(f"\nur (forced): lang={result.language} p={result.language_probability:.2f} text={result.text!r}")
    assert result.language == "ur", "forced hint must be echoed as the result language"
    assert result.language_probability == pytest.approx(1.0)
    assert result.text.strip(), "Urdu transcription must not be empty"
    ratio = _arabic_script_ratio(result.text)
    assert ratio >= 0.8, (
        f"Urdu should be transcribed in Arabic script (ratio={ratio:.2f}, text={result.text!r})"
    )


@pytest.mark.skipif(not _fixtures_present(), reason="audio fixtures not present")
def test_urdu_forced_language_label_is_ur(provider: FasterWhisperSTT) -> None:
    pcm, rate, channels = _read_wav(FIXTURES / "ur_greeting.wav")
    result = provider.transcribe_pcm(
        pcm, sample_rate=rate, channels=channels, language_hint="ur"
    )
    assert result.language == "ur"


@pytest.mark.skipif(not _fixtures_present(), reason="audio fixtures not present")
def test_dead_silence_is_gated_before_the_model(provider: FasterWhisperSTT) -> None:
    """KI-006: digital silence must be rejected WITHOUT decoding (no
    hallucinated digits/strings) and reported as empty text — the WebSocket
    layer then surfaces `no_speech_detected`."""
    result = provider.transcribe_pcm(
        b"\x00\x00" * 32_000, sample_rate=16_000, channels=1, language_hint="ur"
    )
    print(f"\nsilence: lang={result.language} p={result.language_probability:.2f} text={result.text!r}")
    assert result.text == ""
    assert result.language == "ur"
    assert result.audio_duration_s == pytest.approx(2.0, abs=0.01)


@pytest.mark.skipif(not _fixtures_present(), reason="audio fixtures not present")
@pytest.mark.xfail(
    reason="KI-002: with AUTO-detection Whisper confuses Urdu with Hindi on "
    "short utterances; Phase 1 pins the configured language instead — "
    "tracked in project-state/KNOWN_ISSUES.md",
    strict=False,
)
def test_urdu_autodetect_label_is_ur(provider: FasterWhisperSTT) -> None:
    """Documents the residual auto-detect confusion (no hint passed) — the
    code path Phase 3 must not trust blindly for the other participant."""
    pcm, rate, channels = _read_wav(FIXTURES / "ur_greeting.wav")
    result = provider.transcribe_pcm(pcm, sample_rate=rate, channels=channels)
    assert result.language == "ur"


# ---------------------------------------------------------------------------
# Phase 2: full production pipeline — real Silero VAD gate + real Whisper
# over the real WebSocket endpoint (nothing mocked end to end).
# ---------------------------------------------------------------------------

HELLO_UR = {
    "v": 1,
    "type": "hello",
    "config": {
        "native_language": "ur",
        "preferred_language": "en",
        "audio": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1},
    },
}


@pytest.mark.skipif(not _fixtures_present(), reason="audio fixtures not present")
def test_full_ws_pipeline_real_vad_and_stt() -> None:
    """Urdu speech streamed over /ws/audio must survive the Phase 2 VAD gate
    (speech passes, gets trimmed) and be transcribed in Arabic script."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    pcm, rate, _ = _read_wav(FIXTURES / "ur_greeting.wav")
    # Phase 1/2 semantics (pinned STT language) — this test covers the VAD +
    # STT pipeline, not the Phase 3 decision engine.
    from app.config import Settings

    app = create_app(settings=Settings(translation_provider="none", tts_provider="none"))
    with TestClient(app) as client:
        with client.websocket_connect("/ws/audio") as ws:
            ws.send_json(HELLO_UR)
            ready = ws.receive_json()
            assert ready["type"] == "ready"
            health = client.get("/health").json()
            assert health["vad"]["enabled"] is True
            assert health["vad"]["provider"] == "silero-vad"
            chunk = int(rate * 0.1) * 2  # 100 ms chunks, like the frontend
            for i in range(0, len(pcm), chunk):
                ws.send_bytes(pcm[i : i + chunk])
            ws.send_json({"v": 1, "type": "flush"})
            event = ws.receive_json()
            assert event["type"] == "transcription", f"unexpected: {event}"
            assert event["language"] == "ur"
            assert event["audio_duration_s"] == pytest.approx(len(pcm) / 2 / rate, abs=0.05)
            ratio = _arabic_script_ratio(event["text"])
            assert ratio >= 0.8, f"Arabic script expected, got {event['text']!r}"
            print(f"\nws pipeline ur: {event['text']!r} proc_ms={event['processing_ms']}")
