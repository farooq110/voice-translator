"""WebSocket protocol flow tests (protocol v1) using the FakeSTT provider.

These exercise the real endpoint logic end-to-end over a TestClient socket:
handshake, binary framing, flush→transcription, error handling, bounded buffers.
"""

from __future__ import annotations


import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDisconnect

from .conftest import FakeSTT, silence_pcm

HELLO = {
    "v": 1,
    "type": "hello",
    "config": {
        "native_language": "ur",
        "preferred_language": "en",
        "audio": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1},
    },
}

HELLO_EN = {
    **HELLO,
    "config": {**HELLO["config"], "native_language": "en", "preferred_language": "en"},
}


def _open(client: TestClient):
    return client.websocket_connect("/ws/audio")


def test_ready_after_hello(client: TestClient) -> None:
    with _open(client) as ws:
        ws.send_json(HELLO)
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["v"] == 1
        assert ready["session_id"].startswith("s_")
        assert ready["audio"] == HELLO["config"]["audio"]
        assert ready["stt"]["provider"] == "fake-stt"


def test_binary_before_hello_is_fatal(client: TestClient) -> None:
    with _open(client) as ws:
        ws.send_bytes(b"\x00\x00")
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "hello_required"
        assert err["fatal"] is True
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.receive_json()


def test_full_utterance_flow(client: TestClient, fake_stt: FakeSTT) -> None:
    with _open(client) as ws:
        ws.send_json(HELLO)
        ready = ws.receive_json()
        assert ready["type"] == "ready"

        ws.send_bytes(silence_pcm(0.5))
        ws.send_json({"v": 1, "type": "flush"})
        tr = ws.receive_json()
        assert tr["type"] == "transcription"
        assert tr["text"] == "fake transcription 1"
        # KI-006: the hello config's native language (ur) must reach the STT
        # as a forced hint and be echoed back as the result language.
        assert fake_stt.last_language_hint == "ur"
        assert tr["language"] == "ur"
        assert 0.0 <= tr["language_probability"] <= 1.0
        assert tr["audio_duration_s"] == pytest.approx(0.5, abs=0.01)
        assert tr["processing_ms"] >= 0
        assert tr["is_final"] is True


def test_regional_native_language_hint_is_reduced_to_primary_subtag(
    client: TestClient, fake_stt: FakeSTT
) -> None:
    """"ur-PK" must be normalized to "ur" — Whisper speaks ISO-639-1."""
    hello_regional = {
        **HELLO,
        "config": {**HELLO["config"], "native_language": "ur-PK"},
    }
    with _open(client) as ws:
        ws.send_json(hello_regional)
        assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(silence_pcm(0.3))
        ws.send_json({"v": 1, "type": "flush"})
        tr = ws.receive_json()
        assert tr["type"] == "transcription"
        assert fake_stt.last_language_hint == "ur"
        assert tr["language"] == "ur"


def test_no_speech_detected_when_stt_returns_empty(client_empty: TestClient) -> None:
    """KI-006: hallucination-only decodes must surface as a recognizable,
    non-fatal error — never as an empty transcription bubble."""
    with _open(client_empty) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(silence_pcm(0.5))
        ws.send_json({"v": 1, "type": "flush"})
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "no_speech_detected"
        assert err["fatal"] is False
        # the connection stays usable
        ws.send_json({"v": 1, "type": "ping", "ts": 7})
        assert ws.receive_json() == {"type": "pong", "v": 1, "ts": 7}


def test_flush_empty_buffer_warns_and_connection_survives(client: TestClient) -> None:
    with _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"v": 1, "type": "flush"})
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "nothing_to_transcribe"
        assert err["fatal"] is False
        # connection must remain usable
        ws.send_json({"v": 1, "type": "ping", "ts": 123})
        pong = ws.receive_json()
        assert pong == {"type": "pong", "v": 1, "ts": 123}


def test_ping_pong_echo(client: TestClient) -> None:
    with _open(client) as ws:
        ws.send_json(HELLO)
        ws.receive_json()
        ws.send_json({"v": 1, "type": "ping", "ts": 42})
        assert ws.receive_json() == {"type": "pong", "v": 1, "ts": 42}


def test_malformed_json_is_non_fatal(client: TestClient) -> None:
    with _open(client) as ws:
        ws.send_json(HELLO)
        ws.receive_json()
        ws.send_text("this is not json")
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "malformed_json"
        assert err["fatal"] is False
        ws.send_json({"v": 1, "type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_wrong_protocol_version_is_fatal(client: TestClient) -> None:
    with _open(client) as ws:
        ws.send_json({**HELLO, "v": 99})
        err = ws.receive_json()
        assert err["code"] == "unsupported_protocol_version" and err["fatal"] is True
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.receive_json()


def test_chunk_too_large_is_non_fatal_and_dropped(client: TestClient) -> None:
    with _open(client) as ws:
        ws.send_json(HELLO)
        ws.receive_json()
        ws.send_bytes(b"\x00" * 262_146)  # > default max_chunk_bytes (262144)
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "chunk_too_large"
        assert err["fatal"] is False
        ws.send_json({"v": 1, "type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_odd_alignment_chunk_rejected(client: TestClient) -> None:
    with _open(client) as ws:
        ws.send_json(HELLO)
        ws.receive_json()
        ws.send_bytes(b"\x00\x00\x01")
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "invalid_chunk_alignment"


def test_unsupported_sample_rate_is_fatal(client: TestClient) -> None:
    bad = {
        "v": 1,
        "type": "hello",
        "config": {
            "native_language": "ur",
            "preferred_language": "en",
            "audio": {"encoding": "pcm_s16le", "sample_rate": 44100, "channels": 1},
        },
    }
    with _open(client) as ws:
        ws.send_json(bad)
        err = ws.receive_json()
        assert err["code"] == "unsupported_sample_rate" and err["fatal"] is True


def test_hello_twice_warns(client: TestClient) -> None:
    with _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        ws.send_json(HELLO)
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "hello_already_sent"
        assert err["fatal"] is False


def test_identical_languages_warns_but_connects(client: TestClient) -> None:
    with _open(client) as ws:
        ws.send_json(HELLO_EN)
        first = ws.receive_json()
        assert first["type"] == "error" and first["code"] == "identical_languages"
        second = ws.receive_json()
        assert second["type"] == "ready"


def test_auto_flush_when_buffer_cap_reached(client: TestClient) -> None:
    """30 s of audio (the default cap) must auto-flush without an explicit flush."""
    with _open(client) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "ready"
        for _ in range(300):  # 300 × 100 ms chunks = 30 s = 960 000 bytes
            ws.send_bytes(silence_pcm(0.1))
        tr = ws.receive_json()
        assert tr["type"] == "transcription"
        assert tr["audio_duration_s"] == pytest.approx(30.0, abs=0.05)
        # connection still healthy afterwards
        ws.send_json({"v": 1, "type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_session_cleaned_up_after_disconnect(client: TestClient) -> None:
    assert client.get("/health").json()["sessions"]["active"] == 0
    with _open(client) as ws:
        ws.send_json(HELLO)
        ws.receive_json()
        assert client.get("/health").json()["sessions"]["active"] == 1
    # after leaving the context the socket is closed; give the server a beat
    for _ in range(20):
        if client.get("/health").json()["sessions"]["active"] == 0:
            break
        import time

        time.sleep(0.05)
    assert client.get("/health").json()["sessions"]["active"] == 0


def test_malformed_binary_after_hello_nonfatal(client: TestClient) -> None:
    """Valid chunks mixed with an invalid one: invalid dropped, valid kept."""
    with _open(client) as ws:
        ws.send_json(HELLO)
        ws.receive_json()
        ws.send_bytes(b"\x01")  # odd alignment → error, dropped
        assert ws.receive_json()["code"] == "invalid_chunk_alignment"
        ws.send_bytes(silence_pcm(0.3))
        ws.send_json({"v": 1, "type": "flush"})
        tr = ws.receive_json()
        # only the valid audio must have been transcribed
        assert tr["audio_duration_s"] == pytest.approx(0.3, abs=0.01)
