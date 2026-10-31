"""WebSocket endpoint for real-time audio — protocol v1.

Handshake and flow (full spec: docs/audio-protocol.md):

    client ── hello(config) ──▶ server
    client ◀── ready(session) ─ server
    client ── binary PCM16 ───▶ server   (buffered, bounded)
    client ── flush ──────────▶ server
    client ◀── transcription ─ server    (text + detected language)

Invariants enforced here:
- binary audio before `hello` is a fatal protocol violation;
- format must be pcm_s16le / 16 kHz / mono;
- buffers are bounded — at `max_utterance_seconds` an auto-flush is issued;
- STT runs on a bounded worker executor, never on the event loop;
- a server-side Silero VAD pipeline (Phase 2, ADR-0009) gates, trims and
  segments every utterance BEFORE decoding: silence/noise is answered
  `no_speech_detected` without touching the model (STT is never invoked);
- admission control bounds decode work globally and per session — overflow
  is answered with a non-fatal `server_busy` error (backpressure), never
  queued without limit;
- STT is pinned to the hello config's `native_language` (KI-006: the user's
  selection is authoritative; auto-detect mislabels short utterances);
- hallucination-only results (empty after the no-speech filter) are reported
  as `no_speech_detected`, never as an empty transcription;
- results for closed/stale sessions are dropped, never delivered.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from ..audio.validation import (
    ChunkAlignmentError,
    ChunkTooLargeError,
    validate_pcm16_chunk,
)
from ..audio.vad import UtterancePlan, UtterancePlanner
from ..config import Settings
from ..models.protocol import (
    PROTOCOL_VERSION,
    ErrorEvent,
    FlushMessage,
    HelloMessage,
    PingMessage,
    PongEvent,
    ReadyEvent,
    SttInfo,
    TranscriptionEvent,
    parse_client_message,
)
from ..sessions.manager import Session, SessionManager
from ..stt.base import STTProvider, TranscriptionResult
from .backpressure import SttQueue

logger = logging.getLogger(__name__)
router = APIRouter()

CLOSE_NORMAL = 1000
CLOSE_GOING_AWAY = 1001
CLOSE_POLICY_VIOLATION = 1008
CLOSE_TRY_AGAIN_LATER = 1013


class _Connection:
    """Per-connection protocol state (kept separate from the endpoint loop)."""

    def __init__(
        self,
        websocket: WebSocket,
        *,
        settings: Settings,
        session: Session,
        stt: STTProvider,
        executor: ThreadPoolExecutor,
        vad: UtterancePlanner,
        stt_queue: SttQueue,
    ) -> None:
        self.websocket = websocket
        self.settings = settings
        self.session = session
        self.stt = stt
        self.executor = executor
        self.vad = vad
        self.stt_queue = stt_queue
        self.hello_done = False
        self.closed = False
        self._send_lock = asyncio.Lock()
        self._pending: set[asyncio.Task[None]] = set()
        self._max_buffer_bytes = (
            int(settings.max_utterance_seconds * settings.audio_sample_rate) * 2
        )

    # ------------------------------------------------------------------ out

    async def _send(self, event: ReadyEvent | TranscriptionEvent | ErrorEvent | PongEvent) -> None:
        if self.closed:
            return
        async with self._send_lock:
            await self.websocket.send_text(event.model_dump_json())

    async def send_error(self, code: str, message: str, *, fatal: bool = False) -> None:
        logger.debug(
            "ws.error session_id=%s code=%s fatal=%s message=%s",
            self.session.id, code, fatal, message,
        )
        await self._send(ErrorEvent(code=code, message=message, fatal=fatal))

    # ------------------------------------------------------------------ in

    async def handle_text(self, raw: str) -> bool:
        """Handle one JSON text frame. Returns False → close the connection."""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            await self.send_error("malformed_json", "Message is not valid JSON.")
            return True

        if isinstance(payload, dict) and payload.get("v") != PROTOCOL_VERSION:
            await self.send_error(
                "unsupported_protocol_version",
                f"This server speaks protocol v{PROTOCOL_VERSION}.",
                fatal=True,
            )
            return False

        try:
            message = parse_client_message(payload)
        except ValidationError as exc:
            errors = exc.errors()
            detail = str(errors[0].get("msg", "invalid message")) if errors else "invalid message"
            await self.send_error("invalid_message", f"Invalid message: {detail}")
            return True

        if isinstance(message, HelloMessage):
            return await self._handle_hello(message)
        if isinstance(message, PingMessage):
            ts = message.ts if message.ts is not None else int(time.time() * 1000)
            await self._send(PongEvent(ts=ts))
            return True
        if isinstance(message, FlushMessage):
            await self.flush_buffer()
            return True
        return True

    async def _handle_hello(self, message: HelloMessage) -> bool:
        if self.hello_done:
            await self.send_error(
                "hello_already_sent", "Configuration already received for this session."
            )
            return True

        config = message.config
        if config.audio.encoding != "pcm_s16le":
            await self.send_error("unsupported_audio_format", "Only pcm_s16le is supported.", fatal=True)
            return False
        if config.audio.sample_rate != self.settings.audio_sample_rate:
            await self.send_error(
                "unsupported_sample_rate",
                f"Sample rate must be {self.settings.audio_sample_rate} Hz.",
                fatal=True,
            )
            return False
        if config.audio.channels != 1:
            await self.send_error("unsupported_channels", "Only mono audio is supported.", fatal=True)
            return False
        if config.native_language == config.preferred_language:
            # Not fatal: same-language conversations are valid; the translation
            # engine (Phase 3) will treat this configuration as pass-through.
            await self.send_error(
                "identical_languages",
                "Native and preferred languages are identical; pass-through behavior will apply.",
            )

        self.session.config = config
        self.hello_done = True
        logger.info(
            "ws.hello session_id=%s native=%s preferred=%s",
            self.session.id, config.native_language, config.preferred_language,
        )
        await self._send(
            ReadyEvent(
                session_id=self.session.id,
                audio=config.audio,
                stt=SttInfo.model_validate(self.stt.describe()),
            )
        )
        return True

    async def handle_binary(self, data: bytes) -> bool:
        """Handle one binary audio frame. Returns False → close the connection."""
        if not self.hello_done:
            await self.send_error(
                "hello_required",
                "Send the hello message before streaming audio.",
                fatal=True,
            )
            return False
        try:
            validate_pcm16_chunk(data, max_chunk_bytes=self.settings.max_chunk_bytes)
        except ChunkTooLargeError as exc:
            await self.send_error("chunk_too_large", str(exc))
            return True
        except ChunkAlignmentError as exc:
            await self.send_error("invalid_chunk_alignment", str(exc))
            return True
        if not data:
            return True

        total = self.session.append_audio(data)
        if total >= self._max_buffer_bytes:
            logger.info(
                "ws.auto_flush session_id=%s buffered_s=%.1f (cap reached)",
                self.session.id,
                total / 2 / self.settings.audio_sample_rate,
            )
            await self.flush_buffer()
        return True

    # ------------------------------------------------------------------ stt

    async def flush_buffer(self) -> None:
        audio = self.session.take_buffer()
        min_bytes = int(self.settings.min_utterance_seconds * self.settings.audio_sample_rate) * 2
        if len(audio) < min_bytes:
            await self.send_error(
                "nothing_to_transcribe",
                "No speech captured since the last transcription.",
            )
            return

        # Backpressure (Phase 2): bound in-flight decodes per session, then
        # globally. Overflow is a non-fatal `server_busy` — the client keeps
        # the connection and simply re-flushes later; nothing is queued
        # without limit.
        if len(self._pending) >= self.settings.stt_max_pending_per_session:
            logger.info(
                "ws.busy session_id=%s reason=pending_session pending=%d",
                self.session.id, len(self._pending),
            )
            await self.send_error(
                "server_busy",
                "Still transcribing previous speech — one moment, then try again.",
            )
            return
        if not self.stt_queue.try_acquire():
            logger.info(
                "ws.busy session_id=%s reason=queue_full queued=%d",
                self.session.id, self.stt_queue.queued,
            )
            await self.send_error(
                "server_busy",
                "Server is busy transcribing other audio — try again in a moment.",
            )
            return

        self.session.utterances += 1
        utterance_id = f"u_{self.session.utterances:04d}_{uuid.uuid4().hex[:6]}"
        task = asyncio.create_task(self._transcribe_and_deliver(audio, utterance_id))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def _language_hint(self) -> str | None:
        """Primary subtag of the configured native language ("ur-PK" → "ur").

        The translator user's selection is authoritative for their own mic
        audio (Phase 1: only their microphone is streamed). Returns None when
        no valid hello config is available — STT then falls back to auto
        detection instead of guessing wrong.
        """
        config = self.session.config
        if config is None or not config.native_language:
            return None
        primary = config.native_language.split("-", 1)[0].strip().lower()
        return primary or None

    async def _transcribe_and_deliver(self, audio: bytes, utterance_id: str) -> None:
        loop = asyncio.get_running_loop()
        started = time.perf_counter()
        hint = self._language_hint()
        try:
            # Phase 2 VAD pipeline (gate → trim → split), CPU work off-loop.
            plan = await asyncio.wait_for(
                loop.run_in_executor(
                    self.executor,
                    lambda: self.vad.plan(audio, sample_rate=self.settings.audio_sample_rate),
                ),
                timeout=self.settings.stt_timeout_seconds,
            )
            if not plan.chunks:
                processing_ms = int((time.perf_counter() - started) * 1000)
                logger.info(
                    "ws.vad_gate_rejected session_id=%s id=%s duration_s=%.2f speech_s=%.2f processing_ms=%d",
                    self.session.id, utterance_id, plan.source_duration_s, plan.speech_s, processing_ms,
                )
                await self.send_error(
                    "no_speech_detected",
                    "No speech was captured in that utterance — try again, a little "
                    "closer to the microphone.",
                )
                return

            result: TranscriptionResult = await asyncio.wait_for(
                self._decode_plan(plan, hint),
                timeout=self.settings.stt_timeout_seconds * len(plan.chunks),
            )
        except asyncio.TimeoutError:
            await self.send_error(
                "stt_timeout", "Speech recognition took too long; try a shorter utterance."
            )
            return
        except Exception:  # noqa: BLE001 — provider failure must not kill the socket
            logger.exception("stt failed session_id=%s", self.session.id)
            await self.send_error("stt_error", "Speech recognition failed.")
            return
        finally:
            # Exactly one release per acquired slot (acquired in flush_buffer).
            self.stt_queue.release()

        if self.closed or self.session.closed:
            logger.debug("ws.stale_result session_id=%s utterance=%s dropped", self.session.id, utterance_id)
            return

        processing_ms = int((time.perf_counter() - started) * 1000)

        # Noise/silence decoded into nothing (hallucination filter emptied the
        # text): report it as a recognizable, non-fatal condition instead of an
        # empty transcription bubble.
        if not result.text.strip():
            logger.info(
                "ws.no_speech session_id=%s id=%s hint=%s duration_s=%.2f processing_ms=%d",
                self.session.id, utterance_id, hint, result.audio_duration_s, processing_ms,
            )
            await self.send_error(
                "no_speech_detected",
                "Speech could not be recognized in that utterance — try again, a little "
                "closer to the microphone.",
            )
            return

        # Report the duration the user actually spoke (pre-trim), not the
        # trimmed decode input.
        result = TranscriptionResult(
            text=result.text,
            language=result.language,
            language_probability=result.language_probability,
            audio_duration_s=plan.source_duration_s,
        )
        if plan.split:
            logger.info(
                "ws.segmented session_id=%s id=%s chunks=%d dropped_s=%.2f",
                self.session.id, utterance_id, len(plan.chunks), plan.dropped_s,
            )

        event = TranscriptionEvent(
            id=utterance_id,
            text=result.text,
            language=result.language,
            language_probability=round(result.language_probability, 4),
            audio_duration_s=round(result.audio_duration_s, 2),
            processing_ms=processing_ms,
            low_confidence=self.stt.is_low_confidence(result.language_probability),
        )
        logger.info(
            "ws.transcription session_id=%s id=%s hint=%s language=%s prob=%.2f duration_s=%.2f processing_ms=%d chars=%d",
            self.session.id, utterance_id, hint, event.language, event.language_probability,
            event.audio_duration_s, processing_ms, len(event.text),
        )
        logger.debug("ws.transcript session_id=%s text=%r", self.session.id, event.text)
        await self._send(event)

    async def _decode_plan(
        self, plan: UtterancePlan, hint: str | None
    ) -> TranscriptionResult:
        """Decode one planned utterance — one call for a single chunk, one
        per segment for a split utterance (texts joined in order)."""
        loop = asyncio.get_running_loop()

        def decode(chunk: bytes) -> TranscriptionResult:
            return self.stt.transcribe_pcm(
                chunk,
                sample_rate=self.settings.audio_sample_rate,
                language_hint=hint,
            )

        if len(plan.chunks) == 1:
            return await loop.run_in_executor(self.executor, decode, plan.chunks[0])

        texts: list[str] = []
        language = "unknown"
        probability = 0.0
        for chunk in plan.chunks:
            part = await loop.run_in_executor(self.executor, decode, chunk)
            if part.text.strip():
                texts.append(part.text.strip())
                if language == "unknown":
                    language, probability = part.language, part.language_probability
        return TranscriptionResult(
            text=" ".join(texts),
            language=language,
            language_probability=probability,
            audio_duration_s=plan.source_duration_s,
        )


@router.websocket("/ws/audio")
async def audio_socket(websocket: WebSocket) -> None:
    settings: Settings = websocket.app.state.settings
    stt: STTProvider = websocket.app.state.stt
    sessions: SessionManager = websocket.app.state.sessions
    executor: ThreadPoolExecutor = websocket.app.state.stt_executor
    vad: UtterancePlanner = websocket.app.state.vad
    stt_queue: SttQueue = websocket.app.state.stt_queue

    await websocket.accept()
    if sessions.active_count() >= settings.max_sessions:
        await websocket.send_text(
            ErrorEvent(
                code="session_limit_reached",
                message="Server is at maximum capacity; try again later.",
                fatal=True,
            ).model_dump_json()
        )
        await websocket.close(code=CLOSE_TRY_AGAIN_LATER)
        return

    session = sessions.create()
    conn = _Connection(
        websocket,
        settings=settings,
        session=session,
        stt=stt,
        executor=executor,
        vad=vad,
        stt_queue=stt_queue,
    )
    logger.info("ws.open session_id=%s", session.id)

    try:
        while True:
            try:
                message = await asyncio.wait_for(
                    websocket.receive(), timeout=settings.ws_idle_timeout_seconds
                )
            except asyncio.TimeoutError:
                await conn.send_error(
                    "idle_timeout", "No activity for a long time; closing.", fatal=True
                )
                await websocket.close(code=CLOSE_GOING_AWAY)
                break
            if message["type"] == "websocket.disconnect":
                break
            text = message.get("text")
            if text is not None:
                if not await conn.handle_text(text):
                    await websocket.close(code=CLOSE_POLICY_VIOLATION)
                    break
                continue
            data = message.get("bytes")
            if data is not None:
                if not await conn.handle_binary(data):
                    await websocket.close(code=CLOSE_POLICY_VIOLATION)
                    break
    except WebSocketDisconnect:
        pass
    finally:
        conn.closed = True
        sessions.remove(session.id)
        logger.info(
            "ws.close session_id=%s utterances=%d leftover_bytes=%d",
            session.id, session.utterances, session.buffered_bytes,
        )
