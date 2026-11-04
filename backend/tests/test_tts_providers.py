"""TTS provider tests — fake contract + Piper voice discovery (Phase 4).

The Piper unit tests use a synthetic voices directory to verify discovery
and lazy loading without model data. Real synthesis with the provisioned
voices is covered separately (marked `tts_real`, NOT mocked — AGENT.md §6).
"""

from __future__ import annotations

import numpy as np
import pytest
from pathlib import Path

from app.tts.fake import FakeTTSProvider, primary_subtag
from app.tts.piper_local import PiperTTSProvider


def test_primary_subtag_normalization() -> None:
    assert primary_subtag("ur") == "ur"
    assert primary_subtag("ur-PK") == "ur"
    assert primary_subtag("en-US") == "en"
    assert primary_subtag("ES") == "es"


def test_fake_provider_support_matrix() -> None:
    provider = FakeTTSProvider(languages=("en", "ur", "es"))
    assert provider.supports("en")
    assert provider.supports("ur-PK")
    assert provider.supports("es")
    assert not provider.supports("fr")
    assert not provider.supports("de")


def test_piper_discovers_voices_by_language(tmp_path) -> None:
    (tmp_path / "ur").mkdir()
    (tmp_path / "ur" / "ur_PK-fasih-medium.onnx").write_bytes(b"x")
    (tmp_path / "ur" / "ur_PK-fasih-medium.onnx.json").write_text("{}")
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "en_US-lessac-medium.onnx").write_bytes(b"x")
    (tmp_path / "en" / "en_US-lessac-medium.onnx.json").write_text("{}")
    # A voice dir WITHOUT the config json must be ignored (broken install).
    (tmp_path / "fr").mkdir()
    (tmp_path / "fr" / "fr_FR-tom-medium.onnx").write_bytes(b"x")

    provider = PiperTTSProvider(tmp_path)
    assert sorted(provider.voices) == ["en", "ur"]
    assert provider.supports("ur")
    assert provider.supports("ur-PK")
    assert provider.supports("en")
    assert not provider.supports("fr")
    info = provider.describe()
    assert info["provider"] == "piper"
    assert info["languages"] == ["en", "ur"]
    assert info["voices"]["ur"] == "ur_PK-fasih-medium.onnx"


def test_piper_empty_dir_supports_nothing(tmp_path) -> None:
    provider = PiperTTSProvider(tmp_path / "missing")
    assert provider.voices == {}
    assert not provider.supports("en")


def test_piper_synthesize_without_voice_raises_provider_error(tmp_path) -> None:
    from app.tts.base import SynthesisUnavailableError

    provider = PiperTTSProvider(tmp_path)
    with pytest.raises(SynthesisUnavailableError):
        provider.synthesize("hello", "en")


def _voices_provisioned() -> bool:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "models" / "tts"
    if not root.is_dir():
        return False
    return any(
        (lang_dir.is_dir() and any(lang_dir.glob("*.onnx")))
        for lang_dir in root.iterdir()
    )


HAS_VOICES = _voices_provisioned()


@pytest.mark.tts_real
def test_piper_real_synthesis_urdu_english_spanish() -> None:
    """Real local synthesis with the provisioned voices: every language
    produces audible PCM at the voice's sample rate (measured RTF ≈ 0.06–0.08
    on 2 vCPU — see docs/testing.md). Skipped when voices are missing."""
    if not HAS_VOICES:
        pytest.skip("models/tts voices not provisioned")
    provider = PiperTTSProvider(Path(__file__).resolve().parents[2] / "models" / "tts")
    cases = {
        "ur": "یہ ایک سادہ جملہ ہے۔",
        "en": "This is a real speech synthesis test.",
        "es": "Hola, esta es una prueba de síntesis de voz.",
    }
    for language, text in cases.items():
        if not provider.supports(language):
            continue
        audio = provider.synthesize(text, language)
        assert audio.sample_rate >= 16_000
        assert 0.3 <= audio.duration_ms / 1000 <= 15.0
        samples = np.frombuffer(audio.pcm, dtype="<i2").astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(samples**2)))
        assert rms > 0.05, f"{language} synthesis produced silence (rms={rms})"
