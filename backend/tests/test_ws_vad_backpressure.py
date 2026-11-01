"""WebSocket integration tests for the Phase 2 VAD pipeline + backpressure.

Uses planner fakes (deterministic) around the real endpoint logic:

- gate: silence answered `no_speech_detected` WITHOUT calling STT;
- trim: STT receives the trimmed audio; the event reports the pre-trim
  duration the user actually spoke;
- split: segmented utterances decode per chunk, texts joined in order;
- backpressure: per-session and global admission caps answer `server_busy`
  (non-fatal) instead of queueing without bound.
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.stt.base import TranscriptionResult
from .conftest import FakeSTT, PassThroughVAD, SilentVAD, SplitVAD, TrimVAD, silence_pcm

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


def _ready(ws) -> None:
    ws.send_json(HELLO)
    assert ws.receive_json()["type"] == "ready"


def _tight_settings(**overrides: int) -> Settings:
    return Settings(
        stt_max_pending_per_session=overrides.get("stt_max_pending_per_session", 4),
        stt_max_queue_depth=overrides.get("stt_max_queue_depth", 16),
        translation_provider="none",  # Phase 1/2 semantics for these tests
    )


def test_vad_gate_error_carries_utterance_id() -> None:
    """BUG-001: the gate rejection must echo the flush's utterance_id so the
    client can settle exactly that utterance (never a newer one)."""
    stt = FakeSTT()
    app = create_app(settings=_tight_settings(), stt=stt, vad=SilentVAD())
    with TestClient(app) as client:
        with _open(client) as ws:
            _ready(ws)
            ws.send_bytes(silence_pcm(0.5))
            ws.send_json({"v": 1, "type": "flush", "utterance_id": "c_0007_beef42"})
            err = ws.receive_json()
            assert err["type"] == "error" and err["code"] == "no_speech_detected"
            assert err["utterance_id"] == "c_0007_beef42"
            assert stt.calls == 0, "STT must NOT be invoked for gated audio"


def test_vad_gate_rejects_silence_without_calling_stt() -> None:
    """The decisive Phase 2 behavior: gated audio never reaches the model."""
    stt = FakeSTT()
    app = create_app(settings=_tight_settings(), stt=stt, vad=SilentVAD())
    with TestClient(app) as client:
        with _open(client) as ws:
            _ready(ws)
            ws.send_bytes(silence_pcm(0.5))
            ws.send_json({"v": 1, "type": "flush"})
            err = ws.receive_json()
            assert err["type"] == "error" and err["code"] == "no_speech_detected"
            assert err["fatal"] is False
            assert stt.calls == 0, "STT must NOT be invoked for gated audio"
            # the session stays fully usable
            ws.send_json({"v": 1, "type": "ping", "ts": 5})
            assert ws.receive_json() == {"type": "pong", "v": 1, "ts": 5}


def test_vad_trim_delivers_shorter_audio_but_honest_duration() -> None:
    stt = FakeSTT()
    app = create_app(settings=_tight_settings(), stt=stt, vad=TrimVAD(keep_s=0.2))
    with TestClient(app) as client:
        with _open(client) as ws:
            _ready(ws)
            ws.send_bytes(silence_pcm(1.0))
            ws.send_json({"v": 1, "type": "flush"})
            tr = ws.receive_json()
            assert tr["type"] == "transcription"
            # STT saw only the trimmed 0.2 s, the user spoke 1.0 s
            assert len(stt.last_audio_bytes) == int(0.2 * 16_000) * 2
            assert tr["audio_duration_s"] == pytest.approx(1.0, abs=0.01)


def test_vad_split_decodes_per_chunk_and_joins_texts() -> None:
    stt = FakeSTT()
    app = create_app(settings=_tight_settings(), stt=stt, vad=SplitVAD())
    with TestClient(app) as client:
        with _open(client) as ws:
            _ready(ws)
            ws.send_bytes(silence_pcm(2.0))
            ws.send_json({"v": 1, "type": "flush"})
            tr = ws.receive_json()
            assert tr["type"] == "transcription"
            assert stt.calls == 2, "each segment is decoded separately"
            assert tr["text"] == "fake transcription 1 fake transcription 2"
            assert tr["audio_duration_s"] == pytest.approx(2.0, abs=0.01)


class BlockingFakeSTT(FakeSTT):
    """FakeSTT that parks the executor thread until the test releases it —
    makes admission-cap behavior deterministic."""

    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def transcribe_pcm(self, *args, **kwargs) -> TranscriptionResult:  # type: ignore[override]
        self.started.set()
        self.release.wait(timeout=10)
        return super().transcribe_pcm(*args, **kwargs)  # type: ignore[arg-type]


def test_per_session_pending_cap_answers_server_busy() -> None:
    stt = BlockingFakeSTT()
    app = create_app(
        settings=_tight_settings(stt_max_pending_per_session=1), stt=stt, vad=PassThroughVAD()
    )
    with TestClient(app) as client:
        with _open(client) as ws:
            _ready(ws)
            ws.send_bytes(silence_pcm(0.5))
            ws.send_json({"v": 1, "type": "flush"})
            assert stt.started.wait(timeout=5), "first decode must start"
            # second flush while the first is decoding → backpressure
            ws.send_bytes(silence_pcm(0.5))
            ws.send_json({"v": 1, "type": "flush", "utterance_id": "c_0002_cafe02"})
            err = ws.receive_json()
            assert err["type"] == "error" and err["code"] == "server_busy"
            assert err["fatal"] is False
            # BUG-001: the id is echoed even under backpressure…
            assert err["utterance_id"] == "c_0002_cafe02"
            # …and the wording says the segment was DROPPED (nothing to re-flush)
            # with a queue-depth hint (OBS-005)
            assert "dropped" in err["message"]
            assert "1/1" in err["message"]
            # the first utterance still completes and is delivered
            stt.release.set()
            tr = ws.receive_json()
            assert tr["type"] == "transcription"
            # and the session is fully usable again afterwards
            ws.send_bytes(silence_pcm(0.3))
            ws.send_json({"v": 1, "type": "flush"})
            tr2 = ws.receive_json()
            assert tr2["type"] == "transcription"


def test_global_queue_cap_answers_server_busy_across_sessions() -> None:
    stt = BlockingFakeSTT()
    app = create_app(
        settings=_tight_settings(stt_max_queue_depth=1), stt=stt, vad=PassThroughVAD()
    )
    with TestClient(app) as client:
        with _open(client) as ws1, _open(client) as ws2:
            _ready(ws1)
            _ready(ws2)
            ws1.send_bytes(silence_pcm(0.5))
            ws1.send_json({"v": 1, "type": "flush"})
            assert stt.started.wait(timeout=5)
            # a DIFFERENT session flushes while the global queue is full
            ws2.send_bytes(silence_pcm(0.5))
            ws2.send_json({"v": 1, "type": "flush", "utterance_id": "c_0009_dead09"})
            err = ws2.receive_json()
            assert err["type"] == "error" and err["code"] == "server_busy"
            assert err["utterance_id"] == "c_0009_dead09"
            assert "queue 1/1" in err["message"]
            stt.release.set()
            assert ws1.receive_json()["type"] == "transcription"
            # queue slot released → session 2 works again
            ws2.send_bytes(silence_pcm(0.3))
            ws2.send_json({"v": 1, "type": "flush"})
            assert ws2.receive_json()["type"] == "transcription"


def test_queue_slot_is_released_on_stt_failure() -> None:
    """A crashing provider must not leak the admission slot (finally-release)."""

    class ExplodingSTT(FakeSTT):
        def transcribe_pcm(self, *args, **kwargs) -> TranscriptionResult:  # type: ignore[override]
            raise RuntimeError("boom")

    stt = ExplodingSTT()
    app = create_app(
        settings=_tight_settings(stt_max_queue_depth=1), stt=stt, vad=PassThroughVAD()
    )
    with TestClient(app) as client:
        with _open(client) as ws:
            _ready(ws)
            ws.send_bytes(silence_pcm(0.5))
            ws.send_json({"v": 1, "type": "flush"})
            err = ws.receive_json()
            assert err["type"] == "error" and err["code"] == "stt_error"
            # the slot was released despite the exception → next flush admitted
            # (it will fail again — but NOT with server_busy)
            ws.send_bytes(silence_pcm(0.3))
            ws.send_json({"v": 1, "type": "flush"})
            err2 = ws.receive_json()
            assert err2["code"] == "stt_error", "slot must be released after failure"


def test_health_exposes_vad_and_queue_state() -> None:
    app = create_app(settings=_tight_settings(), stt=FakeSTT(), vad=PassThroughVAD())
    with TestClient(app) as client:
        body = client.get("/health").json()
        assert body["vad"] == {"provider": "pass-through-vad", "enabled": True, "loaded": True}
        assert body["stt_queue"] == {"queued": 0, "max": 16}
