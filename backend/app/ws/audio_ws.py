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
- STT language: Phase 1/2 pinned the hello config's `native_language`
  (KI-006). Phase 3 (ADR-0012) decodes with AUTOMATIC detection by default
  (`translation_detect_mode="auto"`) so the translation decision engine can
  apply the master prompt rules to BOTH directions; the detection is
  normalized (script inspection + ur/hi cluster + confidence gate, KI-002)
  before any decision. `"pinned"` restores the Phase 1/2 behavior;
- hallucination-only results (empty after the no-speech filter) are reported
  as `no_speech_detected`, never as an empty transcription;
- results for closed/stale sessions are dropped, never delivered;
- Phase 3 (ADR-0012): every transcription carries a `decision`
  (translate/passthrough + reason). Only `translate` decisions reach the
  translation provider — bounded by rate limit, concurrency cap and timeout;
  failures surface as non-fatal, utterance-scoped errors and the original
  transcript is kept untouched. Pass-through NEVER calls the provider
  (master prompt Rules 3+4).
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
    AudioEvent,
    ErrorEvent,
    FlushMessage,
    HelloMessage,
    PingMessage,
    PongEvent,
    ReadyEvent,
    SttInfo,
    TranscriptionEvent,
    TranslationDecisionInfo,
    TranslationEvent,
    parse_client_message,
)
from ..sessions.manager import Session, SessionManager
from ..stt.base import STTProvider, TranscriptionResult
from ..translation import TranslationService
from ..translation.decision import (
    DETECTION_PINNED,
    DetectionResult,
    TranslationDecision,
    primary_subtag,
)
from ..tts import TTSService
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
        translator: TranslationService | None = None,
        tts: TTSService | None = None,
    ) -> None:
        self.websocket = websocket
        self.settings = settings
        self.session = session
        self.stt = stt
        self.executor = executor
        self.vad = vad
        self.stt_queue = stt_queue
        self.translator = translator
        self.tts = tts
        self.hello_done = False
        self.closed = False
        self._send_lock = asyncio.Lock()
        self._pending: set[asyncio.Task[None]] = set()
        # Phase 3: in-flight translations are tracked so close can cancel
        # them (no outdated work, no sends on a dead socket).
        self._translation_tasks: set[asyncio.Task[None]] = set()
        # Phase 4: in-flight syntheses are tracked the same way.
        self._tts_tasks: set[asyncio.Task[None]] = set()
        self._max_buffer_bytes = (
            int(settings.max_utterance_seconds * settings.audio_sample_rate) * 2
        )

    # ------------------------------------------------------------------ out

    async def _send(
        self,
        event: ReadyEvent | TranscriptionEvent | TranslationEvent | ErrorEvent | PongEvent,
    ) -> None:
        if self.closed:
            return
        async with self._send_lock:
            await self.websocket.send_text(event.model_dump_json())

    async def _send_audio(self, meta: AudioEvent, pcm: bytes) -> None:
        """Send one audio metadata JSON event followed by its binary PCM16
        frame — under the SAME lock so the pairing can never interleave with
        another event (WebSocket ordering then guarantees the client can
        pair the next binary frame with this metadata)."""
        if self.closed:
            return
        async with self._send_lock:
            await self.websocket.send_text(meta.model_dump_json())
            await self.websocket.send_bytes(pcm)

    async def send_error(
        self,
        code: str,
        message: str,
        *,
        fatal: bool = False,
        utterance_id: str | None = None,
    ) -> None:
        logger.debug(
            "ws.error session_id=%s code=%s fatal=%s utterance_id=%s message=%s",
            self.session.id, code, fatal, utterance_id, message,
        )
        await self._send(
            ErrorEvent(code=code, message=message, fatal=fatal, utterance_id=utterance_id)
        )

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
            await self.flush_buffer(utterance_id=message.utterance_id)
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

    async def flush_buffer(self, utterance_id: str | None = None) -> None:
        """Transcribe the buffered audio as one utterance.

        BUG-001: the caller's `utterance_id` (client-generated correlation id)
        is attached to EVERY error this flush can produce and echoed as the
        transcription id, so the client can settle exactly this utterance —
        never a newer one (the old shared-ref attribution raced under load).
        """
        audio = self.session.take_buffer()
        min_bytes = int(self.settings.min_utterance_seconds * self.settings.audio_sample_rate) * 2
        if len(audio) < min_bytes:
            await self.send_error(
                "nothing_to_transcribe",
                "No speech captured since the last transcription.",
                utterance_id=utterance_id,
            )
            return

        # Backpressure (Phase 2): bound in-flight decodes per session, then
        # globally. Overflow is a non-fatal `server_busy` — the buffered audio
        # was consumed and is dropped BY DESIGN (ADR-0009), so there is nothing
        # for the client to re-flush; it simply keeps speaking.
        if len(self._pending) >= self.settings.stt_max_pending_per_session:
            logger.info(
                "ws.busy session_id=%s reason=pending_session pending=%d",
                self.session.id, len(self._pending),
            )
            await self.send_error(
                "server_busy",
                f"Still transcribing previous speech "
                f"({len(self._pending)}/{self.settings.stt_max_pending_per_session} in flight) — "
                f"that segment was dropped; keep talking, the next one will be transcribed.",
                utterance_id=utterance_id,
            )
            return
        if not self.stt_queue.try_acquire():
            stats = self.stt_queue.stats()
            logger.info(
                "ws.busy session_id=%s reason=queue_full queued=%d",
                self.session.id, stats["queued"],
            )
            await self.send_error(
                "server_busy",
                f"Server is busy transcribing other audio "
                f"(queue {stats['queued']}/{stats['max']}) — that segment was dropped; "
                f"keep talking, the next one will be transcribed.",
                utterance_id=utterance_id,
            )
            return

        self.session.utterances += 1
        if utterance_id is None:
            # Server-initiated auto-flush (buffer cap): generate the id here.
            utterance_id = f"u_{self.session.utterances:04d}_{uuid.uuid4().hex[:6]}"
        task = asyncio.create_task(self._transcribe_and_deliver(audio, utterance_id))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def _language_hint(self) -> str | None:
        """Primary subtag of the configured native language ("ur-PK" → "ur").

        The default STT language (pinned mode + translation-disabled path,
        KI-006/ADR-0006). In auto mode the decision engine replaces it with
        the resolved detection language before the decode. Returns None when
        no valid hello config is available.
        """
        config = self.session.config
        if config is None or not config.native_language:
            return None
        primary = config.native_language.split("-", 1)[0].strip().lower()
        return primary or None

    async def _transcribe_and_deliver(self, audio: bytes, utterance_id: str) -> None:
        loop = asyncio.get_running_loop()
        started = time.perf_counter()
        config = self.session.config
        auto_mode = (
            self.translator is not None
            and self.translator.enabled
            and self.translator.detect_mode == "auto"
            and config is not None
        )
        # Pinned default (KI-006): the configured native language. In auto
        # mode this is REPLACED below by the resolved detection language.
        hint = self._language_hint()
        detection: DetectionResult | None = None
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
                    utterance_id=utterance_id,
                )
                return

            # Phase 3 (ADR-0012 v2): standalone detect pass, then ONE decode
            # pinned to the resolved language — never a full auto-decode
            # (KI-002/KI-006: auto decode mislabels Hindustani and is 3–4×
            # slower; the standalone detector + cluster/threshold rules are).
            if auto_mode and self.translator is not None:
                raw_label, raw_prob = await asyncio.wait_for(
                    loop.run_in_executor(
                        self.executor,
                        lambda: self.stt.detect_pcm(
                            audio, sample_rate=self.settings.audio_sample_rate
                        ),
                    ),
                    timeout=self.settings.stt_timeout_seconds,
                )
                assert config is not None
                detection = self.translator.resolve_detection(
                    raw_label, raw_prob, native_language=config.native_language
                )
                hint = detection.language
                logger.info(
                    "ws.detect session_id=%s id=%s raw=%s p=%.2f resolved=%s source=%s",
                    self.session.id, utterance_id, raw_label, raw_prob,
                    detection.language, detection.source,
                )

            result: TranscriptionResult = await asyncio.wait_for(
                self._decode_plan(plan, hint),
                timeout=self.settings.stt_timeout_seconds * len(plan.chunks),
            )
        except asyncio.TimeoutError:
            await self.send_error(
                "stt_timeout", "Speech recognition took too long; try a shorter utterance.",
                utterance_id=utterance_id,
            )
            return
        except Exception:  # noqa: BLE001 — provider failure must not kill the socket
            logger.exception("stt failed session_id=%s", self.session.id)
            await self.send_error(
                "stt_error", "Speech recognition failed.", utterance_id=utterance_id
            )
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
                utterance_id=utterance_id,
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

        # Phase 3 (ADR-0012): decide translate vs pass-through for this
        # utterance. Pure logic — no provider call happens here (Rules 3+4).
        decision: TranslationDecision | None = None
        if self.translator is not None and config is not None:
            if detection is None:
                if self.translator.enabled:
                    # Pinned mode: the user's selection is authoritative.
                    detection = DetectionResult(
                        primary_subtag(config.native_language), 1.0, DETECTION_PINNED
                    )
                else:
                    detection = None
            if detection is not None:
                decision = self.translator.decide_from(
                    detection,
                    text=result.text,
                    native_language=config.native_language,
                    preferred_language=config.preferred_language,
                )

        # In auto mode the event reports the RESOLVED language (the raw
        # whisper label — e.g. `hi` for Urdu speech, KI-002 — lives in the
        # server log and in decision.detection).
        language_for_event = detection.language if detection is not None else result.language
        probability_for_event = (
            detection.probability if detection is not None else result.language_probability
        )

        event = TranscriptionEvent(
            id=utterance_id,
            text=result.text,
            language=language_for_event,
            language_probability=round(probability_for_event, 4),
            audio_duration_s=round(result.audio_duration_s, 2),
            processing_ms=processing_ms,
            low_confidence=self.stt.is_low_confidence(probability_for_event),
            decision=(
                TranslationDecisionInfo(
                    action=decision.action,
                    reason=decision.reason,
                    source_language=decision.source_language,
                    target_language=decision.target_language,
                    detection=decision.detection,
                )
                if decision is not None
                else None
            ),
        )
        if decision is not None:
            logger.info(
                "ws.decision session_id=%s id=%s action=%s reason=%s src=%s tgt=%s detection=%s",
                self.session.id, utterance_id, decision.action, decision.reason,
                decision.source_language, decision.target_language, decision.detection,
            )
        logger.info(
            "ws.transcription session_id=%s id=%s hint=%s language=%s prob=%.2f duration_s=%.2f processing_ms=%d chars=%d",
            self.session.id, utterance_id, hint, event.language, event.language_probability,
            event.audio_duration_s, processing_ms, len(event.text),
        )
        logger.debug("ws.transcript session_id=%s text=%r", self.session.id, event.text)
        await self._send(event)

        if (
            decision is not None
            and decision.action == "translate"
            and decision.target_language is not None
        ):
            task = asyncio.create_task(
                self._translate_and_deliver(utterance_id, result.text, decision)
            )
            self._translation_tasks.add(task)
            task.add_done_callback(self._translation_tasks.discard)

    async def _translate_and_deliver(
        self, utterance_id: str, text: str, decision: TranslationDecision
    ) -> None:
        """Run one bounded translation (Phase 3) and deliver the outcome.

        The provider call happens inside the TranslationService (rate limit,
        concurrency cap, timeout); failures become non-fatal, utterance-
        scoped errors and the original transcript is never touched.
        """
        assert self.translator is not None and decision.target_language is not None
        outcome = await self.translator.translate(
            text=text,
            source_language=decision.source_language,
            target_language=decision.target_language,
        )
        if self.closed or self.session.closed:
            logger.debug(
                "ws.translation_stale session_id=%s id=%s status=%s dropped",
                self.session.id, utterance_id, outcome.status,
            )
            return
        if outcome.status == "ok":
            await self._send(
                TranslationEvent(
                    id=utterance_id,
                    text=outcome.text,
                    source_language=decision.source_language,
                    target_language=decision.target_language,
                    provider=outcome.provider,
                    duration_ms=outcome.duration_ms,
                )
            )
            # Phase 4 (ADR-0013): speak the translated text. Pass-through
            # never reaches this branch — original audio is NEVER regenerated
            # with TTS (master prompt §17).
            if self.tts is not None and self.tts.enabled and outcome.text.strip():
                task = asyncio.create_task(
                    self._synthesize_and_deliver(
                        utterance_id, text=outcome.text, language=decision.target_language
                    )
                )
                self._tts_tasks.add(task)
                task.add_done_callback(self._tts_tasks.discard)
            return
        messages = {
            "failed": f"Translation failed ({outcome.error_message or 'provider error'}) — the original transcript is kept.",
            "timeout": f"Translation took too long and was skipped — the original transcript is kept.",
            "rate_limited": "Translation rate limit reached — the original transcript is kept.",
        }
        codes = {
            "failed": "translation_failed",
            "timeout": "translation_timeout",
            "rate_limited": "translation_rate_limited",
        }
        await self.send_error(
            codes[outcome.status],
            messages[outcome.status],
            utterance_id=utterance_id,
        )

    async def _synthesize_and_deliver(
        self, utterance_id: str, *, text: str, language: str
    ) -> None:
        """Synthesize one translated utterance (Phase 4) and deliver the audio.

        The provider call happens inside the TTSService (rate limit,
        concurrency cap, timeout); failures become non-fatal, utterance-
        scoped errors — the translated text is already on screen, only the
        audio is missing.
        """
        assert self.tts is not None
        outcome = await self.tts.synthesize(text=text, language=language)
        if self.closed or self.session.closed:
            logger.debug(
                "ws.tts_stale session_id=%s id=%s status=%s dropped",
                self.session.id, utterance_id, outcome.status,
            )
            return
        if outcome.status == "ok":
            if len(outcome.pcm) < 2 or len(outcome.pcm) % 2 != 0:
                # Defensive: never send a malformed PCM frame.
                await self.send_error(
                    "tts_failed",
                    "Voice synthesis produced malformed audio — the translated text is kept.",
                    utterance_id=utterance_id,
                )
                return
            await self._send_audio(
                AudioEvent(
                    id=utterance_id,
                    language=language,
                    sample_rate=outcome.sample_rate,
                    bytes=len(outcome.pcm),
                    duration_ms=outcome.audio_duration_ms,
                    synthesis_ms=outcome.duration_ms,
                    provider=outcome.provider,
                ),
                outcome.pcm,
            )
            return
        messages = {
            "failed": f"Voice synthesis failed ({outcome.error_message or 'provider error'}) — the translated text is kept.",
            "timeout": "Voice synthesis took too long and was skipped — the translated text is kept.",
            "rate_limited": "Voice rate limit reached — the translated text is kept.",
            "unsupported": f"No voice available for {language} — the translated text is kept.",
            "text_too_long": "This sentence is too long to be spoken — the translated text is kept.",
        }
        codes = {
            "failed": "tts_failed",
            "timeout": "tts_timeout",
            "rate_limited": "tts_rate_limited",
            "unsupported": "tts_unsupported_language",
            "text_too_long": "tts_text_too_long",
        }
        await self.send_error(
            codes[outcome.status],
            messages[outcome.status],
            utterance_id=utterance_id,
        )

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
        translator=getattr(websocket.app.state, "translator", None),
        tts=getattr(websocket.app.state, "tts", None),
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
        for task in list(conn._translation_tasks):
            task.cancel()  # cancellation of outdated requests (prompt §10/§15)
        for task in list(conn._tts_tasks):
            task.cancel()  # Phase 4: cancel outdated syntheses the same way
        sessions.remove(session.id)
        logger.info(
            "ws.close session_id=%s utterances=%d leftover_bytes=%d",
            session.id, session.utterances, session.buffered_bytes,
        )
