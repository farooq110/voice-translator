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
- Phase 5 (ADR-0014): two-participant rooms. The host owns the room's
  language pair; the guest configures nothing. Transcripts and translations
  are broadcast to BOTH members with a `speaker` tag; synthesized speech is
  routed ONLY to the other participant (a solo room falls back to local
  playback — the exact Phase 1–4 shape); pass-through utterances relay the
  ORIGINAL captured audio to the peer (never regenerated, never replayed to
  the speaker — feedback-loop prevention). Rooms linger briefly after their
  last member leaves so a dropped connection can rejoin with the same code.
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
    HelloConfig,
    HelloMessage,
    PeerEvent,
    PingMessage,
    PongEvent,
    ReadyEvent,
    RelayEvent,
    RoomInfo,
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
from .rooms import Room, RoomFullError, RoomManager, RoomMember

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
        rooms: RoomManager | None = None,
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
        self.rooms = rooms
        # Phase 5 (ADR-0014): room membership. Every host connection owns a
        # room; guests join their invite's room.
        self.role: str = "host"
        self.room: Room | None = None
        self.hello_done = False
        self.closed = False
        # QA round (BUG-002): monotonic timestamp of the last frame RECEIVED
        # from this client (any text/binary/disconnect). The endpoint loop
        # refreshes it on every message. A seat whose owner has sent nothing
        # for `room_stale_seat_seconds` is a GHOST (the platform proxy can
        # swallow a dead client's disconnect) and may be reclaimed on rejoin.
        self.last_seen: float = time.monotonic()
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
        event: (
            ReadyEvent
            | TranscriptionEvent
            | TranslationEvent
            | ErrorEvent
            | PongEvent
            | PeerEvent
        ),
    ) -> None:
        await self.room_send(event.model_dump_json(exclude_none=True))

    async def room_send(self, payload: str) -> None:
        """RoomMember protocol: one JSON text frame to this connection.
        Swallows send errors on dead sockets — a failing member must never
        break another member's delivery."""
        if self.closed:
            return
        async with self._send_lock:
            try:
                await self.websocket.send_text(payload)
            except Exception:  # noqa: BLE001 — dead socket, nothing to deliver
                logger.debug("ws.send_failed session_id=%s", self.session.id)

    async def room_send_media(self, meta_json: str, pcm: bytes) -> None:
        """One metadata JSON frame + its binary PCM frame under the SAME lock
        so the pairing can never interleave with another event (WebSocket
        ordering then lets the client pair the next binary frame with this
        metadata). Used for BOTH TTS `audio` and pass-through `relay`."""
        if self.closed:
            return
        async with self._send_lock:
            try:
                await self.websocket.send_text(meta_json)
                await self.websocket.send_bytes(pcm)
            except Exception:  # noqa: BLE001 — dead socket
                logger.debug("ws.send_media_failed session_id=%s", self.session.id)

    # ------------------------------------------------------------- routing

    def _speaker(self) -> str | None:
        """`speaker` tag for events in room mode; None keeps the exact
        Phase 1–4 solo wire shape."""
        return self.role if self.room is not None else None

    def _effective_config(self) -> HelloConfig | None:
        """The language pair that governs decisions: the ROOM's config (the
        host's — the guest configures nothing, ADR-0014) when in a room,
        else this connection's own config (solo)."""
        if self.room is not None and self.room.config is not None:
            return self.room.config
        return self.session.config

    def _audio_recipients(self) -> tuple[list[RoomMember], bool]:
        """Who should receive this speaker's synthesized audio, and whether
        the delivery is cross-participant (drives the `speaker` tag).
        Room with a peer → the OTHER participant only (master prompt: the
        system never translates a participant's speech back at them).
        Solo room / no room → the speaker (exact Phase 1–4 fallback)."""
        if self.room is not None:
            peers = self.room.peers_of(self.role)
            if peers:
                return peers, True
        return [self], False

    async def _broadcast_text(
        self, event: TranscriptionEvent | TranslationEvent | ErrorEvent
    ) -> None:
        """Send one text event to EVERY room member (transcripts, translations
        and their errors are shared; each client tags lines by speaker).
        Falls back to a solo send when no room is attached."""
        if self.room is None:
            await self._send(event)
            return
        payload = event.model_dump_json(exclude_none=True)
        for member in self.room.everyone():
            await member.room_send(payload)

    async def _send_error_to(
        self,
        members: list[RoomMember],
        *,
        code: str,
        message: str,
        utterance_id: str | None,
        cross_participant: bool,
    ) -> None:
        payload = ErrorEvent(
            code=code,
            message=message,
            utterance_id=utterance_id,
            speaker=self.role if cross_participant else None,
        ).model_dump_json(exclude_none=True)
        for member in members:
            await member.room_send(payload)

    async def leave_room(self) -> None:
        """Detach from the room on disconnect: tell the remaining members the
        peer is gone, free the slot (a reconnecting client re-hellos with the
        same code), and arm the linger destruction when nobody is left."""
        room = self.room
        self.room = None
        if room is None:
            return
        role = self.role
        # QA round (BUG-002): if this seat was reclaimed (stale-seat eviction)
        # and is now owned by a NEWER connection, this ghost's cleanup must
        # not eject the returning participant or fire a bogus "left" event.
        if room.members.get(role) is not self:
            logger.info(
                "ws.leave_skipped_seat_taken session_id=%s role=%s",
                self.session.id, role,
            )
            return
        room.leave(role)
        for member in room.everyone():
            await member.room_send(
                PeerEvent(
                    event="left",
                    role=role,  # type: ignore[arg-type]
                    peers=room.peer_count(excluding=member.role),
                ).model_dump_json(exclude_none=True)
            )
        if not room.members and self.rooms is not None:
            room.schedule_destroy(self.rooms)

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

    async def _reclaim_stale_seat(self, room: Room, role: str) -> bool:
        """QA round (BUG-002): free `role`'s seat when its owner is a GHOST.

        The preview proxy can swallow a dead client's disconnect, so the
        server may keep the seat occupied for the full idle timeout (180 s)
        while the participant is long gone. A returning participant that
        re-hellos with the same code and role proves the seat owner is back;
        the OLD connection is evicted when it has sent nothing for
        `room_stale_seat_seconds` (a live client pings every 4 s and streams
        audio while recording, so it can never look stale).

        Returns True when a stale seat was evicted (caller retries the join).
        """
        stale_after = max(0.0, self.settings.room_stale_seat_seconds)
        ghost = room.members.get(role)
        if ghost is None:
            return False
        ghost_seen = getattr(ghost, "last_seen", None)
        if ghost_seen is None or (time.monotonic() - ghost_seen) <= stale_after:
            # Seat owner is alive (recent frames) — genuinely occupied.
            return False
        logger.warning(
            "ws.room_evict_ghost code=%s role=%s silent_s=%.1f threshold_s=%.1f",
            room.code, role, time.monotonic() - ghost_seen, stale_after,
        )
        room.leave(role)
        ghost.room = None  # its eventual cleanup must not touch this room
        # Tell the ghost why it lost the room, then close it server-side
        # (swallow errors — its socket may already be unusable).
        await ghost.room_send(
            ErrorEvent(
                code="seat_taken",
                message="Your seat was taken over by a new connection.",
                fatal=True,
            ).model_dump_json(exclude_none=True)
        )
        try:
            await ghost.websocket.close(code=CLOSE_POLICY_VIOLATION)
        except Exception:  # noqa: BLE001 — dead socket
            pass
        # Notify whoever remains (e.g. the peer) that the ghost is gone.
        for member in room.everyone():
            await member.room_send(
                PeerEvent(
                    event="left",
                    role=role,  # type: ignore[arg-type]
                    peers=room.peer_count(excluding=member.role),
                ).model_dump_json(exclude_none=True)
            )
        if not room.members and self.rooms is not None:
            room.schedule_destroy(self.rooms)
        return True

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

        self.role = config.role

        if self.rooms is None:
            # Defensive: no room manager (should never happen) — behave solo.
            if not config.native_language or not config.preferred_language:
                await self.send_error(
                    "missing_languages",
                    "Both a native and a preferred translation language are required.",
                    fatal=True,
                )
                return False
            self.session.config = config
            self.hello_done = True
            await self._send(
                ReadyEvent(
                    session_id=self.session.id,
                    audio=config.audio,
                    stt=SttInfo.model_validate(self.stt.describe()),
                )
            )
            return True

        if config.role == "guest":
            return await self._join_room_as_guest(config)

        # ---- host path (also the Phase 1–4 solo shape: role defaults to host)
        if not config.native_language or not config.preferred_language:
            await self.send_error(
                "missing_languages",
                "Both a native and a preferred translation language are required.",
                fatal=True,
            )
            return False
        if config.native_language == config.preferred_language:
            # Not fatal: same-language conversations are valid; the translation
            # engine (Phase 3) will treat this configuration as pass-through.
            await self.send_error(
                "identical_languages",
                "Native and preferred languages are identical; pass-through behavior will apply.",
            )

        room = self.rooms.get(config.room) if config.room else None
        if room is not None:
            # Rejoin within the linger window (network drop). The host stays
            # the config authority — apply its (possibly edited) languages.
            try:
                room.join("host", self)
            except RoomFullError:
                # QA round (BUG-002): the seat may be held by a ghost (the
                # proxy swallowed its disconnect). Reclaim it when silent.
                if not await self._reclaim_stale_seat(room, "host"):
                    await self.send_error(
                        "room_full",
                        "This room already has an active host connection.",
                        fatal=True,
                    )
                    return False
                room.join("host", self)
            room.config = config
            logger.info("ws.room_rejoin session_id=%s code=%s", self.session.id, room.code)
        else:
            # A stale/expired code simply creates a fresh room — the host
            # receives the NEW code in `ready` and re-shares the link.
            room = self.rooms.create(config)
            room.join("host", self)  # register the creating host immediately

        self.room = room
        self.session.config = config
        self.hello_done = True
        logger.info(
            "ws.hello session_id=%s role=%s room=%s native=%s preferred=%s",
            self.session.id, config.role, room.code,
            config.native_language, config.preferred_language,
        )
        await self._send(
            ReadyEvent(
                session_id=self.session.id,
                audio=config.audio,
                stt=SttInfo.model_validate(self.stt.describe()),
                room=RoomInfo(
                    code=room.code,
                    role="host",
                    peers=room.peer_count(excluding="host"),
                    native_language=config.native_language,
                    preferred_language=config.preferred_language,
                ),
            )
        )
        # If the guest was already waiting (host rejoin), tell it the host
        # is back.
        for member in room.peers_of("host"):
            await member.room_send(
                PeerEvent(
                    event="joined", role="host", peers=room.peer_count(excluding=member.role)
                ).model_dump_json(exclude_none=True)
            )
        return True

    async def _join_room_as_guest(self, config: HelloConfig) -> bool:
        """Guest hello: join the invited room, configure nothing (ADR-0014)."""
        if not config.room:
            await self.send_error(
                "room_required",
                "A guest connection must present a room code.",
                fatal=True,
            )
            return False
        room = self.rooms.get(config.room)
        if room is None or room.config is None:
            await self.send_error(
                "room_not_found",
                "That room code is unknown or has expired — ask for a new invite link.",
                fatal=True,
            )
            return False
        try:
            room.join("guest", self)
        except RoomFullError:
            # QA round (BUG-002): reclaim the seat when its owner is a ghost.
            if not await self._reclaim_stale_seat(room, "guest"):
                await self.send_error(
                    "room_full",
                    "This room already has two participants.",
                    fatal=True,
                )
                return False
            room.join("guest", self)
        room_config = room.config
        self.room = room
        self.session.config = config  # audio format only; languages come from the room
        self.hello_done = True
        logger.info("ws.hello session_id=%s role=guest room=%s", self.session.id, room.code)
        await self._send(
            ReadyEvent(
                session_id=self.session.id,
                audio=config.audio,
                stt=SttInfo.model_validate(self.stt.describe()),
                room=RoomInfo(
                    code=room.code,
                    role="guest",
                    peers=room.peer_count(excluding="guest"),
                    native_language=room_config.native_language,
                    preferred_language=room_config.preferred_language,
                ),
            )
        )
        for member in room.peers_of("guest"):
            await member.room_send(
                PeerEvent(
                    event="joined", role="guest", peers=room.peer_count(excluding=member.role)
                ).model_dump_json(exclude_none=True)
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
        """Primary subtag of the effective native language ("ur-PK" → "ur").

        The effective config is the ROOM's (host) config in room mode — the
        guest configures nothing but is decoded against the host's pair
        (ADR-0014) — else this connection's config (solo). In auto mode the
        decision engine replaces the hint with the resolved detection
        language before the decode. Returns None when no config is available.
        """
        config = self._effective_config()
        if config is None or not config.native_language:
            return None
        primary = config.native_language.split("-", 1)[0].strip().lower()
        return primary or None

    async def _transcribe_and_deliver(self, audio: bytes, utterance_id: str) -> None:
        loop = asyncio.get_running_loop()
        started = time.perf_counter()
        # Phase 5 (ADR-0014): decisions are governed by the ROOM's config —
        # the host's language pair for BOTH participants (the guest
        # configures nothing).
        config = self._effective_config()
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
            speaker=self._speaker(),
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
        # Phase 5: the transcript is shared — BOTH participants see every
        # line, tagged with its speaker (each client renders You / Guest).
        await self._broadcast_text(event)

        # Phase 5 (ADR-0014): a pass-through decision relays the ORIGINAL
        # captured audio (VAD-trimmed speech region, 16 kHz) to the OTHER
        # participant — as captured, never regenerated, never replayed to the
        # speaker (master prompt Rules 3+4; feedback-loop prevention). Solo
        # rooms keep the exact Phase 1–4 shape (no relay, no one to hear it).
        if (
            decision is not None
            and decision.action == "passthrough"
            and self.room is not None
        ):
            peers = self.room.peers_of(self.role)
            relay_pcm = b"".join(plan.chunks)
            if peers and len(relay_pcm) >= 2:
                relay = RelayEvent(
                    id=utterance_id,
                    speaker=self.role,  # type: ignore[arg-type]
                    language=language_for_event,
                    sample_rate=self.settings.audio_sample_rate,
                    channels=1,
                    bytes=len(relay_pcm),
                    duration_ms=int(plan.source_duration_s * 1000),
                )
                logger.info(
                    "ws.relay session_id=%s id=%s to_peers=%d bytes=%d",
                    self.session.id, utterance_id, len(peers), len(relay_pcm),
                )
                meta_json = relay.model_dump_json(exclude_none=True)
                for member in peers:
                    await member.room_send_media(meta_json, relay_pcm)

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
            await self._broadcast_text(
                TranslationEvent(
                    id=utterance_id,
                    speaker=self._speaker(),
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
        # Phase 5: the translated text is shared, so the failure is shared —
        # BOTH members mark the line (the original transcript is kept).
        await self._send_error_to(
            self.room.everyone() if self.room is not None else [self],
            code=codes[outcome.status],
            message=messages[outcome.status],
            utterance_id=utterance_id,
            cross_participant=self.room is not None,
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
        recipients, cross_participant = self._audio_recipients()
        if outcome.status == "ok":
            if len(outcome.pcm) < 2 or len(outcome.pcm) % 2 != 0:
                # Defensive: never send a malformed PCM frame.
                await self._send_error_to(
                    recipients,
                    code="tts_failed",
                    message="Voice synthesis produced malformed audio — the translated text is kept.",
                    utterance_id=utterance_id,
                    cross_participant=cross_participant,
                )
                return
            # Phase 5 (ADR-0014): audio is routed to the OTHER participant
            # (never back to the speaker); a solo room falls back to the
            # speaker — the exact Phase 1–4 local-playback behavior.
            meta_json = AudioEvent(
                id=utterance_id,
                language=language,
                sample_rate=outcome.sample_rate,
                bytes=len(outcome.pcm),
                duration_ms=outcome.audio_duration_ms,
                synthesis_ms=outcome.duration_ms,
                provider=outcome.provider,
                speaker=self.role if cross_participant else None,
            ).model_dump_json(exclude_none=True)
            logger.info(
                "ws.audio session_id=%s id=%s bytes=%d synthesis_ms=%d recipients=%d cross=%s",
                self.session.id, utterance_id, len(outcome.pcm),
                outcome.duration_ms, len(recipients), cross_participant,
            )
            for member in recipients:
                await member.room_send_media(meta_json, outcome.pcm)
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
        # Phase 5: the audio was FOR the recipient — the recipient is the one
        # missing it, so the utterance-scoped error goes to the same set the
        # audio would have reached.
        await self._send_error_to(
            recipients,
            code=codes[outcome.status],
            message=messages[outcome.status],
            utterance_id=utterance_id,
            cross_participant=cross_participant,
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
        rooms=getattr(websocket.app.state, "rooms", None),
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
            # QA round (BUG-002): every received frame proves the client is
            # alive — the stale-seat eviction heuristic depends on it.
            conn.last_seen = time.monotonic()
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
        await conn.leave_room()  # Phase 5: notify the peer, arm the linger
        sessions.remove(session.id)
        logger.info(
            "ws.close session_id=%s utterances=%d leftover_bytes=%d",
            session.id, session.utterances, session.buffered_bytes,
        )
