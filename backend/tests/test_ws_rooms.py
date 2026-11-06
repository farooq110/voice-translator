"""WebSocket flow tests for Phase 5 — two-participant rooms (ADR-0014).

Runs the REAL endpoint with fake STT/translation/TTS providers and asserts
the master prompt §17 Phase 5 scenarios that are observable at the protocol
level: room creation/joining, transcript broadcast with speaker attribution,
translated-audio routing to ONLY the intended recipient (scenario 11),
original-audio pass-through relay without TTS (scenario 10), same-language
both sides (scenario 5), low confidence (scenario 6), unsupported third
language (scenario 7), provider failure shared as utterance errors
(scenario 9), reconnection/rejoin (scenario 8) and cleanup (linger).

The acoustic feedback-loop scenario (12) is a client-side property — the
server never re-ingests delivered audio (architectural), and the browser
playback guard is unit-tested in the frontend suite.

QA round (2026-11-05): stale-seat eviction tests (BUG-002) — a seat whose
owner went silent (the proxy swallowed the disconnect) is reclaimed when the
SAME participant re-hellos with the room code, and the evicted ghost's
cleanup can never eject the returning participant.
"""

from __future__ import annotations

import time
from collections import deque
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.main import create_app
from app.translation.decision import DecisionEngine
from app.translation.engine import TranslationService
from app.translation.fake import FakeTranslationProvider
from .conftest import FakeSTT, PassThroughVAD, silence_pcm

HOST_HELLO = {
    "v": 1,
    "type": "hello",
    "config": {
        "role": "host",
        "native_language": "ur",
        "preferred_language": "en",
        "audio": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1},
    },
}


def guest_hello(code: str) -> dict:
    return {
        "v": 1,
        "type": "hello",
        "config": {
            "role": "guest",
            "room": code,
            "audio": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1},
        },
    }


class ScriptedSTT(FakeSTT):
    """FakeSTT whose standalone detections are scripted per test — lets ONE
    app serve both participants speaking different languages."""

    def __init__(self, detections: list[tuple[str, float]] | None = None) -> None:
        super().__init__(language="en")
        self._detections: deque[tuple[str, float]] = deque(detections or [])

    def script(self, detections: list[tuple[str, float]]) -> None:
        self._detections.clear()
        self._detections.extend(detections)

    def detect_pcm(self, pcm16: bytes, *, sample_rate: int, channels: int = 1):
        if self._detections:
            return self._detections.popleft()
        return super().detect_pcm(pcm16, sample_rate=sample_rate, channels=channels)


def _settings(**overrides) -> Settings:
    return Settings(
        translation_provider="fake",
        translation_detect_mode="auto",
        tts_provider="fake",
        **overrides,
    )


def _app(stt: FakeSTT, **overrides) -> TestClient:
    return TestClient(
        create_app(stt=stt, vad=PassThroughVAD(), settings=_settings(**overrides))
    )


def _say(ws, seconds: float = 0.5, utterance_id: str | None = None) -> None:
    ws.send_bytes(silence_pcm(seconds))
    flush = {"v": 1, "type": "flush"}
    if utterance_id is not None:
        flush["utterance_id"] = utterance_id
    ws.send_json(flush)


def _receive_until(ws, wanted_type: str, limit: int = 10) -> dict:
    for _ in range(limit):
        event = ws.receive_json()
        if event.get("type") == wanted_type:
            return event
    raise AssertionError(f"no {wanted_type} event arrived")


def _open(client: TestClient):
    return client.websocket_connect("/ws/audio")


@contextmanager
def _open_room():
    """Context manager: host + guest over one app (host ur→en), fully
    handshaked. Yields (client, host, guest, stt) — script detections with
    `stt.script([...])` before each utterance."""
    stt = ScriptedSTT()
    client = _app(stt)
    with client as client, _open(client) as host, _open(client) as guest:
        host.send_json(HOST_HELLO)
        ready = host.receive_json()
        assert ready["type"] == "ready"
        code = ready["room"]["code"]
        guest.send_json(guest_hello(code))
        g_ready = guest.receive_json()
        assert g_ready["type"] == "ready"
        assert host.receive_json()["type"] == "peer"  # guest joined
        yield client, host, guest, stt


# --------------------------------------------------------------------- rooms


def test_host_hello_creates_room_with_invite_code() -> None:
    with _app(ScriptedSTT([("ur", 0.97)])) as client, _open(client) as host:
        host.send_json(HOST_HELLO)
        ready = _receive_until(host, "ready")
        room = ready["room"]
        assert len(room["code"]) == 6
        assert room["role"] == "host"
        assert room["peers"] == 0
        assert room["native_language"] == "ur"
        assert room["preferred_language"] == "en"


def test_guest_joins_configures_nothing_and_host_is_notified() -> None:
    with _app(ScriptedSTT([("ur", 0.97)])) as client, _open(client) as host:
        host.send_json(HOST_HELLO)
        code = _receive_until(host, "ready")["room"]["code"]
        with _open(client) as guest:
            guest.send_json(guest_hello(code))
            g_ready = _receive_until(guest, "ready")
            # The guest configures NOTHING — the room's pair comes from the host.
            assert g_ready["room"]["role"] == "guest"
            assert g_ready["room"]["peers"] == 1
            assert g_ready["room"]["native_language"] == "ur"
            assert g_ready["room"]["preferred_language"] == "en"
            joined = _receive_until(host, "peer")
            assert joined == {"type": "peer", "v": 1, "event": "joined", "role": "guest", "peers": 1}


def test_guest_with_unknown_code_is_rejected() -> None:
    with _app(ScriptedSTT([("ur", 0.97)])) as client, _open(client) as ws:
        ws.send_json(guest_hello("ZZZZZZ"))
        err = _receive_until(ws, "error")
        assert err["code"] == "room_not_found"
        assert err["fatal"] is True


def test_guest_without_code_is_rejected() -> None:
    with _app(ScriptedSTT([("ur", 0.97)])) as client, _open(client) as ws:
        ws.send_json(
            {
                "v": 1,
                "type": "hello",
                "config": {
                    "role": "guest",
                    "audio": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1},
                },
            }
        )
        err = _receive_until(ws, "error")
        assert err["code"] == "room_required"


def test_second_guest_is_rejected_room_full() -> None:
    stt = ScriptedSTT([("ur", 0.97)])
    with _app(stt) as client, _open(client) as host:
        host.send_json(HOST_HELLO)
        code = _receive_until(host, "ready")["room"]["code"]
        with _open(client) as guest:
            guest.send_json(guest_hello(code))
            assert _receive_until(guest, "ready")["type"] == "ready"
            assert host.receive_json()["type"] == "peer"
            with _open(client) as third:
                third.send_json(guest_hello(code))
                err = _receive_until(third, "error")
                assert err["code"] == "room_full"


def test_host_hello_without_languages_is_rejected() -> None:
    with _app(ScriptedSTT([("ur", 0.97)])) as client, _open(client) as ws:
        ws.send_json(
            {
                "v": 1,
                "type": "hello",
                "config": {
                    "role": "host",
                    "audio": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1},
                },
            }
        )
        assert _receive_until(ws, "error")["code"] == "missing_languages"


# ------------------------------------------------------------------ routing


def test_host_native_speech_reaches_only_guest_as_voice() -> None:
    """Scenario 1 + 11: host speaks native ur → the guest receives transcript,
    translation AND voice; the host receives NO audio (never translated back)."""
    with _open_room() as (client, host, guest, stt):
        stt.script([("ur", 0.97)])
        _say(host, utterance_id="h_0001_aaaaaa")

        host_tr = _receive_until(host, "transcription")
        assert host_tr["speaker"] == "host"
        assert host_tr["decision"]["action"] == "translate"
        host_tl = _receive_until(host, "translation")
        assert host_tl["speaker"] == "host"
        assert host_tl["text"].startswith("[fake:ur→en]")

        guest_tr = _receive_until(guest, "transcription")
        assert guest_tr["id"] == "h_0001_aaaaaa"
        assert guest_tr["speaker"] == "host"
        guest_tl = _receive_until(guest, "translation")
        assert guest_tl["id"] == "h_0001_aaaaaa"
        audio = _receive_until(guest, "audio")
        assert audio["id"] == "h_0001_aaaaaa"
        assert audio["language"] == "en"
        assert audio["speaker"] == "host"
        assert audio["sample_rate"] == 22_050
        pcm = guest.receive_bytes()
        assert len(pcm) == audio["bytes"] and len(pcm) > 0

        # Scenario 11: the host's stream carries NOTHING after its translation.
        host.send_json({"v": 1, "type": "ping", "ts": 5})
        assert host.receive_json() == {"type": "pong", "v": 1, "ts": 5}


def test_guest_preferred_speech_reaches_only_host_as_voice() -> None:
    """Scenario 2 + 11: guest speaks preferred en → the host receives the
    Urdu voice; the guest receives NO audio."""
    with _open_room() as (client, host, guest, stt):
        stt.script([("en", 0.97)])
        _say(guest, utterance_id="g_0001_bbbbbb")

        # Broadcasts land on the host first (insertion order) — the host's
        # stream is [transcription, translation, audio+binary].
        host_tr = _receive_until(host, "transcription")
        assert host_tr["speaker"] == "guest"
        assert host_tr["decision"]["action"] == "translate"
        assert host_tr["decision"]["reason"] == "preferred_language"
        _receive_until(host, "translation")
        host_audio = _receive_until(host, "audio")
        assert host_audio["speaker"] == "guest"
        assert host_audio["language"] == "ur"
        assert len(host.receive_bytes()) == host_audio["bytes"]

        guest_tr = _receive_until(guest, "transcription")
        assert guest_tr["id"] == "g_0001_bbbbbb"
        _receive_until(guest, "translation")
        guest.send_json({"v": 1, "type": "ping", "ts": 6})
        assert guest.receive_json() == {"type": "pong", "v": 1, "ts": 6}


def test_third_language_relayed_as_original_audio_without_tts() -> None:
    """Scenarios 3/4/10: a third language passes through — the peer receives
    the ORIGINAL captured audio (relay, 16 kHz), nobody receives TTS audio,
    nobody receives a translation."""
    with _open_room() as (client, host, guest, stt):
        stt.script([("es", 0.97)])
        _say(host, utterance_id="h_0002cccccc")

        host_tr = _receive_until(host, "transcription")
        assert host_tr["decision"]["action"] == "passthrough"
        assert host_tr["decision"]["reason"] == "third_language"

        guest_tr = _receive_until(guest, "transcription")
        assert guest_tr["decision"]["action"] == "passthrough"
        relay = _receive_until(guest, "relay")
        assert relay["id"] == "h_0002cccccc"
        assert relay["speaker"] == "host"
        assert relay["language"] == "es"
        assert relay["sample_rate"] == 16_000
        pcm = guest.receive_bytes()
        assert len(pcm) == relay["bytes"] and len(pcm) > 0

        # No translation, no TTS on either side (Rules 3+4).
        host.send_json({"v": 1, "type": "ping", "ts": 7})
        assert host.receive_json() == {"type": "pong", "v": 1, "ts": 7}
        guest.send_json({"v": 1, "type": "ping", "ts": 8})
        assert guest.receive_json() == {"type": "pong", "v": 1, "ts": 8}


def test_same_language_from_both_sides_translates_both_ways() -> None:
    """Scenario 5: both speak ur (the native language) — each utterance is
    translated to en and delivered to the OTHER participant."""
    with _open_room() as (client, host, guest, stt):
        stt.script([("ur", 0.97)])
        _say(host, utterance_id="h_0003dddddd")  # host ur → guest en voice
        _receive_until(host, "translation")
        _receive_until(guest, "audio")
        guest.receive_bytes()  # drain the binary pair of the first utterance

        stt.script([("ur", 0.97)])
        _say(guest, utterance_id="g_0002eeeeee")  # guest ur → host en voice
        host_tr = _receive_until(host, "transcription")
        assert host_tr["speaker"] == "guest"
        host_tl = _receive_until(host, "translation")
        assert host_tl["text"].startswith("[fake:ur→en]")
        host_audio = _receive_until(host, "audio")
        assert host_audio["speaker"] == "guest"
        guest_tl = _receive_until(guest, "translation")
        assert guest_tl["id"] == "g_0002eeeeee"


def test_low_confidence_detection_passes_through() -> None:
    """Scenario 6: a below-threshold non-native label → honest low-confidence
    pass-through on BOTH sides, original audio relayed, no TTS."""
    stt = ScriptedSTT([("fr", 0.40)])
    with _app(stt) as client, _open(client) as host:
        host.send_json(HOST_HELLO)
        code = _receive_until(host, "ready")["room"]["code"]
        with _open(client) as guest:
            guest.send_json(guest_hello(code))
            assert _receive_until(guest, "ready")["type"] == "ready"
            assert host.receive_json()["type"] == "peer"
            _say(host, utterance_id="h_0004ffffff")
            tr = _receive_until(host, "transcription")
            assert tr["decision"]["action"] == "passthrough"
            assert tr["decision"]["reason"] == "low_confidence"
            guest_tr = _receive_until(guest, "transcription")
            assert guest_tr["decision"]["reason"] == "low_confidence"
            assert _receive_until(guest, "relay")["language"] == "ur"


def test_confident_third_language_is_unsupported_pass_through() -> None:
    """Scenario 7: a confident language outside the configured pair passes
    through untouched (no provider call, no TTS)."""
    stt = ScriptedSTT([("fr", 0.95)])
    with _app(stt) as client, _open(client) as host:
        host.send_json(HOST_HELLO)
        code = _receive_until(host, "ready")["room"]["code"]
        with _open(client) as guest:
            guest.send_json(guest_hello(code))
            assert _receive_until(guest, "ready")["type"] == "ready"
            assert host.receive_json()["type"] == "peer"
            _say(host, utterance_id="h_0005aaaaaa")
            tr = _receive_until(host, "transcription")
            assert tr["decision"]["action"] == "passthrough"
            assert tr["decision"]["reason"] == "third_language"
            assert tr["language"] == "fr"


def test_solo_room_keeps_phase4_local_playback_shape() -> None:
    """A host with no guest yet hears its own translation — the exact
    Phase 1–4 behavior (no `speaker` tag, no relay, audio to self)."""
    stt = ScriptedSTT([("ur", 0.97)])
    with _app(stt) as client, _open(client) as host:
        host.send_json(HOST_HELLO)
        assert _receive_until(host, "ready")["room"]["peers"] == 0
        _say(host, utterance_id="h_0006bbbbbb")
        tr = _receive_until(host, "transcription")
        # Room mode tags the speaker; the SOLO audio fallback stays untagged
        # (local playback — the exact Phase 1–4 shape for the audio pair).
        assert tr["speaker"] == "host"
        _receive_until(host, "translation")
        audio = _receive_until(host, "audio")
        assert "speaker" not in audio
        assert len(host.receive_bytes()) == audio["bytes"]


def test_translation_provider_failure_is_shared_as_utterance_error() -> None:
    """Scenario 9: a failing translation provider answers a non-fatal,
    utterance-scoped error on BOTH sides (the text is shared) — the original
    transcript is kept and no audio is produced."""
    stt = ScriptedSTT([("ur", 0.97)])
    failing = TranslationService(
        provider=FakeTranslationProvider(fail=True),
        decision_engine=DecisionEngine(),
        detect_mode="auto",
    )
    app = create_app(
        stt=stt,
        vad=PassThroughVAD(),
        translator=failing,
        settings=_settings(),
    )
    with TestClient(app) as client, _open(client) as host:
        host.send_json(HOST_HELLO)
        code = _receive_until(host, "ready")["room"]["code"]
        with _open(client) as guest:
            guest.send_json(guest_hello(code))
            assert _receive_until(guest, "ready")["type"] == "ready"
            assert host.receive_json()["type"] == "peer"
            _say(host, utterance_id="h_0007cccccc")
            tr = _receive_until(host, "transcription")
            assert tr["decision"]["action"] == "translate"
            host_err = _receive_until(host, "error")
            assert host_err["code"] == "translation_failed"
            assert host_err["utterance_id"] == "h_0007cccccc"
            assert host_err["fatal"] is False
            guest_err = _receive_until(guest, "error")
            assert guest_err["code"] == "translation_failed"
            assert guest_err["speaker"] == "host"
            # No audio anywhere (nothing was translated).
            host.send_json({"v": 1, "type": "ping", "ts": 9})
            assert host.receive_json() == {"type": "pong", "v": 1, "ts": 9}


# ------------------------------------------------- reconnection and cleanup


def test_host_reconnect_rejoins_same_room_and_routing_resumes() -> None:
    """Scenario 8: the host's connection drops — the guest is told, the host
    rejoins with the SAME code, and routing resumes both ways."""
    stt = ScriptedSTT([("ur", 0.97), ("ur", 0.97)])
    with _app(stt) as client, _open(client) as h:
        h.send_json(HOST_HELLO)
        ready = _receive_until(h, "ready")
        code = ready["room"]["code"]
        with _open(client) as g:
            g.send_json(guest_hello(code))
            assert _receive_until(g, "ready")["type"] == "ready"
            assert _receive_until(h, "peer")["event"] == "joined"

            h.close()  # network drop
            left = _receive_until(g, "peer")
            assert left == {"type": "peer", "v": 1, "event": "left", "role": "host", "peers": 0}

            with _open(client) as h2:  # rejoin within the linger window
                h2.send_json({**HOST_HELLO, "config": {**HOST_HELLO["config"], "room": code}})
                h2_ready = _receive_until(h2, "ready")
                assert h2_ready["room"]["code"] == code  # SAME room
                assert h2_ready["room"]["peers"] == 1
                assert _receive_until(g, "peer")["event"] == "joined"

                # Routing resumes: host speaks → guest receives the voice.
                stt.script([("ur", 0.97)])
                _say(h2, utterance_id="h_0008dddddd")
                _receive_until(g, "transcription")
                _receive_until(g, "translation")
                audio = _receive_until(g, "audio")
                assert audio["speaker"] == "host"


def test_room_lingers_then_is_destroyed() -> None:
    """Cleanup: while the linger window runs, a rejoin works; after it
    expires the room is gone (join answers room_not_found) and /health
    reports zero rooms."""
    stt = ScriptedSTT([("ur", 0.97)])
    with _app(stt, room_linger_seconds=0.3) as client, _open(client) as host:
        host.send_json(HOST_HELLO)
        code = _receive_until(host, "ready")["room"]["code"]
        health = client.get("/health").json()
        assert health["rooms"]["active"] == 1

        host.close()  # last member leaves → linger starts
        time.sleep(0.05)
        with _open(client) as late_host:  # still within the window
            late_host.send_json({**HOST_HELLO, "config": {**HOST_HELLO["config"], "room": code}})
            # A stale host code may either rejoin (window open) or create a
            # fresh room — both are acceptable; the guest path must fail
            # AFTER expiry. Verify the rejoin path here.
            ready = _receive_until(late_host, "ready")
            assert ready["room"]["code"] == code

        time.sleep(0.5)  # linger (0.3 s) expires with the room empty
        with _open(client) as ws:
            ws.send_json(guest_hello(code))
            assert _receive_until(ws, "error")["code"] == "room_not_found"
        health = client.get("/health").json()
        assert health["rooms"]["active"] == 0


# ------------------------------------------- stale-seat eviction (QA BUG-002)


def test_ghost_host_seat_is_evicted_on_rejoin() -> None:
    """QA round BUG-002: a host seat whose connection has gone SILENT (the
    proxy swallowed its disconnect — the socket stays open server-side) is
    reclaimed when the SAME participant re-hellos with the room code,
    instead of answering a fatal room_full for the idle-timeout duration."""
    stt = ScriptedSTT()
    # stale threshold 0 → any silence qualifies (no waiting in tests).
    with _app(stt, room_stale_seat_seconds=0.0) as client, _open(client) as ghost:
        ghost.send_json(HOST_HELLO)
        code = _receive_until(ghost, "ready")["room"]["code"]
        with _open(client) as guest:
            guest.send_json(guest_hello(code))
            assert _receive_until(guest, "ready")["type"] == "ready"
            assert _receive_until(ghost, "peer")["event"] == "joined"

            # Host tab crashes with no close frame reaching the server; the
            # participant reopens the invite link (new socket, same code).
            with _open(client) as returning:
                returning.send_json(
                    {**HOST_HELLO, "config": {**HOST_HELLO["config"], "room": code}}
                )
                ready = _receive_until(returning, "ready")
                assert ready["room"]["code"] == code

                # The ghost is told why it lost the room, then closed.
                err = _receive_until(ghost, "error")
                assert err["code"] == "seat_taken"
                assert err["fatal"] is True
                with pytest.raises(WebSocketDisconnect):
                    while True:
                        ghost.receive_json()

                # The guest saw the ghost leave and the host return.
                left = _receive_until(guest, "peer")
                assert left["event"] == "left" and left["role"] == "host"
                joined = _receive_until(guest, "peer")
                assert joined["event"] == "joined" and joined["role"] == "host"

                # Membership: exactly the returning host + the guest.
                room = client.app.state.rooms.get(code)
                assert set(room.members.keys()) == {"host", "guest"}

                # Routing resumes for the returning host.
                stt.script([("ur", 0.97)])
                _say(returning, utterance_id="h_qa2ghost")
                tr = _receive_until(guest, "transcription")
                assert tr["speaker"] == "host"


def test_fresh_host_seat_is_not_evicted() -> None:
    """A room_full for a LIVE seat owner (recent frames) stays fatal — a
    second tab / third device cannot steal the seat; eviction only applies
    to silent ghosts."""
    stt = ScriptedSTT()
    with _app(stt, room_stale_seat_seconds=10_000.0) as client, _open(client) as live:
        live.send_json(HOST_HELLO)
        code = _receive_until(live, "ready")["room"]["code"]
        with _open(client) as second:
            second.send_json(
                {**HOST_HELLO, "config": {**HOST_HELLO["config"], "room": code}}
            )
            err = _receive_until(second, "error")
            assert err["code"] == "room_full"
            assert err["fatal"] is True
            with pytest.raises(WebSocketDisconnect):
                second.receive_json()
        # The live owner is untouched.
        room = client.app.state.rooms.get(code)
        assert set(room.members.keys()) == {"host"}


def test_ghost_guest_seat_is_evicted_on_rejoin() -> None:
    """Same reclaim for the guest seat — the invited participant can reopen
    the invite link after a crash without waiting out the idle timeout."""
    stt = ScriptedSTT()
    with _app(stt, room_stale_seat_seconds=0.0) as client, _open(client) as host:
        host.send_json(HOST_HELLO)
        code = _receive_until(host, "ready")["room"]["code"]
        with _open(client) as ghost:
            ghost.send_json(guest_hello(code))
            assert _receive_until(ghost, "ready")["type"] == "ready"
            assert _receive_until(host, "peer")["event"] == "joined"

            with _open(client) as returning:
                returning.send_json(guest_hello(code))
                ready = _receive_until(returning, "ready")
                assert ready["room"]["code"] == code
                assert ready["room"]["role"] == "guest"

                err = _receive_until(ghost, "error")
                assert err["code"] == "seat_taken"
                assert err["fatal"] is True

                # Host saw exactly one leave + one join (no duplicates).
                left = _receive_until(host, "peer")
                assert left["event"] == "left" and left["role"] == "guest"
                joined = _receive_until(host, "peer")
                assert joined["event"] == "joined" and joined["role"] == "guest"

                room = client.app.state.rooms.get(code)
                assert set(room.members.keys()) == {"host", "guest"}


def test_evicted_ghost_cleanup_does_not_eject_the_new_member() -> None:
    """Regression guard: the reclaimed ghost's server-side handler keeps
    looping until its idle timeout; when it finally ends, its cleanup must
    NOT remove the returning participant nor fire a bogus peer 'left'."""
    stt = ScriptedSTT()
    with _app(stt, room_stale_seat_seconds=0.0) as client, _open(client) as ghost:
        ghost.send_json(HOST_HELLO)
        code = _receive_until(ghost, "ready")["room"]["code"]
        with _open(client) as guest:
            guest.send_json(guest_hello(code))
            assert _receive_until(guest, "ready")["type"] == "ready"
            with _open(client) as returning:
                returning.send_json(
                    {**HOST_HELLO, "config": {**HOST_HELLO["config"], "room": code}}
                )
                assert _receive_until(returning, "ready")["type"] == "ready"
                assert _receive_until(ghost, "error")["code"] == "seat_taken"

                # Drain the guest's expected room events: the eviction notice
                # and the returning host's join.
                assert _receive_until(guest, "peer")["event"] == "left"
                assert _receive_until(guest, "peer")["event"] == "joined"

                # The ghost's handler ends (the server closed its socket).
                ghost.close()
                time.sleep(0.05)

                # Membership unchanged: the returning host keeps the seat.
                room = client.app.state.rooms.get(code)
                assert set(room.members.keys()) == {"host", "guest"}

                # ...and the guest sees NO bogus host 'left' around host speech.
                stt.script([("ur", 0.97)])
                _say(returning, utterance_id="h_qa2guard")
                events = [guest.receive_json() for _ in range(3)]
                bogus_lefts = [
                    e for e in events
                    if e.get("type") == "peer" and e.get("event") == "left"
                ]
                assert bogus_lefts == []
                assert any(e.get("type") == "transcription" for e in events)


def test_duplicate_hello_in_room_reacks_with_room_info() -> None:
    """BUG-601 in the room shape: a repeated host hello (the client repeats
    it until `ready` arrives) is re-acked with the SAME session and room —
    never an error, never a second room."""
    with _app(ScriptedSTT()) as client, _open(client) as host:
        host.send_json(HOST_HELLO)
        first = _receive_until(host, "ready")
        host.send_json(HOST_HELLO)
        second = _receive_until(host, "ready")
        assert second["session_id"] == first["session_id"]
        assert second["room"]["code"] == first["room"]["code"]
        assert second["room"]["role"] == "host"
