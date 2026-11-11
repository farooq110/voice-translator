"""The Zoom bridge: webhook events → RTMS audio → the existing pipeline.

Design (ADR-0019, Phase 7): the bridge is an ORCHESTRATOR only. For every
`meeting.rtms_started` webhook it spawns one `ZoomMeetingWorker` that

1. opens a protocol-v1 WebSocket to the app's OWN `/ws/audio` endpoint
   (`PipelineClient`) as the room's host — the meeting audio flows through
   the same Silero-VAD → Whisper → decision-engine → translation → Piper
   path a browser's audio would, and the language pair comes from settings
   (`zoom_native_language`/`zoom_preferred_language`);
2. connects to Zoom's RTMS media stream (`RtmsClient`) and feeds every L16
   frame through an `UtteranceSegmenter` (pause endpointing + soft cap);
   completed segments are queued to the pipeline connection (drop-on-full —
   a stalled consumer must never block the RTMS keep-alive loop);
3. exposes the room's invite code at `GET /zoom/meetings` so the translator
   user joins the meeting's translation room in the browser exactly like a
   Phase 5 two-person session — the human hears the translated voice and
   sees the speaker-tagged transcript; the bridge itself ignores `audio`
   events.

Lifecycle rules that mirror Zoom's documented gotchas:

- ONE connection per stream — a repeated `rtms_started` for a meeting that
  already has a worker is logged and ignored (Zoom kicks the old connection
  when a second one arrives; dedupe keeps us on the right side of that);
- `meeting.rtms_stopped` (or app shutdown) stops the worker: the tail
  segment is flushed, the pipeline connection drains and closes, and the
  RTMS session is closed with the documented STREAM_CLOSE_REQ;
- workers are independent: one meeting failing never touches another.
"""

from __future__ import annotations

import asyncio
import logging
import time

from ..config import Settings
from .pipeline_client import PipelineClient
from .rtms import RtmsClient, RtmsError

logger = logging.getLogger(__name__)

TAIL_DRAIN_S = 2.0
ACLOSE_GRACE_S = 5.0

_MEETING_KEYS = ("meeting_uuid", "uuid")
_STREAM_KEYS = ("rtms_stream_id", "stream_id")
_URL_KEYS = ("server_urls", "server_url", "media_url")


class UtteranceSegmenter:
    """Meeting-audio endpointer — a calmer cousin of the browser's.

    A meeting stream is noisier than a phone mic (far-end loudspeakers,
    crosstalk), so the segmenter only DECIDES WHEN TO FLUSH and lets the
    server-side Silero VAD gate/trim/split do the real work (ADR-0009):

    - RMS above `rms_threshold` marks speech and clears the silence run;
    - `pause_ms` of quiet after speech flushes the buffer (pause split);
    - a buffer reaching `soft_cap_s` is flushed unconditionally (the server
      planner splits at internal pauses);
    - `tail()` returns whatever remains, for the stop-time flush.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16_000,
        pause_ms: int = 600,
        soft_cap_s: float = 6.0,
        rms_threshold: int = 300,
        min_segment_s: float = 0.4,
    ) -> None:
        self._sample_rate = sample_rate
        self._pause_s = max(0.0, pause_ms) / 1000.0
        self._soft_cap_s = soft_cap_s
        self._rms_threshold = rms_threshold
        self._min_segment_s = min_segment_s
        self._buffer = bytearray()
        self._silence_run_s = 0.0
        self._speech_seen = False

    def feed(self, pcm: bytes) -> list[bytes]:
        """Absorb one audio frame; return segments ready to flush (0..1)."""
        if not pcm:
            return []
        self._buffer.extend(pcm)
        duration = len(pcm) / 2 / self._sample_rate
        if _rms16(pcm) >= self._rms_threshold:
            self._speech_seen = True
            self._silence_run_s = 0.0
        else:
            self._silence_run_s += duration
        buffered_s = len(self._buffer) / 2 / self._sample_rate
        pause_hit = (
            self._speech_seen
            and self._silence_run_s >= self._pause_s
            and buffered_s >= self._min_segment_s
        )
        cap_hit = buffered_s >= self._soft_cap_s
        if pause_hit or cap_hit:
            return [self._take()]
        return []

    def tail(self) -> bytes:
        """Whatever is still buffered (flushed on stop; server VAD gates it)."""
        return self._take()

    def _take(self) -> bytes:
        data = bytes(self._buffer)
        self._buffer.clear()
        self._silence_run_s = 0.0
        self._speech_seen = False
        return data


def _rms16(pcm: bytes) -> int:
    """Integer RMS of a little-endian PCM16 buffer (0..32767)."""
    count = len(pcm) // 2
    if not count:
        return 0
    total = 0
    for i in range(0, count * 2, 2):
        # struct-free hot path for the common 20 ms frame (320 samples):
        # int.from_bytes on the local slice — allocation-light enough.
        sample = int.from_bytes(pcm[i:i + 2], "little", signed=True)
        total += sample * sample
    return int((total // count) ** 0.5)


class ZoomMeetingWorker:
    """One bridged meeting: pipeline connection + RTMS session + stats."""

    def __init__(
        self,
        *,
        settings: Settings,
        meeting_uuid: str,
        rtms_stream_id: str,
        server_urls: str,
    ) -> None:
        self._settings = settings
        self.meeting_uuid = meeting_uuid
        self.rtms_stream_id = rtms_stream_id
        self.server_urls = server_urls
        self.started_at = time.time()
        self.state = "starting"
        self.last_state_change: str | None = None
        self.bytes_ingested = 0
        self.frames_seen = 0
        self.room_code: str | None = None

        self._pipeline = PipelineClient(
            url=settings.zoom_ws_url,
            native_language=settings.zoom_native_language,
            preferred_language=settings.zoom_preferred_language,
            reconnect_max_s=settings.zoom_reconnect_max_s,
        )
        self._segmenter = UtteranceSegmenter(
            pause_ms=settings.zoom_pause_ms,
            soft_cap_s=settings.zoom_soft_cap_s,
            rms_threshold=settings.zoom_rms_threshold,
        )
        self._rtms: RtmsClient | None = None
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ api

    def start(self) -> None:
        self._task = asyncio.create_task(self.run(), name=f"zoom-worker-{self.meeting_uuid[:8]}")

    def request_stop(self) -> None:
        self._stop.set()

    @property
    def done(self) -> bool:
        task = self._task
        return task is None or task.done()

    async def wait_done(self, timeout: float | None = None) -> None:
        task = self._task
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------ run

    async def run(self) -> None:
        pipeline_task = asyncio.create_task(self._pipeline.run(), name="zoom-pipeline-client")
        if not await self._pipeline.wait_connected(self._settings.zoom_worker_start_timeout_s):
            self.state = "error"
            logger.error(
                "zoom.worker.pipeline_failed meeting_uuid=%s url=%s — the app's own "
                "WebSocket did not accept the bridge connection",
                _mask(self.meeting_uuid), self._settings.zoom_ws_url,
            )
            self._pipeline.stop()
            await _bounded_wait(pipeline_task, ACLOSE_GRACE_S)
            return
        self.room_code = self._pipeline.room_code
        self.state = "live"
        logger.info(
            "zoom.worker.live meeting_uuid=%s room=%s pair=%s→%s",
            _mask(self.meeting_uuid), self.room_code,
            self._settings.zoom_native_language, self._settings.zoom_preferred_language,
        )

        self._rtms = RtmsClient(
            client_id=self._settings.zoom_client_id,
            client_secret=self._settings.zoom_client_secret,
            meeting_uuid=self.meeting_uuid,
            rtms_stream_id=self.rtms_stream_id,
            server_urls=self.server_urls,
            on_audio=self._on_meeting_audio,
            on_state=self._on_rtms_state,
            connect_timeout_s=self._settings.zoom_connect_timeout_s,
        )
        rtms_task = asyncio.create_task(self._rtms.run(), name="zoom-rtms-client")
        stop_wait = asyncio.create_task(self._stop.wait())
        done, _pending = await asyncio.wait(
            {rtms_task, stop_wait}, return_when=asyncio.FIRST_COMPLETED
        )
        rtms_failed = False
        if rtms_task in done:
            exc = rtms_task.exception()
            if exc is not None:
                rtms_failed = True
                logger.warning(
                    "zoom.worker.rtms_ended meeting_uuid=%s (%s)", _mask(self.meeting_uuid), exc
                )

        # --- ordered shutdown: tail flush → drain → stop pipeline → close rtms
        tail = self._segmenter.tail()
        if tail:
            self._pipeline.enqueue_segment(tail)
        await asyncio.sleep(TAIL_DRAIN_S)
        self._pipeline.stop()
        await _bounded_wait(pipeline_task, ACLOSE_GRACE_S)
        if self._rtms is not None and not rtms_task.done():
            await self._rtms.aclose()
            await _bounded_wait(rtms_task, ACLOSE_GRACE_S)
        stop_wait.cancel()

        self.state = "error" if rtms_failed else "stopped"
        logger.info(
            "zoom.worker.done meeting_uuid=%s state=%s bytes=%d frames=%d tx=%d trans=%d audio=%d dropped=%d",
            _mask(self.meeting_uuid), self.state, self.bytes_ingested, self.frames_seen,
            self._pipeline.transcriptions, self._pipeline.translations,
            self._pipeline.audio_events, self._pipeline.segments_dropped,
        )

    # ------------------------------------------------------------- audio in

    async def _on_meeting_audio(self, pcm: bytes) -> None:
        """RTMS frame → endpointer → pipeline queue (never blocks the media loop)."""
        self.frames_seen += 1
        self.bytes_ingested += len(pcm)
        for segment in self._segmenter.feed(pcm):
            self._pipeline.enqueue_segment(segment)

    async def _on_rtms_state(self, label: str) -> None:
        self.last_state_change = label
        if label == "paused":
            self.state = "paused"
        elif label == "resumed":
            self.state = "live"

    # ------------------------------------------------------------ reporting

    def report(self, *, include_room: bool = False) -> dict[str, object]:
        info: dict[str, object] = {
            "meeting_uuid": _mask(self.meeting_uuid),
            "rtms_stream_id": self.rtms_stream_id,
            "state": self.state,
            "started_at": self.started_at,
            "uptime_s": round(time.time() - self.started_at, 1),
            "bytes_ingested": self.bytes_ingested,
            "frames": self.frames_seen,
            "transcriptions": self._pipeline.transcriptions,
            "translations": self._pipeline.translations,
            "tts_audio_events": self._pipeline.audio_events,
            "segments_dropped": self._pipeline.segments_dropped,
            "pipeline_connected": self._pipeline.connected,
            "last_state": self.last_state_change,
        }
        if include_room:
            info["room_code"] = self.room_code
            info["invite_path"] = f"/?room={self.room_code}" if self.room_code else None
        return info


class ZoomBridge:
    """Webhook-driven registry of active meeting workers (one app instance)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._workers: dict[str, ZoomMeetingWorker] = {}
        self._total_started = 0
        self._events_seen = 0

    # ------------------------------------------------------------------ api

    def handle_event(self, event: str, payload: object) -> None:
        """Route one validated webhook event (runs as a background task)."""
        self._events_seen += 1
        if event == "meeting.rtms_started":
            stream = extract_stream(payload)
            if stream is None:
                logger.warning("zoom.webhook.rtms_started_missing_fields keys=%s", _keys(payload))
                return
            meeting_uuid, rtms_stream_id, server_urls = stream
            if meeting_uuid in self._workers and not self._workers[meeting_uuid].done:
                # Zoom's single-connection rule: a duplicate start is ignored;
                # a RETRY after our worker finished (state stopped/error) may
                # legitimately arrive and starts a fresh worker.
                logger.info("zoom.webhook.duplicate_ignored meeting_uuid=%s", _mask(meeting_uuid))
                return
            worker = ZoomMeetingWorker(
                settings=self._settings,
                meeting_uuid=meeting_uuid,
                rtms_stream_id=rtms_stream_id,
                server_urls=server_urls,
            )
            self._workers[meeting_uuid] = worker
            self._total_started += 1
            worker.start()
            logger.info("zoom.webhook.worker_started meeting_uuid=%s stream=%s", _mask(meeting_uuid), rtms_stream_id)
        elif event == "meeting.rtms_stopped":
            # Stop payloads can arrive slimmer than start payloads (observed
            # in the wild: some deliveries carry only the meeting_uuid) —
            # match on the uuid alone.
            meeting_uuid = _meeting_uuid_of(payload)
            if meeting_uuid is None:
                logger.warning("zoom.webhook.rtms_stopped_missing_uuid keys=%s", _keys(payload))
                return
            worker = self._workers.get(meeting_uuid)
            if worker is None or worker.done:
                logger.debug("zoom.webhook.stop_unknown_meeting meeting_uuid=%s", _mask(meeting_uuid))
                return
            worker.request_stop()
            logger.info("zoom.webhook.stop_requested meeting_uuid=%s", _mask(meeting_uuid))
        else:
            logger.debug("zoom.webhook.ignored event=%s", event)

    async def aclose(self) -> None:
        """App shutdown: stop every worker and wait briefly for clean exits."""
        for worker in self._workers.values():
            if not worker.done:
                worker.request_stop()
        for worker in self._workers.values():
            if not worker.done:
                await worker.wait_done(ACLOSE_GRACE_S)

    # ------------------------------------------------------------ reporting

    def describe(self) -> dict[str, object]:
        active = [w for w in self._workers.values() if not w.done]
        return {
            "enabled": True,
            "active_meetings": len(active),
            "total_meetings": self._total_started,
            "pair": f"{self._settings.zoom_native_language}→{self._settings.zoom_preferred_language}",
            "bytes_ingested": sum(w.bytes_ingested for w in self._workers.values()),
        }

    def meetings(self) -> list[dict[str, object]]:
        """REST payload for /zoom/meetings — invites for the translator user."""
        return [
            w.report(include_room=True)
            for w in sorted(self._workers.values(), key=lambda w: w.started_at)
        ]

    @property
    def worker_count(self) -> int:
        return len(self._workers)


# ------------------------------------------------------------------ helpers

def extract_stream(payload: object) -> tuple[str, str, str] | None:
    """Pull (meeting_uuid, rtms_stream_id, server_urls) out of a webhook payload.

    Zoom webhook deliveries nest the event object under
    ``payload.object.{...}``; some tooling and tests flatten it to
    ``payload.{...}``. Both shapes are accepted; the first complete triple
    wins.
    """
    for candidate in _payload_shapes(payload):
        meeting_uuid = _first_str(candidate, _MEETING_KEYS)
        rtms_stream_id = _first_str(candidate, _STREAM_KEYS)
        server_urls = _first_str(candidate, _URL_KEYS)
        if meeting_uuid and rtms_stream_id and server_urls:
            return meeting_uuid, rtms_stream_id, server_urls
    return None


def _payload_shapes(payload: object) -> list[object]:
    shapes: list[object] = []
    if isinstance(payload, dict):
        shapes.append(payload)
        inner = payload.get("object")
        if isinstance(inner, dict):
            shapes.append(inner)
    return shapes


def _meeting_uuid_of(payload: object) -> str | None:
    """The meeting uuid from a stop payload — full stream first, uuid-only fallback."""
    stream = extract_stream(payload)
    if stream is not None:
        return stream[0]
    for shape in _payload_shapes(payload):
        meeting_uuid = _first_str(shape, _MEETING_KEYS)
        if meeting_uuid:
            return meeting_uuid
    return None


def _first_str(source: object, keys: tuple[str, ...]) -> str | None:
    if not isinstance(source, dict):
        return None
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _keys(payload: object) -> list[str]:
    return sorted(payload.keys()) if isinstance(payload, dict) else []


def _mask(meeting_uuid: str) -> str:
    return meeting_uuid[:8] + "…" if len(meeting_uuid) > 8 else meeting_uuid


async def _bounded_wait(task: asyncio.Task[None], timeout: float) -> None:
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        if not task.done():
            task.cancel()
