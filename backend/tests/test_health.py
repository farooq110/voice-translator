"""Health endpoint contract."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from .conftest import FakeSTT, PassThroughVAD


def test_health_ok(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["app"] == "ai-voice-translator"
    assert body["protocol"] == 1
    assert body["audio"] == {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1}
    assert body["stt"]["provider"] == "fake-stt"
    assert body["stt"]["loaded"] is True
    assert body["sessions"] == {"active": 0, "max": 50, "by_platform": {}}
    assert "version" in body


def _piper_app(voices_dir: Path):
    return TestClient(
        create_app(
            stt=FakeSTT(),
            vad=PassThroughVAD(),
            settings=Settings(
                translation_provider="fake",
                tts_provider="piper",
                tts_voices_dir=str(voices_dir),
            ),
        )
    )


def test_health_warns_when_tts_enabled_without_voices(tmp_path: Path) -> None:
    """QA round (BUG-001): a piper deployment with ZERO usable voices must be
    visible in /health (tts.warning) — not discovered as silent audio during
    a real conversation."""
    with _piper_app(tmp_path / "tts") as c:  # empty voices dir
        body = c.get("/health").json()
    assert body["tts"]["enabled"] is True
    assert body["tts"]["languages"] == []
    assert "TTS enabled but no voice models" in body["tts"]["warning"]


def test_health_no_warning_when_voice_present(tmp_path: Path) -> None:
    """A complete voice (onnx + adjacent onnx.json) clears the warning."""
    voices = tmp_path / "tts" / "en"
    voices.mkdir(parents=True)
    (voices / "en_US-dummy.onnx").write_bytes(b"onnx-bytes")
    (voices / "en_US-dummy.onnx.json").write_text("{}")
    with _piper_app(tmp_path / "tts") as c:
        body = c.get("/health").json()
    assert body["tts"]["languages"] == ["en"]
    assert "warning" not in body["tts"]
