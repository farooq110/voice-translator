"""Audio WebSocket protocol — version 1.

Versioned contract between the browser frontend and the backend. The full
specification lives in `docs/audio-protocol.md`; any breaking change must
introduce a new protocol version and a matching doc update.

Framing rules (v1):
- Control messages: JSON text frames, discriminated by `type`, carrying `v=1`.
- Audio: raw binary frames, PCM16 little-endian, mono, 16 kHz.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

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


class HelloConfig(StrictModel):
    """Translator user's language configuration (only they configure anything)."""

    native_language: str = Field(pattern=r"^[a-z]{2,3}(-[A-Za-z]{2,8})?$")
    preferred_language: str = Field(pattern=r"^[a-z]{2,3}(-[A-Za-z]{2,8})?$")
    audio: AudioFormat


class HelloMessage(StrictModel):
    type: Literal["hello"] = "hello"
    v: Literal[PROTOCOL_VERSION]
    config: HelloConfig


class PingMessage(StrictModel):
    type: Literal["ping"] = "ping"
    v: Literal[PROTOCOL_VERSION]
    ts: int | None = None


class FlushMessage(StrictModel):
    """Request transcription of all audio buffered since the last flush."""

    type: Literal["flush"] = "flush"
    v: Literal[PROTOCOL_VERSION]


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


class ReadyEvent(StrictModel):
    """Handshake acknowledgment; sent after a valid `hello`."""

    type: Literal["ready"] = "ready"
    v: int = PROTOCOL_VERSION
    session_id: str
    audio: AudioFormat
    stt: SttInfo


class TranscriptionEvent(StrictModel):
    type: Literal["transcription"] = "transcription"
    v: int = PROTOCOL_VERSION
    id: str
    text: str
    language: str
    language_probability: float = Field(ge=0.0, le=1.0)
    audio_duration_s: float = Field(ge=0.0)
    processing_ms: int = Field(ge=0)
    is_final: bool = True
    low_confidence: bool = False


class ErrorEvent(StrictModel):
    type: Literal["error"] = "error"
    v: int = PROTOCOL_VERSION
    code: str
    message: str
    fatal: bool = False


class PongEvent(StrictModel):
    type: Literal["pong"] = "pong"
    v: int = PROTOCOL_VERSION
    ts: int


ServerEvent = Annotated[
    Union[ReadyEvent, TranscriptionEvent, ErrorEvent, PongEvent],
    Field(discriminator="type"),
]

__all__ = [
    "PROTOCOL_VERSION",
    "SUPPORTED_CHANNELS",
    "SUPPORTED_ENCODING",
    "SUPPORTED_SAMPLE_RATE",
    "AudioFormat",
    "ErrorEvent",
    "FlushMessage",
    "HelloConfig",
    "HelloMessage",
    "PingMessage",
    "PongEvent",
    "ReadyEvent",
    "SttInfo",
    "TranscriptionEvent",
    "ValidationError",
    "parse_client_message",
]
