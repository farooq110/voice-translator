"""WebSocket flow tests for the Phase 3 translation decision engine.

Runs the REAL endpoint with the fake translation provider (deterministic)
and the planner/STT doubles: asserts decisions on `transcription`, the
`translation` event round-trip, pass-through (no provider call), the
utterance-scoped error mapping, rate limiting and disabled mode.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.translation.decision import DecisionEngine
from app.translation.engine import TranslationService
from app.translation.fake import FakeTranslationProvider
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

HELLO_IDENTICAL = {
    **HELLO,
    "config": {**HELLO["config"], "native_language": "en", "preferred_language": "en"},
}


def _open(client: TestClient):
    return client.websocket_connect("/ws/audio")


def _auto_settings(**overrides) -> Settings:
    return Settings(translation_provider="fake", translation_detect_mode="auto", tts_provider="none", **overrides)


def _pinned_settings(**overrides) -> Settings:
    return Settings(translation_provider="fake", translation_detect_mode="pinned", tts_provider="none", **overrides)


def _say(ws, seconds: float = 0.5, utterance_id: str | None = None) -> None:
    ws.send_bytes(silence_pcm(seconds))
    flush = {"v": 1, "type": "flush"}
    if utterance_id is not None:
        flush["utterance_id"] = utterance_id
    ws.send_json(flush)


def _receive_until(ws, wanted_type: str, limit: int = 8) -> dict:
    """Collect events until one of `wanted_type` arrives (bounded)."""
    for _ in range(limit):
        event = ws.receive_json()
        if event.get("type") == wanted_type:
            return event
    raise AssertionError(f"no {wanted_type} event arrived")


def test_auto_mode_native_speech_translates_to_preferred() -> None:
    stt = FakeSTT(language="ur")  # auto-detect label
    app = create_app(stt=stt, vad=PassThroughVAD(), settings=_auto_settings())
    with TestClient(app) as client, _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        _say(ws, utterance_id="c_0001_dead01")

        tr = _receive_until(ws, "transcription")
        assert tr["id"] == "c_0001_dead01"
        assert tr["text"] == "fake transcription 1"
        assert tr["language"] == "ur"
        decision = tr["decision"]
        assert decision["action"] == "translate"
        assert decision["reason"] == "native_language"
        assert decision["source_language"] == "ur"
        assert decision["target_language"] == "en"
        assert decision["detection"] == "whisper"

        translation = _receive_until(ws, "translation")
        assert translation["id"] == "c_0001_dead01"
        assert translation["text"] == "[fake:ur\u2192en] fake transcription 1"
        assert translation["source_language"] == "ur"
        assert translation["target_language"] == "en"
        assert translation["provider"] == "fake"
        assert translation["duration_ms"] >= 0


def test_auto_mode_preferred_speech_translates_to_native() -> None:
    """Rule 2: the OTHER participant's preferred-language speech translates
    into the native language — impossible in the Phase 1/2 pinned mode."""
    stt = FakeSTT(language="en")
    app = create_app(stt=stt, vad=PassThroughVAD(), settings=_auto_settings())
    with TestClient(app) as client, _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        _say(ws)

        tr = _receive_until(ws, "transcription")
        assert tr["language"] == "en"
        assert tr["decision"]["action"] == "translate"
        assert tr["decision"]["reason"] == "preferred_language"
        assert tr["decision"]["target_language"] == "ur"

        translation = _receive_until(ws, "translation")
        assert translation["text"] == "[fake:en\u2192ur] fake transcription 1"
        assert translation["target_language"] == "ur"


def test_auto_mode_ki002_hindi_label_resolves_via_cluster() -> None:
    """The MEASURED production signature (KI-002): Urdu speech arrives with a
    standalone detect_language label `hi` (p≈0.775). The cluster resolves it
    to Urdu BEFORE the decode — the decode is pinned to `ur` (correct text),
    and the event reports the RESOLVED language with detection=cluster."""
    stt = FakeSTT(language="ur", detect_language="hi", detect_probability=0.775)
    app = create_app(stt=stt, vad=PassThroughVAD(), settings=_auto_settings())
    with TestClient(app) as client, _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        _say(ws, utterance_id="c_0009_ki0002")

        tr = _receive_until(ws, "transcription")
        assert tr["language"] == "ur"  # resolved, not the raw `hi` label
        assert tr["decision"]["action"] == "translate"
        assert tr["decision"]["reason"] == "native_language"
        assert tr["decision"]["source_language"] == "ur"
        assert tr["decision"]["detection"] == "cluster"
        # the decode itself was FORCED to ur (never auto-decoded)
        assert stt.last_language_hint == "ur"
        translation = _receive_until(ws, "translation")
        assert translation["text"] == "[fake:ur\u2192en] fake transcription 1"


def test_auto_mode_uncertain_detection_falls_back_and_gates() -> None:
    """A confident-looking-but-untrusted third-language label below the
    threshold falls back to the native pin (correct text for the native
    speaker) and the decision answers low_confidence pass-through."""
    stt = FakeSTT(language="ur", detect_language="fr", detect_probability=0.4)
    app = create_app(stt=stt, vad=PassThroughVAD(), settings=_auto_settings())
    with TestClient(app) as client, _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        _say(ws)
        tr = _receive_until(ws, "transcription")
        assert tr["language"] == "ur"  # fallback pin → native decode
        assert stt.last_language_hint == "ur"
        assert tr["decision"]["action"] == "passthrough"
        assert tr["decision"]["reason"] == "low_confidence"
        ws.send_json({"v": 1, "type": "ping", "ts": 21})
        assert ws.receive_json() == {"type": "pong", "v": 1, "ts": 21}


def test_third_language_passes_through_without_translation() -> None:
    """Rule 3: a third language passes through — the provider is NEVER called
    (the fake provider records its calls)."""
    stt = FakeSTT(language="es")
    provider = FakeTranslationProvider()
    service = TranslationService(
        provider=provider, decision_engine=DecisionEngine(), detect_mode="auto"
    )
    app = create_app(
        stt=stt,
        vad=PassThroughVAD(),
        settings=Settings(translation_provider="none", tts_provider="none"),
        translator=service,
    )
    with TestClient(app) as client, _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        _say(ws)

        tr = _receive_until(ws, "transcription")
        assert tr["decision"]["action"] == "passthrough"
        assert tr["decision"]["reason"] == "third_language"
        assert tr["decision"].get("target_language") is None  # absent ≡ null

        ws.send_json({"v": 1, "type": "ping", "ts": 77})
        pong = ws.receive_json()
        assert pong == {"type": "pong", "v": 1, "ts": 77}  # nothing in between
    assert provider.calls == []  # Rule 4: no provider call for pass-through


def test_low_confidence_detection_passes_through() -> None:
    stt = FakeSTT(language="ur", probability=0.4)
    app = create_app(stt=stt, vad=PassThroughVAD(), settings=_auto_settings())
    with TestClient(app) as client, _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        _say(ws)
        tr = _receive_until(ws, "transcription")
        assert tr["decision"]["action"] == "passthrough"
        assert tr["decision"]["reason"] == "low_confidence"
        ws.send_json({"v": 1, "type": "ping", "ts": 5})
        assert ws.receive_json() == {"type": "pong", "v": 1, "ts": 5}


def test_identical_configuration_passes_through() -> None:
    stt = FakeSTT(language="en")
    app = create_app(stt=stt, vad=PassThroughVAD(), settings=_auto_settings())
    with TestClient(app) as client, _open(client) as ws:
        ws.send_json(HELLO_IDENTICAL)
        first = ws.receive_json()
        assert first["type"] == "error" and first["code"] == "identical_languages"
        assert ws.receive_json()["type"] == "ready"
        _say(ws)
        tr = _receive_until(ws, "transcription")
        assert tr["decision"]["action"] == "passthrough"
        assert tr["decision"]["reason"] == "identical_config"


def test_pinned_mode_decision_and_hint() -> None:
    stt = FakeSTT()
    app = create_app(stt=stt, vad=PassThroughVAD(), settings=_pinned_settings())
    with TestClient(app) as client, _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        _say(ws)
        tr = _receive_until(ws, "transcription")
        # KI-006: pinned mode still forces the configured native language.
        assert stt.last_language_hint == "ur"
        assert tr["language"] == "ur"
        assert tr["decision"]["action"] == "translate"
        assert tr["decision"]["detection"] == "pinned"
        assert tr["decision"]["reason"] == "native_language"
        _receive_until(ws, "translation")


def test_unsupported_pair_passes_through() -> None:
    stt = FakeSTT(language="en")  # would translate en→ur
    service = TranslationService(
        provider=FakeTranslationProvider(supported=frozenset({("ur", "en")})),
        decision_engine=DecisionEngine(),
        detect_mode="auto",
    )
    app = create_app(
        stt=stt,
        vad=PassThroughVAD(),
        settings=Settings(translation_provider="none", tts_provider="none"),
        translator=service,
    )
    with TestClient(app) as client, _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        _say(ws)
        tr = _receive_until(ws, "transcription")
        assert tr["decision"]["action"] == "passthrough"
        assert tr["decision"]["reason"] == "unsupported_pair"
        ws.send_json({"v": 1, "type": "ping", "ts": 9})
        assert ws.receive_json() == {"type": "pong", "v": 1, "ts": 9}


def test_provider_failure_is_utterance_scoped_non_fatal() -> None:
    stt = FakeSTT(language="ur")
    service = TranslationService(
        provider=FakeTranslationProvider(fail=True),
        decision_engine=DecisionEngine(),
        detect_mode="auto",
    )
    app = create_app(
        stt=stt,
        vad=PassThroughVAD(),
        settings=Settings(translation_provider="none", tts_provider="none"),
        translator=service,
    )
    with TestClient(app) as client, _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        _say(ws, utterance_id="c_0002_feed02")

        tr = _receive_until(ws, "transcription")
        assert tr["id"] == "c_0002_feed02"
        err = _receive_until(ws, "error")
        assert err["code"] == "translation_failed"
        assert err["fatal"] is False
        assert err["utterance_id"] == "c_0002_feed02"
        # the connection stays usable and no translation event arrives
        ws.send_json({"v": 1, "type": "ping", "ts": 11})
        assert ws.receive_json() == {"type": "pong", "v": 1, "ts": 11}


def test_rate_limited_translation_is_utterance_scoped() -> None:
    stt = FakeSTT(language="ur")
    service = TranslationService(
        provider=FakeTranslationProvider(),
        decision_engine=DecisionEngine(),
        detect_mode="auto",
        rate_limit_per_minute=1,
    )
    app = create_app(
        stt=stt,
        vad=PassThroughVAD(),
        settings=Settings(translation_provider="none", tts_provider="none"),
        translator=service,
    )
    with TestClient(app) as client, _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        _say(ws, utterance_id="c_0003_aa0001")
        _say(ws, utterance_id="c_0004_aa0002")

        events: list[dict] = []
        while len(events) < 6:
            events.append(ws.receive_json())
            types = [e["type"] for e in events]
            if types.count("error") >= 1 and len([e for e in events if e["type"] == "transcription"]) >= 2:
                break
        transcriptions = [e for e in events if e["type"] == "transcription"]
        errors = [e for e in events if e["type"] == "error"]
        assert len(transcriptions) == 2
        assert len(errors) == 1
        assert errors[0]["code"] == "translation_rate_limited"
        assert errors[0]["utterance_id"] == "c_0004_aa0002"


def test_disabled_translation_has_no_decision_field(client: TestClient) -> None:
    """conftest `client` = translation disabled (provider none) — the protocol
    keeps the exact Phase 1/2 shape (no `decision`, no `translation` event)."""
    with _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        _say(ws)
        tr = ws.receive_json()
        assert tr["type"] == "transcription"
        assert "decision" not in tr or tr["decision"] is None
        ws.send_json({"v": 1, "type": "ping", "ts": 13})
        assert ws.receive_json() == {"type": "pong", "v": 1, "ts": 13}


def test_translation_event_covers_server_generated_ids_too() -> None:
    """Server-initiated flushes without a client id still get translated with
    a server-generated id."""
    stt = FakeSTT(language="ur")
    app = create_app(stt=stt, vad=PassThroughVAD(), settings=_auto_settings())
    with TestClient(app) as client, _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(silence_pcm(0.4))
        ws.send_json({"v": 1, "type": "flush"})  # no utterance_id
        tr = _receive_until(ws, "transcription")
        assert tr["id"].startswith("u_")
        translation = _receive_until(ws, "translation")
        assert translation["id"] == tr["id"]
