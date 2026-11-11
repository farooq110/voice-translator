"""RTMS client — Zoom's Real-Time Media Streams WebSocket protocol (Phase 7).

Implements the documented two-phase handshake against Zoom's RTMS servers:

    webhook meeting.rtms_started ──▶ (payload: meeting_uuid, rtms_stream_id,
                                      server_urls)
    ┌─ signaling WebSocket (server_urls) ─────────────────────────────┐
    │ client → {msg_type: 1, protocol_version, meeting_uuid,          │
    │           rtms_stream_id, signature, media_type: 1 (AUDIO)}     │
    │ server → {msg_type: 2, …}          (carries the media WS URL)   │
    └──────────────────────────────────────────────────────────────────┘
    ┌─ media WebSocket (from the msg_type 2 response) ─────────────────┐
    │ client → {msg_type: 3, …, media_type: 1, media_params:{audio:…}}│
    │ server → {msg_type: 4, status_code: 0}                          │
    │ client → {msg_type: 7, rtms_stream_id}   (CLIENT_READY_ACK)     │
    │ server → {msg_type: 12} keep-alive  → client answers {13, ts}   │
    │ server → {msg_type: 14} audio frames → on_audio(pcm16_bytes)    │
    │ server → {msg_type: 8/9} stream/session state updates           │
    └──────────────────────────────────────────────────────────────────┘

Audio is negotiated as **raw L16 PCM, 16 kHz, mono, 20 ms send rate** —
byte-for-byte the pipeline's native format, so frames need no decode or
resample (the RTMS spec also offers G.711/G.722/Opus; L16 keeps the bridge
dependency-free — ADR-0019).

Integers are used for every enum (the RTMS reference mandates integers, not
names): msg_type 1/2/3/4/7/8/9/12/13/14/21, MEDIA_CONTENT_TYPE.RAW_AUDIO=2,
AUDIO_SAMPLE_RATE.SR_16K=1, AUDIO_CHANNEL.MONO=1, MEDIA_PAYLOAD_TYPE.L16=1,
MEDIA_DATA_OPTION.AUDIO_MIXED_STREAM=1, RTMS_STATUS_CODE.STATUS_OK=0.

Honest boundary: the handshake shape, message numbers, audio parameters and
keep-alive rules follow Zoom's published reference (developers.zoom.us/docs/
rtms — data type definitions + meetings quickstart, and Zoom's own
zoom-plugin-codex RTMS guide), retrieved 2026-11-10. The sandbox has no Zoom
account, so the ONLY parts verifiable here run against the in-repo fake RTMS
server (`scripts/fake_zoom_rtms.py`); field names Zoom does not publish in
full (notably the media URL key inside the msg_type 2 response and the exact
audio frame encoding) are handled defensively and listed in
project-state/KNOWN_ISSUES.md (KI-029) as the real-Zoom verification points.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from collections.abc import Awaitable, Callable

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from .signing import rtms_join_signature

logger = logging.getLogger(__name__)

# --- RTMS protocol constants (integers, per the Zoom reference) -----------
MSG_SIGNALING_HAND_SHAKE_REQ = 1
MSG_SIGNALING_HAND_SHAKE_RESP = 2
MSG_DATA_HAND_SHAKE_REQ = 3
MSG_DATA_HAND_SHAKE_RESP = 4
MSG_CLIENT_READY_ACK = 7
MSG_STREAM_STATE_UPDATE = 8
MSG_SESSION_STATE_UPDATE = 9
MSG_KEEP_ALIVE_REQ = 12
MSG_KEEP_ALIVE_RESP = 13
MSG_MEDIA_DATA_AUDIO = 14
MSG_STREAM_CLOSE_REQ = 21

MEDIA_TYPE_AUDIO = 1  # MEDIA_DATA_TYPE.AUDIO (bitmask member)

CONTENT_RAW_AUDIO = 2  # MEDIA_CONTENT_TYPE
SAMPLE_RATE_16K = 1  # AUDIO_SAMPLE_RATE.SR_16K
CHANNEL_MONO = 1  # AUDIO_CHANNEL
PAYLOAD_L16 = 1  # MEDIA_PAYLOAD_TYPE (uncompressed PCM16)
DATA_OPT_MIXED_STREAM = 1  # MEDIA_DATA_OPTION.AUDIO_MIXED_STREAM
STATUS_OK = 0  # RTMS_STATUS_CODE

# Session/stream states we react to (RTMS_SESSION_STATE / RTMS_STREAM_STATE).
SESSION_STATE_STOPPED = 5
STREAM_STATE_ACTIVE = 1
STREAM_STATE_PAUSED = 5
STREAM_STATE_RESUMED = 6

_MEDIA_URL_KEYS = ("media_url", "data_url", "url", "server_url", "media_server")


class RtmsError(RuntimeError):
    """The RTMS handshake or stream failed in a way worth a clean restart."""


class RtmsClient:
    """One Zoom RTMS session: signaling + media sockets for one stream.

    The client is deliberately dumb about the PRODUCT: it hands every decoded
    L16 frame to `on_audio` and every session-state change to `on_state`, and
    otherwise only maintains the protocol (handshake, keep-alives, graceful
    close). The Zoom bridge owns the pipeline.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        meeting_uuid: str,
        rtms_stream_id: str,
        server_urls: str,
        on_audio: Callable[[bytes], Awaitable[None]],
        on_state: Callable[[str], Awaitable[None]] | None = None,
        connect_timeout_s: float = 15.0,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._meeting_uuid = meeting_uuid
        self._stream_id = rtms_stream_id
        self._server_urls = server_urls
        self._on_audio = on_audio
        self._on_state = on_state
        self._connect_timeout = connect_timeout_s
        self._sig = rtms_join_signature(client_id, meeting_uuid, rtms_stream_id, client_secret)
        self._closed = asyncio.Event()
        self._audio_seen = False
        # Live sockets are kept for the graceful-close path.
        self._signaling_ws = None
        self._media_ws = None

    # ------------------------------------------------------------------ api

    @property
    def audio_seen(self) -> bool:
        """True once at least one audio frame arrived (handshake evidence)."""
        return self._audio_seen

    async def run(self) -> None:
        """Run the full session until the remote side stops it or close().

        Raises RtmsError with a one-line cause on any handshake/protocol
        failure — the bridge logs it and may retry.
        """
        try:
            media_url = await self._signaling_phase()
            await self._media_phase(media_url)
        except RtmsError:
            await self.aclose()
            raise
        except (ConnectionClosed, OSError, asyncio.TimeoutError) as exc:
            await self.aclose()
            raise RtmsError(f"rtms connection failed: {exc}") from exc
        await self.aclose()

    async def aclose(self) -> None:
        """Graceful shutdown: STREAM_CLOSE_REQ on signaling, then close both."""
        self._closed.set()
        for ws, payload in (
            (self._media_ws, None),
            (self._signaling_ws, self._stream_close_req()),
        ):
            if ws is None:
                continue
            try:
                if payload is not None:
                    await ws.send(json.dumps(payload))
                await ws.close()
            except Exception:  # noqa: BLE001 — close is best-effort by design
                pass
        self._media_ws = None
        self._signaling_ws = None

    # ------------------------------------------------------------ handshake

    def _signaling_handshake(self) -> dict[str, object]:
        return {
            "msg_type": MSG_SIGNALING_HAND_SHAKE_REQ,
            "protocol_version": 1,
            "meeting_uuid": self._meeting_uuid,
            "rtms_stream_id": self._stream_id,
            "signature": self._sig,
            "media_type": MEDIA_TYPE_AUDIO,
        }

    def _media_handshake(self) -> dict[str, object]:
        return {
            "msg_type": MSG_DATA_HAND_SHAKE_REQ,
            "protocol_version": 1,
            "sequence": 0,
            "meeting_uuid": self._meeting_uuid,
            "rtms_stream_id": self._stream_id,
            "signature": self._sig,
            "media_type": MEDIA_TYPE_AUDIO,
            "media_params": {
                "audio": {
                    "content_type": CONTENT_RAW_AUDIO,
                    "sample_rate": SAMPLE_RATE_16K,
                    "channel": CHANNEL_MONO,
                    "codec": PAYLOAD_L16,
                    "data_opt": DATA_OPT_MIXED_STREAM,
                    "send_rate": 20,
                },
            },
        }

    def _client_ready_ack(self) -> dict[str, object]:
        return {"msg_type": MSG_CLIENT_READY_ACK, "rtms_stream_id": self._stream_id}

    def _stream_close_req(self) -> dict[str, object]:
        return {"msg_type": MSG_STREAM_CLOSE_REQ, "rtms_stream_id": self._stream_id}

    async def _signaling_phase(self) -> str:
        """Open the signaling socket, handshake, return the media WS URL."""
        async with asyncio.timeout(self._connect_timeout):
            self._signaling_ws = await ws_connect(self._server_urls, open_timeout=self._connect_timeout)
            await self._signaling_ws.send(json.dumps(self._signaling_handshake()))
            raw = await self._signaling_ws.recv()
        resp = _parse_json_object(raw, "signaling handshake response")
        if resp.get("msg_type") != MSG_SIGNALING_HAND_SHAKE_RESP:
            raise RtmsError(f"unexpected signaling response msg_type={resp.get('msg_type')!r}")
        if _status_code(resp) != STATUS_OK:
            raise RtmsError(f"signaling handshake rejected status={_status_code(resp)!r}")
        media_url = _find_media_url(resp)
        if not media_url:
            raise RtmsError(
                "signaling handshake response carries no media URL "
                f"(keys={sorted(resp.keys())}) — see KI-029"
            )
        logger.info(
            "zoom.rtms.signaling_ok meeting_uuid=%s stream=%s",
            _mask_uuid(self._meeting_uuid), self._stream_id,
        )
        return media_url

    async def _media_phase(self, media_url: str) -> None:
        """Open the media socket, subscribe to audio, pump frames."""
        async with asyncio.timeout(self._connect_timeout):
            self._media_ws = await ws_connect(media_url, open_timeout=self._connect_timeout)
            await self._media_ws.send(json.dumps(self._media_handshake()))
            raw = await self._media_ws.recv()
        resp = _parse_json_object(raw, "media handshake response")
        if resp.get("msg_type") != MSG_DATA_HAND_SHAKE_RESP:
            raise RtmsError(f"unexpected media response msg_type={resp.get('msg_type')!r}")
        if _status_code(resp) != STATUS_OK:
            raise RtmsError(f"media handshake rejected status={_status_code(resp)!r}")
        await self._media_ws.send(json.dumps(self._client_ready_ack()))
        logger.info(
            "zoom.rtms.media_ok meeting_uuid=%s stream=%s codec=l16 rate=16000 ch=1",
            _mask_uuid(self._meeting_uuid), self._stream_id,
        )
        await self._media_loop()

    # ------------------------------------------------------------- framing

    async def _media_loop(self) -> None:
        assert self._media_ws is not None
        ws = self._media_ws
        async for raw in ws:
            if isinstance(raw, (bytes, bytearray, memoryview)):
                # Some deployments stream audio as raw BINARY frames instead
                # of base64-in-JSON — accept both (KI-029).
                pcm = _decode_audio_content(bytes(raw))
                if pcm:
                    self._audio_seen = True
                    await self._on_audio(pcm)
                continue
            msg = _parse_json_object(raw, "media message")
            msg_type = msg.get("msg_type")
            if msg_type == MSG_KEEP_ALIVE_REQ:
                await ws.send(json.dumps({"msg_type": MSG_KEEP_ALIVE_RESP, "timestamp": msg.get("timestamp")}))
            elif msg_type == MSG_MEDIA_DATA_AUDIO:
                pcm = _decode_audio_content(msg.get("content"))
                if pcm:
                    self._audio_seen = True
                    await self._on_audio(pcm)
            elif msg_type in (MSG_STREAM_STATE_UPDATE, MSG_SESSION_STATE_UPDATE):
                await self._handle_state(msg)
            elif msg_type == MSG_SIGNALING_HAND_SHAKE_RESP:
                pass  # keep-alive-adjacent echo on some deployments — ignore
            else:
                logger.debug("zoom.rtms.media_msg msg_type=%s (ignored)", msg_type)

    async def _handle_state(self, msg: dict[str, object]) -> None:
        content = msg.get("content") if isinstance(msg.get("content"), dict) else msg
        session_state = content.get("session_state")
        stream_state = content.get("stream_state")
        stop_reason = content.get("stop_reason") or content.get("reason")
        logger.info(
            "zoom.rtms.state meeting_uuid=%s session_state=%s stream_state=%s stop_reason=%s",
            _mask_uuid(self._meeting_uuid), session_state, stream_state, stop_reason,
        )
        if session_state == SESSION_STATE_STOPPED:
            raise RtmsError("session stopped by Zoom (stop_reason=%s)" % stop_reason)
        if self._on_state is not None:
            label = "paused" if stream_state == STREAM_STATE_PAUSED else (
                "resumed" if stream_state == STREAM_STATE_RESUMED else (
                    "active" if stream_state == STREAM_STATE_ACTIVE else None
                )
            )
            if label:
                await self._on_state(label)


# ------------------------------------------------------------------ helpers

def _parse_json_object(raw: object, what: str) -> dict[str, object]:
    """RTMS messages are JSON objects on TEXT frames; anything else is fatal."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RtmsError(f"{what} is not JSON: {exc}") from exc
    else:
        parsed = raw
    if not isinstance(parsed, dict):
        raise RtmsError(f"{what} is not a JSON object")
    return parsed


def _status_code(msg: dict[str, object]) -> object:
    content = msg.get("content")
    if isinstance(content, dict) and "status_code" in content:
        return content.get("status_code")
    return msg.get("status_code")


def _find_media_url(msg: dict[str, object]) -> str | None:
    """Locate the media WebSocket URL in a signaling handshake response.

    Zoom's reference does not pin the key in the retrieved pages; probe the
    documented-plausible names at the top level and inside `content`
    (defensive — see KI-029).
    """
    candidates: list[object] = [msg]
    content = msg.get("content")
    if isinstance(content, dict):
        candidates.insert(0, content)
    for source in candidates:
        assert isinstance(source, dict)
        for key in _MEDIA_URL_KEYS:
            value = source.get(key)
            if isinstance(value, str) and value.startswith(("ws://", "wss://")):
                return value
    return None


def _decode_audio_content(content: object) -> bytes | None:
    """Decode one audio DATA frame's content into raw PCM16 bytes.

    The reference sends frames as JSON text with the samples base64-encoded
    in `content`; be tolerant of binary frames and of an object wrapper
    (`{"data": "<b64>"}`) — both appear in Zoom's samples. Anything that
    decodes to an ODD byte count is dropped (it would desync PCM16 framing).
    """
    if content is None:
        return None
    if isinstance(content, dict):
        content = content.get("data")
    if content is None:
        return None
    if isinstance(content, str):
        try:
            data = base64.b64decode(content, validate=False)
        except (binascii.Error, ValueError):
            logger.debug("zoom.rtms.audio_frame_not_base64 — dropped")
            return None
    elif isinstance(content, (bytes, bytearray, memoryview)):
        data = bytes(content)
    else:
        logger.debug("zoom.rtms.audio_frame_unsupported_type=%s — dropped", type(content).__name__)
        return None
    if len(data) % 2:
        logger.debug("zoom.rtms.audio_frame_odd_bytes=%d — dropped", len(data))
        return None
    return data


def _mask_uuid(meeting_uuid: str) -> str:
    """Privacy: meetings are identified in logs by their first 8 chars only."""
    return meeting_uuid[:8] + "…" if len(meeting_uuid) > 8 else meeting_uuid
