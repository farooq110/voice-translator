"""Zoom bridge integration tests — worker + pipeline client + fake RTMS.

Covers the FULL bridge loop in one process: a webhook event starts a worker,
the worker connects its PipelineClient to a fake of OUR /ws/audio (it speaks
the real protocol-v1: hello → ready → binary → flush), then the RtmsClient
connects to the FakeZoomRTMS double and streams meeting audio that must
appear as binary PCM16 uploads on the pipeline connection. Zoom's side is
the only fake — the pipeline protocol here is the real client code.

The webhook routing unit tests (dedupe, unknown stop, payload shapes) run
without any sockets.
"""

from __future__ import annotations

import asyncio
import json
import math
import struct

from websockets.asyncio.server import serve

from app.config import Settings
from app.zoom.bridge import ZoomBridge, extract_stream
from app.zoom.fake_rtms import FakeZoomRTMS, wait_until


def _settings(ws_url: str) -> Settings:
    return Settings(
        zoom_enabled=True,
        zoom_client_id="test-client",
        zoom_client_secret="test-secret",
        zoom_secret_token="",
        zoom_ws_url=ws_url,
        zoom_worker_start_timeout_s=5.0,
        zoom_pause_ms=300,
        zoom_soft_cap_s=5.0,
        zoom_rms_threshold=300,
    )


# ---------------------------------------------------------------- unit tests


def test_extract_stream_accepts_both_payload_shapes():
    flat = {"meeting_uuid": "u1", "rtms_stream_id": "s1", "server_urls": "ws://x"}
    nested = {"account_id": "acc", "object": dict(flat)}
    assert extract_stream(flat) == ("u1", "s1", "ws://x")
    assert extract_stream(nested) == ("u1", "s1", "ws://x")
    assert extract_stream({"object": {"meeting_uuid": "u1"}}) is None
    assert extract_stream(None) is None
    assert extract_stream({"meeting_uuid": "u1", "rtms_stream_id": "s1"}) is None  # no urls


def test_bridge_ignores_unknown_and_malformed_events():
    bridge = ZoomBridge(_settings("ws://127.0.0.1:1/ws/audio"))
    bridge.handle_event("meeting.participant_joined", {})
    bridge.handle_event("meeting.rtms_started", {"object": {}})  # missing fields
    assert bridge.worker_count == 0
    assert bridge.describe()["total_meetings"] == 0


class _StubWorker:
    """White-box double for seeding bridge._workers without a live loop."""

    def __init__(self, done: bool) -> None:
        self.done_flag = done

    @property
    def done(self) -> bool:
        return self.done_flag

    def request_stop(self) -> None:
        self.done_flag = True


def test_bridge_dedupes_rtms_started_for_a_live_worker():
    bridge = ZoomBridge(_settings("ws://127.0.0.1:1/ws/audio"))
    seeded = _StubWorker(done=False)
    bridge._workers["mtg-dup"] = seeded  # type: ignore[assignment]
    event = ("meeting.rtms_started", {"object": {
        "meeting_uuid": "mtg-dup", "rtms_stream_id": "st", "server_urls": "ws://127.0.0.1:1/sig",
    }})
    bridge.handle_event(*event)  # Zoom's duplicate-connection gotcha
    assert bridge._workers["mtg-dup"] is seeded
    assert bridge.worker_count == 1


def test_bridge_stop_for_unknown_meeting_is_noop():
    bridge = ZoomBridge(_settings("ws://127.0.0.1:1/ws/audio"))
    bridge.handle_event("meeting.rtms_stopped", {"object": {"meeting_uuid": "ghost"}})
    assert bridge.worker_count == 0


def test_finished_worker_can_be_restarted():
    """A RETRY of rtms_started after the worker finished starts a fresh one."""

    async def scenario() -> None:
        bridge = ZoomBridge(_settings("ws://127.0.0.1:1/ws/audio"))
        bridge._workers["mtg-retry"] = _StubWorker(done=True)  # type: ignore[assignment]
        bridge.handle_event("meeting.rtms_started", {"object": {
            "meeting_uuid": "mtg-retry", "rtms_stream_id": "st",
            "server_urls": "ws://127.0.0.1:1/sig",
        }})
        # The finished stub was REPLACED by a real worker (unreachable WS →
        # the worker errors fast with the shrunken start timeout).
        worker = bridge._workers["mtg-retry"]
        assert worker is not None and worker is not _StubWorker  # type: ignore[comparison-overlap]
        await wait_until(lambda: worker.done, timeout=10, what="replacement worker failing fast")
        await bridge.aclose()

    asyncio.run(scenario())


# ------------------------------------------------------- integration test


def _sine_pcm(seconds: float, rate: int = 16000, amp: int = 9000) -> bytes:
    frames = int(rate * seconds)
    return b"".join(
        struct.pack("<h", int(amp * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(frames)
    )


class _FakePipelineServer:
    """Minimal fake of the app's /ws/audio (protocol-v1) for bridge tests."""

    def __init__(self) -> None:
        self.hellos: list[dict] = []
        self.audio_bytes = 0
        self.binary_frames = 0
        self.flushes = 0
        self.room_code = "ZTEST7"
        self._server = None

    async def start(self) -> str:
        self._server = await serve(self._handler, "127.0.0.1", port=0)
        port = self._server.sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{port}/ws/audio"

    async def stop(self) -> None:
        self._server.close()
        await self._server.wait_closed()

    async def _handler(self, ws) -> None:
        hello = json.loads(await ws.recv())
        self.hellos.append(hello)
        room = hello["config"].get("room") or self.room_code
        await ws.send(json.dumps({
            "type": "ready", "v": 1, "session_id": "sess-fake",
            "audio": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1},
            "stt": {"provider": "fake", "loaded": True},
            "room": {"code": room, "role": "host", "peers": 0,
                     "native_language": hello["config"]["native_language"],
                     "preferred_language": hello["config"]["preferred_language"]},
        }))
        async for raw in ws:
            if isinstance(raw, bytes):
                self.binary_frames += 1
                self.audio_bytes += len(raw)
            else:
                msg = json.loads(raw)
                if msg.get("type") == "flush":
                    self.flushes += 1


def test_worker_streams_meeting_audio_through_pipeline_client():
    """Full worker loop against BOTH fakes — the Phase 7 core integration."""

    async def scenario() -> dict[str, object]:
        pipeline = _FakePipelineServer()
        ws_url = await pipeline.start()
        fake_zoom = FakeZoomRTMS(
            client_id="test-client", client_secret="test-secret",
            meeting_uuid="mtg-itest-1", rtms_stream_id="stream-1",
            keepalive_interval_s=0.2,
        )
        await fake_zoom.start()

        bridge = ZoomBridge(_settings(ws_url))
        bridge.handle_event("meeting.rtms_started", {"object": {
            "meeting_uuid": "mtg-itest-1",
            "rtms_stream_id": "stream-1",
            "server_urls": fake_zoom.signaling_url,
        }})
        try:
            # The worker must reach `live` (pipeline connected + RTMS phase started).
            await wait_until(
                lambda: bridge._workers["mtg-itest-1"].state == "live",
                timeout=10, what="worker reaching live state",
            )
            worker = bridge._workers["mtg-itest-1"]

            # Both handshakes arrived, signatures verified by the fake.
            await wait_until(
                lambda: len(fake_zoom.signaling_handshakes) == 1
                and len(fake_zoom.media_handshakes) == 1
                and len(fake_zoom.ready_acks) == 1,
                timeout=10, what="RTMS handshake completing",
            )
            assert fake_zoom.signaling_signature_ok is True
            assert fake_zoom.media_handshakes[0]["media_type"] == 1  # audio only
            params = fake_zoom.media_params
            assert params["codec"] == 1 and params["sample_rate"] == 1  # L16, 16 kHz
            assert params["channel"] == 1 and params["data_opt"] == 1  # mono, mixed

            # Stream 2 s of "meeting speech" at 8× realtime (~0.25 s wall).
            # Under the soft cap it BUFFERS (the endpointer only flushes on
            # pause/soft-cap) — assert ingestion at the worker, then stop the
            # meeting and expect the tail flush to carry the audio through.
            await fake_zoom.stream_audio(_sine_pcm(seconds=2.0), speed=8.0)
            await wait_until(
                lambda: worker.bytes_ingested >= 32000,
                timeout=10, what="meeting audio reaching the worker",
            )
            assert worker.room_code == "ZTEST7"
            assert fake_zoom.keepalive_responses >= 1  # client answers keep-alives

            # The pipeline hello identified the bridge as the room's host.
            hello = pipeline.hellos[0]["config"]
            assert hello["role"] == "host"
            assert hello["native_language"] == "ur"
            assert hello["preferred_language"] == "en"
            assert hello["client"]["platform"] == "zoom-bridge"

            # Stop via the webhook path; the tail flush must deliver the
            # buffered meeting audio to the pipeline connection, and the fake
            # must see the graceful close.
            bridge.handle_event("meeting.rtms_stopped", {"object": {"meeting_uuid": "mtg-itest-1"}})
            await wait_until(lambda: worker.done, timeout=10, what="worker finishing")
            assert worker.state == "stopped"
            assert pipeline.binary_frames >= 1
            assert pipeline.audio_bytes >= 32000
            assert pipeline.flushes >= 1  # the tail flush
            await wait_until(
                lambda: len(fake_zoom.stream_close_reqs) == 1,
                timeout=5, what="STREAM_CLOSE_REQ reaching the fake signaling socket",
            )
            return {
                "audio_frames": fake_zoom.audio_frames_sent,
                "pipeline_bytes": pipeline.audio_bytes,
                "keepalives_answered": fake_zoom.keepalive_responses,
            }
        finally:
            await bridge.aclose()
            await fake_zoom.stop()
            await pipeline.stop()

    result = asyncio.run(scenario())
    # The endpointer must have flushed at least one segment — audio actually flowed.
    assert result["pipeline_bytes"] > 0
