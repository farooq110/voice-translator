"""Audio WebSocket protocol — version 1.

Versioned contract between the browser frontend and the backend. The full
specification lives in `docs/audio-protocol.md`; any breaking change must
introduce a new protocol version and a matching doc update.

Framing rules (v1):
- Control messages: JSON text frames, discriminated by `type`, carrying `v=1`.
- Audio: raw binary frames, PCM16 little-endian, mono, 16 kHz.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

PROTOCOL_VERSION = 1
SUPPORTED_ENCODING = "pcm_s16le"
SUPPORTED_SAMPLE_RATE = 16_000
SUPPORTED_CHANNELS = 1


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# client -> server
# ---------------------------------------------------------------------------


class AudioFormat(StrictModel):
    encoding: Literal["pcm_s16le"]
    sample_rate: int = Field(ge=8_000, le=48_000)
    channels: int = Field(ge=1, le=2)


_LANG_PATTERN = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,8})?$")


class ClientInfo(StrictModel):
    """Optional self-reported client platform (Phase 6, KI-004).

    A coarse lowercase tag such as "ios-safari" or "android-chrome" — never
    a raw user-agent string. Purely observational: the server aggregates it
    into /health so the real-device matrix can see which platforms connect.
    """

    platform: str = Field(min_length=2, max_length=24, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")


class HelloConfig(StrictModel):
    """Per-connection hello configuration.

    Phase 5 (ADR-0014): the hello also carries the connection's ROOM role.

    - `role="host"` (default — the Phase 1–4 shape) is the translator user:
      it configures `native_language` + `preferred_language` (both REQUIRED,
      validated in the endpoint) and owns the room's language pair.
    - `role="guest"` is the other participant: it configures NOTHING — it
      sends only the `room` code it was invited with; the language pair comes
      from the room (the host's config). Any language fields a guest sends
      are accepted for tolerance and ignored.

    `room` is the invite code (host may re-send its own code to rejoin after
    a network drop — the room lingers briefly after its last member leaves).

    `client` (Phase 6, optional) is the self-reported platform tag — absent
    on older clients and never required.
    """

    native_language: str = ""
    preferred_language: str = ""
    role: Literal["host", "guest"] = "host"
    room: str | None = Field(default=None, pattern=r"^[A-Z0-9]{4,10}$")
    client: ClientInfo | None = None
    audio: AudioFormat

    @field_validator("native_language", "preferred_language")
    @classmethod
    def _language_shape(cls, value: str) -> str:
        if value != "" and not _LANG_PATTERN.match(value):
            raise ValueError("language must be an ISO-639-1 tag like 'ur' or 'en-US'")
        return value.lower() if value else value


class HelloMessage(StrictModel):
    type: Literal["hello"] = "hello"
    v: Literal[PROTOCOL_VERSION]
    config: HelloConfig


class PingMessage(StrictModel):
    type: Literal["ping"] = "ping"
    v: Literal[PROTOCOL_VERSION]
    ts: int | None = None


class FlushMessage(StrictModel):
    """Request transcription of all audio buffered since the last flush.

    `utterance_id` (optional, BUG-001 fix): a client-generated correlation id
    echoed on every event/error for that flush, so the client can settle
    exactly the utterance a result belongs to. Absent on server-initiated
    auto-flushes (the server then generates its own id).
    """

    type: Literal["flush"] = "flush"
    v: Literal[PROTOCOL_VERSION]
    utterance_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$"
    )


ClientMessage = Annotated[
    Union[HelloMessage, PingMessage, FlushMessage],
    Field(discriminator="type"),
]

_client_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def parse_client_message(payload: Any) -> ClientMessage:
    """Validate a decoded JSON payload. Raises pydantic.ValidationError."""
    return _client_adapter.validate_python(payload)


# ---------------------------------------------------------------------------
# server -> client
# ---------------------------------------------------------------------------


class SttInfo(StrictModel):
    provider: str
    model: str
    device: str
    loaded: bool


class RoomInfo(StrictModel):
    """Room membership snapshot attached to `ready` (Phase 5, ADR-0014)."""

    code: str
    role: Literal["host", "guest"]
    peers: int = Field(ge=0, le=1)
    """How many OTHER participants are in the room right now."""
    native_language: str = ""
    """The room's configured pair (empty until the host has said hello)."""
    preferred_language: str = ""


class ReadyEvent(StrictModel):
    """Handshake acknowledgment; sent after a valid `hello`."""

    type: Literal["ready"] = "ready"
    v: int = PROTOCOL_VERSION
    session_id: str
    audio: AudioFormat
    stt: SttInfo
    """Phase 5: present when the connection joined a room (every host
    connection owns a room; guests join their invite's room)."""
    room: RoomInfo | None = None


class TranslationDecisionInfo(StrictModel):
    """Why this utterance will (or will not) be translated — Phase 3, ADR-0012.

    `action="translate"` carries `target_language`; every `passthrough`
    reason explains itself (third language, low confidence, unsupported pair,
    …). `detection` records HOW `source_language` was determined:
    `whisper` (model label), `script` (text script evidence overrode the
    label — KI-002), `cluster` (ur/hi register collapse), `pinned` (the
    hello config's native language) or `unknown`.
    """

    action: Literal["translate", "passthrough"]
    reason: Literal[
        "native_language",
        "preferred_language",
        "third_language",
        "identical_config",
        "low_confidence",
        "undetected_language",
        "unsupported_pair",
        "text_too_long",
    ]
    source_language: str
    target_language: str | None = None
    detection: Literal["whisper", "cluster", "pinned", "fallback", "unknown"]


class TranscriptionEvent(StrictModel):
    type: Literal["transcription"] = "transcription"
    v: int = PROTOCOL_VERSION
    id: str
    """Phase 5 (ADR-0014): who spoke this utterance — present only in room
    mode ("host" or "guest"). Solo connections keep the exact Phase 1–4
    shape (field absent). Clients key transcript entries by (speaker, id):
    utterance ids are only unique PER CONNECTION."""
    speaker: Literal["host", "guest"] | None = None
    text: str
    language: str
    language_probability: float = Field(ge=0.0, le=1.0)
    audio_duration_s: float = Field(ge=0.0)
    processing_ms: int = Field(ge=0)
    is_final: bool = True
    low_confidence: bool = False
    """Phase 3 (ADR-0012): present only when translation is enabled on the
    server. `action="translate"` is followed by a `translation` event with
    the same id; `passthrough` means NO translation will arrive — the
    original text is final."""
    decision: TranslationDecisionInfo | None = None


class ErrorEvent(StrictModel):
    type: Literal["error"] = "error"
    v: int = PROTOCOL_VERSION
    code: str
    message: str
    fatal: bool = False
    """Phase 5: speaker attribution for utterance-scoped errors that are
    delivered to a room member OTHER than the speaker (translation and TTS
    outcomes are shared; the recipient must attach them to the right line).
    Absent for speaker-local errors (STT-level) and solo connections."""
    speaker: Literal["host", "guest"] | None = None
    """BUG-001 fix: the flush this error belongs to (client-generated
    utterance_id echoed back), so the client can settle exactly that
    utterance's pending state. None for connection-level errors and
    server-initiated auto-flushes."""
    utterance_id: str | None = None


class TranslationEvent(StrictModel):
    """Translated text for the utterance with the same id (Phase 3, ADR-0012).

    Arrives AFTER its `transcription` (which already carries the decision).
    Only ever sent when the decision was `action="translate"` — original
    pass-through audio/text never reaches a provider (master prompt Rule 4).
    """

    type: Literal["translation"] = "translation"
    v: int = PROTOCOL_VERSION
    id: str
    """Phase 5 (ADR-0014): who spoke the translated utterance (room mode)."""
    speaker: Literal["host", "guest"] | None = None
    text: str
    source_language: str
    target_language: str
    provider: str
    duration_ms: int = Field(ge=0)


class PongEvent(StrictModel):
    type: Literal["pong"] = "pong"
    v: int = PROTOCOL_VERSION
    ts: int


class AudioEvent(StrictModel):
    """Synthesized-speech metadata (Phase 4, ADR-0013).

    Arrives AFTER the `translation` event with the same id — and is ALWAYS
    followed by exactly one BINARY frame containing `bytes` octets of raw
    PCM16 little-endian mono audio at `sample_rate` (WebSocket ordering
    guarantees the pairing; the client pairs the next binary frame it sees
    with the most recent `audio` metadata).

    Only ever sent for `translate` decisions — original pass-through audio
    is forwarded as captured and is NEVER regenerated with TTS (master
    prompt §17). Absent when TTS is disabled or the voice for the target
    language is not provisioned (a non-fatal, utterance-scoped error is
    emitted instead).
    """

    type: Literal["audio"] = "audio"
    v: int = PROTOCOL_VERSION
    id: str
    language: str
    encoding: Literal["pcm_s16le"] = "pcm_s16le"
    sample_rate: int = Field(ge=8_000, le=48_000)
    channels: int = Field(default=1, ge=1, le=2)
    bytes: int = Field(ge=2)
    """Size of the following binary frame (a multiple of 2 — PCM16 samples)."""
    duration_ms: int = Field(ge=0)
    """Duration of the synthesized audio (not the synthesis time)."""
    synthesis_ms: int = Field(ge=0)
    provider: str
    """Phase 5 (ADR-0014): who spoke the utterance this voice belongs to
    (room mode) — audio is routed to the OTHER participant, so the recipient
    can mark the clip as remote (feedback-loop playback guard)."""
    speaker: Literal["host", "guest"] | None = None


class PeerEvent(StrictModel):
    """Room membership change (Phase 5, ADR-0014).

    Sent to every room member when the other participant joins or leaves.
    `peers` is the number of OTHER members after the change."""

    type: Literal["peer"] = "peer"
    v: int = PROTOCOL_VERSION
    event: Literal["joined", "left"]
    role: Literal["host", "guest"]
    peers: int = Field(ge=0, le=1)


class RelayEvent(StrictModel):
    """Original-audio pass-through relay (Phase 5, ADR-0014).

    For a `passthrough` decision (third language), the ORIGINAL captured
    audio of the utterance is relayed to the OTHER participant as captured —
    never regenerated with TTS (master prompt Rules 3+4). Framing is the
    same metadata + ONE binary PCM16 frame pair as `audio`, at the capture
    rate (16 kHz). The speaker never receives their own relay (they are
    speaking the original — replaying it would be a feedback loop).

    Absent in solo mode (no one to relay to) and when translation is
    disabled (no decision is made at all).
    """

    type: Literal["relay"] = "relay"
    v: int = PROTOCOL_VERSION
    id: str
    speaker: Literal["host", "guest"]
    language: str
    encoding: Literal["pcm_s16le"] = "pcm_s16le"
    sample_rate: int = Field(ge=8_000, le=48_000)
    channels: int = Field(default=1, ge=1, le=2)
    bytes: int = Field(ge=2)
    duration_ms: int = Field(ge=0)


ServerEvent = Annotated[
    Union[
        ReadyEvent,
        TranscriptionEvent,
        TranslationEvent,
        ErrorEvent,
        PongEvent,
        AudioEvent,
        PeerEvent,
        RelayEvent,
    ],
    Field(discriminator="type"),
]

__all__ = [
    "PROTOCOL_VERSION",
    "SUPPORTED_CHANNELS",
    "SUPPORTED_ENCODING",
    "SUPPORTED_SAMPLE_RATE",
    "AudioEvent",
    "AudioFormat",
    "ErrorEvent",
    "FlushMessage",
    "HelloConfig",
    "HelloMessage",
    "PeerEvent",
    "PingMessage",
    "PongEvent",
    "ReadyEvent",
    "RelayEvent",
    "RoomInfo",
    "SttInfo",
    "TranscriptionEvent",
    "TranslationDecisionInfo",
    "TranslationEvent",
    "ValidationError",
    "parse_client_message",
]
