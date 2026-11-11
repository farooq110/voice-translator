"""A FAKE of Zoom's RTMS edge — test double, never used by the product path.

Implements exactly the protocol side `RtmsClient` speaks (ADR-0019):

    signaling socket:
        client  {msg_type: 1, meeting_uuid, rtms_stream_id, signature, media_type}
        server  {msg_type: 2, content: {media_url, status_code: 0}}
        client  {msg_type: 21, …}            → graceful close (recorded)
        server  {msg_type: 12, timestamp}    → expects {msg_type: 13, ts}
    media socket:
        client  {msg_type: 3, media_params: {audio: …}}
        server  {msg_type: 4, status_code: 0}
        client  {msg_type: 7, rtms_stream_id}  (CLIENT_READY_ACK — recorded)
        server  {msg_type: 14, content: <b64 PCM16>}  (audio to the client)
        server  {msg_type: 12, timestamp}    → expects {msg_type: 13, ts}

It verifies the client's signature against the credentials it was given
(so a signature regression FAILS the test), answers keep-alives checks both
ways, and streams L16 audio pushed via `stream_audio()`. The handshake
response deliberately uses the `content.media_url` shape.

TEST HONESTY (AGENT.md §6): this double stands in for ZOOM'S TRANSPORT ONLY.
Every claim about the translation pipeline (Silero VAD, Whisper, decision
engine, OPUS-MT, Piper) is made against the REAL implementations — the fake
never touches them.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Callable

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .signing import rtms_join_signature

logger = logging.getLogger(__name__)

MSG_SIGNALING_HAND_SHAKE_REQ = 1
MSG_SIGNALING_HAND_SHAKE_RESP = 2
MSG_DATA_HAND_SHAKE_REQ = 3
MSG_DATA_HAND_SHAKE_RESP = 4
MSG_CLIENT_READY_ACK = 7
MSG_KEEP_ALIVE_REQ = 12
MSG_KEEP_ALIVE_RESP = 13
MSG_MEDIA_DATA_AUDIO = 14
MSG_STREAM_CLOSE_REQ = 21

STATUS_OK = 0
STATUS_INVALID_SIGNATURE = 3


class FakeZoomRTMS:
    """One fake RTMS endpoint pair (signaling + media) on ephemeral ports."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        meeting_uuid: str,
        rtms_stream_id: str,
        keepalive_interval_s: float = 1.0,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._meeting_uuid = meeting_uuid
        self._stream_id = rtms_stream_id
        self._keepalive_interval = keepalive_interval_s
        # --- recorded protocol facts (assertions read these) ---
        self.signaling_handshakes: list[dict] = []
        self.media_handshakes: list[dict] = []
        self.ready_acks: list[dict] = []
        self.keepalive_responses = 0
        self.stream_close_reqs: list[dict] = []
        self.audio_frames_sent = 0
        self.signaling_signature_ok: bool | None = None
        self.media_params: dict | None = None
        # --- runtime ---
        self.signaling_url: str | None = None
        self.media_url: str | None = None
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._servers: list[object] = []
        self._signaling_conn: ServerConnection | None = None
        self._media_conn: ServerConnection | None = None

    # ------------------------------------------------------------------ api

    async def start(self) -> None:
        signaling = await serve(self._handle_signaling, "127.0.0.1", port=0)
        media = await serve(self._handle_media, "127.0.0.1", port=0)
        self._servers = [signaling, media]
        self.signaling_url = f"ws://127.0.0.1:{signaling.sockets[0].getsockname()[1]}"
        self.media_url = f"ws://127.0.0.1:{media.sockets[0].getsockname()[1]}"

    async def stop(self) -> None:
        for ws in (self._media_conn, self._signaling_conn):
            if ws is not None:
                try:
                    await ws.close()
                except Exception:  # noqa: BLE001
                    pass
        for server in self._servers:
            server.close()
            await server.wait_closed()

    async def stream_audio(self, pcm: bytes, *, frame_ms: int = 20, speed: float = 4.0) -> None:
        """Push L16 PCM as 20 ms base64 frames at `speed`× realtime pace."""
        frame_bytes = int(16000 * (frame_ms / 1000.0)) * 2
        delay = (frame_ms / 1000.0) / max(speed, 0.1)
        for offset in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
            frame = pcm[offset:offset + frame_bytes]
            await self._audio_queue.put(frame)
            self.audio_frames_sent += 1
            await asyncio.sleep(delay)
        # pad the remainder so no audio is lost
        remainder = len(pcm) % frame_bytes
        if remainder >= 2:
            await self._audio_queue.put(pcm[-(remainder - remainder % 2):])
            self.audio_frames_sent += 1
        await self._audio_queue.put(None)  # end of stream marker

    async def send_session_stopped(self, stop_reason: int = 6) -> None:
        """Inject SESSION_STATE_UPDATE(session_state=STOPPED) on the media socket."""
        await self._audio_queue.put(("json", {
            "msg_type": 9,
            "content": {"session_state": 5, "stop_reason": stop_reason},
        }))

    async def send_raw_media(self, message: dict) -> None:
        """Inject an arbitrary JSON media message (edge-case tests)."""
        await self._audio_queue.put(("json", message))

    async def send_binary_media(self, data: bytes) -> None:
        """Inject a BINARY frame on the media socket (defensive-decode path)."""
        await self._audio_queue.put(("binary", data))

    async def send_dict_content_audio(self, pcm: bytes) -> None:
        """Audio wrapped as {"data": "<b64>"} (Zoom sample variant)."""
        await self.send_raw_media({
            "msg_type": MSG_MEDIA_DATA_AUDIO,
            "content": {"data": base64.b64encode(pcm).decode("ascii")},
        })

    # ------------------------------------------------------------ signaling

    async def _handle_signaling(self, ws: ServerConnection) -> None:
        self._signaling_conn = ws
        try:
            raw = await ws.recv()
            handshake = json.loads(raw)
            self.signaling_handshakes.append(handshake)
            expected = rtms_join_signature(
                self._client_id, self._meeting_uuid, self._stream_id, self._client_secret
            )
            self.signaling_signature_ok = handshake.get("signature") == expected
            if not self.signaling_signature_ok or handshake.get("meeting_uuid") != self._meeting_uuid:
                await ws.send(json.dumps({
                    "msg_type": MSG_SIGNALING_HAND_SHAKE_RESP,
                    "content": {"status_code": STATUS_INVALID_SIGNATURE},
                }))
                return
            await ws.send(json.dumps({
                "msg_type": MSG_SIGNALING_HAND_SHAKE_RESP,
                "content": {"media_url": self.media_url, "status_code": STATUS_OK},
            }))
            # Keep-alive + close-request loop (Zoom's documented behavior).
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self._keepalive_interval)
                except asyncio.TimeoutError:
                    await ws.send(json.dumps({
                        "msg_type": MSG_KEEP_ALIVE_REQ, "timestamp": asyncio.get_running_loop().time(),
                    }))
                    continue
                msg = json.loads(raw)
                if msg.get("msg_type") == MSG_STREAM_CLOSE_REQ:
                    self.stream_close_reqs.append(msg)
                    return
        except ConnectionClosed:
            pass

    # --------------------------------------------------------------- media

    async def _handle_media(self, ws: ServerConnection) -> None:
        self._media_conn = ws
        sender: asyncio.Task[None] | None = None
        try:
            raw = await ws.recv()
            handshake = json.loads(raw)
            self.media_handshakes.append(handshake)
            expected = rtms_join_signature(
                self._client_id, self._meeting_uuid, self._stream_id, self._client_secret
            )
            if handshake.get("signature") != expected:
                await ws.send(json.dumps({"msg_type": MSG_DATA_HAND_SHAKE_RESP, "status_code": STATUS_INVALID_SIGNATURE}))
                return
            params = (handshake.get("media_params") or {}).get("audio")
            self.media_params = params
            await ws.send(json.dumps({"msg_type": MSG_DATA_HAND_SHAKE_RESP, "status_code": STATUS_OK}))
            # CLIENT_READY_ACK (bounded wait — a client that never acks is a bug).
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            ready = json.loads(raw)
            if ready.get("msg_type") != MSG_CLIENT_READY_ACK:
                logger.warning("fake_zoom: expected CLIENT_READY_ACK, got %s", ready.get("msg_type"))
                return
            self.ready_acks.append(ready)
            sender = asyncio.create_task(self._audio_sender(ws))
            # Keep-alive + response accounting.
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self._keepalive_interval)
                except asyncio.TimeoutError:
                    await ws.send(json.dumps({
                        "msg_type": MSG_KEEP_ALIVE_REQ, "timestamp": asyncio.get_running_loop().time(),
                    }))
                    continue
                msg = json.loads(raw)
                if msg.get("msg_type") == MSG_KEEP_ALIVE_RESP:
                    self.keepalive_responses += 1
        except (ConnectionClosed, asyncio.TimeoutError):
            pass
        finally:
            if sender is not None:
                sender.cancel()

    async def _audio_sender(self, ws: ServerConnection) -> None:
        while True:
            item = await self._audio_queue.get()
            if item is None:
                await self._audio_queue.put(None)  # keep the marker for late consumers
                return
            if isinstance(item, tuple):  # injected ("json", msg) / ("binary", data)
                kind, payload = item
                if kind == "json":
                    await ws.send(json.dumps(payload))
                else:
                    await ws.send(payload)
                continue
            await ws.send(json.dumps({
                "msg_type": MSG_MEDIA_DATA_AUDIO,
                "timestamp": asyncio.get_running_loop().time(),
                "content": base64.b64encode(item).decode("ascii"),
            }))


async def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 10.0,
    interval: float = 0.05,
    what: str = "condition",
) -> None:
    """Poll until `predicate()` is true — assert-friendly helper for tests."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise TimeoutError(f"wait_until: {what} not satisfied within {timeout}s")
