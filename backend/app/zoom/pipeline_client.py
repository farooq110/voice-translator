"""Protocol-v1 client that feeds Zoom meeting audio into OUR pipeline (Phase 7).

The Zoom bridge does NOT reimplement the STT/decision/translation/TTS logic —
it connects to the app's own `/ws/audio` endpoint as an ordinary protocol-v1
client (AGENT.md §4: the translation engine stays independent of
meeting-integration adapters). Playing the `host` role, it:

1. says hello with the bridge's configured language pair
   (`zoom_native_language` → `zoom_preferred_language`) and the coarse
   platform tag `zoom-bridge`;
2. streams meeting audio as binary PCM16/16 kHz/mono frames — the exact
   format a browser uploads;
3. flushes completed segments (the endpointer lives in the bridge) so the
   server's Silero VAD + utterance planner can do their normal work;
4. receives `transcription`/`translation`/`audio`/`peer` events like any
   browser: transcript text is NEVER logged above DEBUG (privacy rule), and
   `audio` events are ignored — the human listener hears them by joining the
   room with the invite link, exactly as in a two-browser session (Phase 5).

The room the bridge owns is a normal two-participant room: the bridge is the
host, the human joins as the guest. Reconnects re-hello with the same room
code (the Phase 5 rejoin path, QA-hardened in v0.5.1).

Bounded by design: the send queue holds at most `queue_max` segments and
drops the NEWEST segment when full (a stalled consumer must not block the
RTMS keep-alive loop — Zoom closes a silent media socket in ~65 s).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from ..models.protocol import PROTOCOL_VERSION

logger = logging.getLogger(__name__)

_CLOSE_NORMAL = 1000


class PipelineClient:
    """Minimal, reconnecting protocol-v1 client for the app's own WS API."""

    def __init__(
        self,
        *,
        url: str,
        native_language: str,
        preferred_language: str,
        platform_tag: str = "zoom-bridge",
        queue_max: int = 16,
        ping_interval_s: float = 10.0,
        reconnect_max_s: float = 30.0,
    ) -> None:
        self._url = url
        self._native = native_language
        self._preferred = preferred_language
        self._platform = platform_tag
        self._ping_interval = ping_interval_s
        self._reconnect_max = reconnect_max_s
        self._queue: asyncio.Queue[tuple[str, bytes | None]] = asyncio.Queue(maxsize=max(1, queue_max))
        self._stopped = asyncio.Event()
        self._connected = asyncio.Event()
        self.room_code: str | None = None
        self.session_id: str | None = None
        # --- counters (health/REST visibility) ---
        self.segments_dropped = 0
        self.segments_sent = 0
        self.flushes_sent = 0
        self.bytes_sent = 0
        self.transcriptions = 0
        self.translations = 0
        self.audio_events = 0
        self.reconnects = 0
        self.last_connected_at: float | None = None

    # ------------------------------------------------------------------ api

    def enqueue_segment(self, pcm: bytes) -> None:
        """Queue one completed audio segment for upload (drop-on-full).

        The queue entry is the segment AND its flush: the browser triggers
        transcription with an explicit flush message, so the bridge does the
        same — one binary upload followed by one flush, atomically ordered by
        the queue. Dropping on full drops BOTH (a flush without its audio
        would transcribe the wrong buffer).
        """
        if self._stopped.is_set() or not pcm:
            return
        try:
            self._queue.put_nowait(("segment", pcm))
        except asyncio.QueueFull:
            self.segments_dropped += 1

    def stop(self) -> None:
        """Ask the run loop to finish after draining what it can."""
        self._stopped.set()
        try:
            self._queue.put_nowait(("stop", None))  # wake the sender so it can exit
        except asyncio.QueueFull:
            pass  # the sender observes _stopped within one ping interval

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    async def wait_connected(self, timeout: float = 20.0) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    # ------------------------------------------------------------------ run

    async def run(self) -> None:
        """Connect → hello → stream until stop(); reconnect with backoff."""
        backoff = 1.0
        while not self._stopped.is_set():
            try:
                async with ws_connect(self._url, open_timeout=15) as ws:
                    await self._handshake(ws)
                    self._connected.set()
                    self.last_connected_at = time.time()
                    backoff = 1.0
                    # Sender (uploads) and receiver (events) race the stop
                    # event — stop() must end the loop even when the server
                    # keeps the socket open (bounded shutdown).
                    receiver = asyncio.create_task(self._receiver_loop(ws))
                    sender = asyncio.create_task(self._sender_loop(ws))
                    stop_wait = asyncio.create_task(self._stopped.wait())
                    await asyncio.wait(
                        {receiver, sender, stop_wait}, return_when=asyncio.FIRST_COMPLETED
                    )
                    stop_wait.cancel()
                    for task in (receiver, sender):
                        task.cancel()
                    await asyncio.gather(receiver, sender, return_exceptions=True)
                    self._connected.clear()
            except (ConnectionClosed, OSError, asyncio.TimeoutError) as exc:
                self._connected.clear()
                if self._stopped.is_set():
                    return
                logger.warning("zoom.pipeline.disconnected (%s) — reconnecting", exc)
            if self._stopped.is_set():
                return
            self.reconnects += 1
            # Stop-aware backoff sleep (a stop() must not wait it out).
            try:
                await asyncio.wait_for(asyncio.shield(self._stopped.wait()), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, self._reconnect_max)

    # ------------------------------------------------------------- protocol

    def _hello_payload(self) -> str:
        return json.dumps({
            "type": "hello",
            "v": PROTOCOL_VERSION,
            "config": {
                "native_language": self._native,
                "preferred_language": self._preferred,
                "role": "host",
                # Rejoin our own room after a reconnect (Phase 5 linger).
                "room": self.room_code,
                "client": {"platform": self._platform},
                "audio": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1},
            },
        })

    async def _handshake(self, ws: object) -> None:
        await ws.send(self._hello_payload())  # type: ignore[attr-defined]
        # The endpoint re-acks repeated hellos (BUG-601 hardening), and the
        # hello watchdog tolerates delay — a bounded wait keeps us honest.
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=15)  # type: ignore[attr-defined]
            msg = json.loads(raw)
            if msg.get("type") == "ready":
                self.session_id = msg.get("session_id")
                room = msg.get("room") or {}
                if room.get("code"):
                    self.room_code = room["code"]
                logger.info(
                    "zoom.pipeline.ready session=%s room=%s native=%s preferred=%s",
                    self.session_id, self.room_code, self._native, self._preferred,
                )
                return
            if msg.get("type") == "error":
                raise OSError(f"hello rejected: {msg.get('code')} — {msg.get('message')}")

    async def _sender_loop(self, ws: object) -> None:
        """Drain the segment queue; heartbeat between segments.

        The server refreshes a seat's `last_seen` on EVERY received frame
        (v0.5.1 hardening), so the 10 s ping both keeps the socket past the
        180 s idle timeout and keeps the seat alive for the human guest.
        """
        while not self._stopped.is_set():
            try:
                kind, payload = await asyncio.wait_for(self._queue.get(), timeout=self._ping_interval)
            except asyncio.TimeoutError:
                await ws.send(json.dumps({"type": "ping", "v": PROTOCOL_VERSION}))  # type: ignore[attr-defined]
                continue
            if kind == "stop":
                continue
            if kind == "segment":
                assert payload is not None
                await ws.send(payload)  # type: ignore[attr-defined]
                self.segments_sent += 1
                self.bytes_sent += len(payload)
                await ws.send(json.dumps({"type": "flush", "v": PROTOCOL_VERSION}))  # type: ignore[attr-defined]
                self.flushes_sent += 1

    async def _receiver_loop(self, ws: object) -> None:
        async for raw in ws:
            if isinstance(raw, bytes):
                continue  # binary server→client frames only pair with audio metadata, ignored here
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_type = msg.get("type")
            if msg_type == "transcription":
                self.transcriptions += 1
                logger.debug(
                    "zoom.pipeline.transcription speaker=%s lang=%s (text not logged)",
                    msg.get("speaker"), msg.get("language"),
                )
            elif msg_type == "translation":
                self.translations += 1
            elif msg_type == "audio":
                self.audio_events += 1  # routed to the human guest by the room
            elif msg_type == "peer":
                logger.info(
                    "zoom.pipeline.peer event=%s role=%s peers=%s",
                    msg.get("event"), msg.get("role"), msg.get("peers"),
                )
            elif msg_type == "error":
                # Non-fatal pipeline errors (server_busy, tts_*) are expected
                # backpressure; fatal ones (room_full, hello_timeout) end the
                # connection and the reconnect loop re-hellos with the room.
                logger.info(
                    "zoom.pipeline.error code=%s fatal=%s", msg.get("code"), msg.get("fatal")
                )
            # pong/ready (re-ack) — nothing to do

    async def aclose(self) -> None:
        self._connected.clear()
