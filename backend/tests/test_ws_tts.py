"""WebSocket flow tests for Phase 4 TTS + browser audio playback (ADR-0013).

Runs the REAL endpoint with fake translation + fake TTS providers
(deterministic): asserts the audio event + binary frame pairing, the
pass-through-bypasses-TTS rule, utterance-scoped non-fatal error mapping,
rate limiting and disabled mode.
"""

from __future__ import annotations

import numpy as np
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.tts.fake import FakeTTSProvider
from app.tts.service import TTSService
from .conftest import FakeSTT, PassThroughVAD, silence_pcm

HELLO = {
    "v": 1,
    "type": "hello",
    "config": {
        "native_language": "ur",
        "preferred_language": "en",
        "audio": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1},
    },
}


def _open(client: TestClient):
    return client.websocket_connect("/ws/audio")


def _settings(**overrides) -> Settings:
    base: dict = {
        "translation_provider": "fake",
        "translation_detect_mode": "auto",
        "tts_provider": "fake",
    }
    base.update(overrides)
    return Settings(**base)


def _say(ws, seconds: float = 0.5, utterance_id: str | None = None) -> None:
    ws.send_bytes(silence_pcm(seconds))
    flush = {"v": 1, "type": "flush"}
    if utterance_id is not None:
        flush["utterance_id"] = utterance_id
    ws.send_json(flush)


def _receive_until(ws, wanted_type: str, limit: int = 10) -> dict:
    """Collect JSON events until one of `wanted_type` arrives (bounded)."""
    for _ in range(limit):
        event = ws.receive_json()
        if event.get("type") == wanted_type:
            return event
    raise AssertionError(f"no {wanted_type} event arrived")


def _rms(pcm: bytes) -> float:
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0


def test_translated_utterance_produces_audio_event_and_binary_frame() -> None:
    stt = FakeSTT(language="ur")
    tts = FakeTTSProvider()
    service = TTSService(provider=tts, timeout_s=5)
    app = create_app(stt=stt, vad=PassThroughVAD(), settings=_settings(), tts=service)
    with TestClient(app) as client, _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        _say(ws, utterance_id="c_0001_dead01")

        tr = _receive_until(ws, "transcription")
        assert tr["decision"]["action"] == "translate"
        assert tr["decision"]["target_language"] == "en"

        translation = _receive_until(ws, "translation")
        assert translation["id"] == "c_0001_dead01"

        audio = _receive_until(ws, "audio")
        assert audio["id"] == "c_0001_dead01"
        assert audio["language"] == "en"
        assert audio["encoding"] == "pcm_s16le"
        assert audio["channels"] == 1
        assert audio["provider"] == "fake"
        assert audio["bytes"] >= 2
        assert audio["duration_ms"] > 0

        pcm = ws.receive_bytes()
        assert len(pcm) == audio["bytes"]
        assert len(pcm) % 2 == 0
        assert _rms(pcm) > 0.05  # real audible samples, not silence
        # duration matches the PCM length at the announced sample rate
        assert audio["duration_ms"] == int(
            len(pcm) / 2 / audio["sample_rate"] * 1000
        )


def test_pass_through_bypasses_tts() -> None:
    """Master prompt §17: original audio pass-through is NEVER regenerated
    with TTS — no audio event, no binary frame, no provider call."""
    stt = FakeSTT(language="es")  # third language → pass-through
    tts = FakeTTSProvider()
    service = TTSService(provider=tts, timeout_s=5)
    app = create_app(stt=stt, vad=PassThroughVAD(), settings=_settings(), tts=service)
    with TestClient(app) as client, _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        _say(ws, utterance_id="c_0002_feed02")

        tr = _receive_until(ws, "transcription")
        assert tr["decision"]["action"] == "passthrough"
        assert tr["decision"]["reason"] == "third_language"

        # Nothing between the transcription and the next pong — no audio.
        ws.send_json({"v": 1, "type": "ping", "ts": 21})
        pong = ws.receive_json()
        assert pong == {"type": "pong", "v": 1, "ts": 21}
    assert tts.calls == 0  # the provider was never asked


def test_disabled_tts_keeps_phase3_shape() -> None:
    """TTS provider none → no audio events; the protocol keeps the exact
    Phase 3 event sequence."""
    stt = FakeSTT(language="ur")
    tts = FakeTTSProvider()
    app = create_app(
        stt=stt,
        vad=PassThroughVAD(),
        settings=_settings(tts_provider="none"),
        tts=TTSService(provider=None, timeout_s=5),
    )
    with TestClient(app) as client, _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        _say(ws)
        _receive_until(ws, "transcription")
        _receive_until(ws, "translation")
        ws.send_json({"v": 1, "type": "ping", "ts": 3})
        assert ws.receive_json() == {"type": "pong", "v": 1, "ts": 3}
    assert tts.calls == 0


def test_tts_provider_failure_is_utterance_scoped_non_fatal() -> None:
    stt = FakeSTT(language="ur")
    tts = FakeTTSProvider(fail=True)
    service = TTSService(provider=tts, timeout_s=5)
    app = create_app(stt=stt, vad=PassThroughVAD(), settings=_settings(), tts=service)
    with TestClient(app) as client, _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        _say(ws, utterance_id="c_0003_beef03")

        _receive_until(ws, "transcription")
        _receive_until(ws, "translation")
        err = _receive_until(ws, "error")
        assert err["code"] == "tts_failed"
        assert err["fatal"] is False
        assert err["utterance_id"] == "c_0003_beef03"
        # the connection stays usable
        ws.send_json({"v": 1, "type": "ping", "ts": 11})
        assert ws.receive_json() == {"type": "pong", "v": 1, "ts": 11}


def test_unsupported_tts_language_is_visible_not_silent() -> None:
    """Honest degradation: a target language without a voice answers a
    non-fatal utterance-scoped error — the translated text is kept."""
    stt = FakeSTT(language="en")  # Rule 2 → target ur... but no voice for it
    tts = FakeTTSProvider(languages=("en",))  # only English
    service = TTSService(provider=tts, timeout_s=5)
    app = create_app(stt=stt, vad=PassThroughVAD(), settings=_settings(), tts=service)
    with TestClient(app) as client, _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        _say(ws, utterance_id="c_0004_cafe04")

        _receive_until(ws, "transcription")
        translation = _receive_until(ws, "translation")
        assert translation["target_language"] == "ur"
        err = _receive_until(ws, "error")
        assert err["code"] == "tts_unsupported_language"
        assert err["fatal"] is False
        assert err["utterance_id"] == "c_0004_cafe04"


def test_tts_rate_limited_is_utterance_scoped() -> None:
    stt = FakeSTT(language="ur")
    service = TTSService(
        provider=FakeTTSProvider(), timeout_s=5, rate_limit_per_minute=1
    )
    app = create_app(stt=stt, vad=PassThroughVAD(), settings=_settings(), tts=service)
    with TestClient(app) as client, _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        _say(ws, utterance_id="c_0005_aa0001")
        _say(ws, utterance_id="c_0006_aa0002")

        # Deterministic totals for two translated utterances with the SECOND
        # synthesis rate-limited: 2 transcriptions + 2 translations + 1 audio
        # + 1 tts_rate_limited error (+ exactly 1 binary frame right after
        # the audio metadata). Event ORDER may interleave, so collect first
        # and assert on the multiset.
        events: list[dict] = []
        binaries: list[tuple[dict, bytes]] = []
        while len(events) < 6:
            event = ws.receive_json()
            events.append(event)
            if event.get("type") == "audio":
                binaries.append((event, ws.receive_bytes()))

        types = [e["type"] for e in events]
        assert types.count("transcription") == 2
        assert types.count("translation") == 2
        audios = [e for e in events if e["type"] == "audio"]
        assert len(audios) == 1
        assert audios[0]["id"] == "c_0005_aa0001"
        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) == 1
        assert errors[0]["code"] == "tts_rate_limited"
        assert errors[0]["utterance_id"] == "c_0006_aa0002"
        assert errors[0]["fatal"] is False
        # exactly one audio payload, matching its metadata
        assert len(binaries) == 1
        (meta, pcm) = binaries[0]
        assert len(pcm) == meta["bytes"]


def test_health_exposes_tts_section() -> None:
    tts = TTSService(provider=FakeTTSProvider(languages=("en", "ur")), timeout_s=5)
    app = create_app(
        stt=FakeSTT(), vad=PassThroughVAD(), settings=_settings(), tts=tts
    )
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["tts"]["enabled"] is True
    assert body["tts"]["provider"] == "fake"
    assert body["tts"]["languages"] == ["en", "ur"]


def test_health_tts_disabled_is_visible() -> None:
    app = create_app(
        stt=FakeSTT(),
        vad=PassThroughVAD(),
        settings=_settings(tts_provider="none"),
        tts=TTSService(provider=None, timeout_s=5),
    )
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["tts"]["enabled"] is False
    assert body["tts"]["provider"] is None
