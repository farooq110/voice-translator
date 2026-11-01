"""Protocol v1 model validation (pydantic contracts)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.protocol import (
    PROTOCOL_VERSION,
    ErrorEvent,
    TranscriptionEvent,
    parse_client_message,
)

VALID_HELLO = {
    "v": 1,
    "type": "hello",
    "config": {
        "native_language": "ur",
        "preferred_language": "en",
        "audio": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1},
    },
}


def test_hello_parses() -> None:
    msg = parse_client_message(VALID_HELLO)
    assert msg.type == "hello"
    assert msg.config.native_language == "ur"
    assert msg.config.preferred_language == "en"
    assert msg.config.audio.sample_rate == 16000


def test_hello_wrong_version_rejected() -> None:
    bad = {**VALID_HELLO, "v": 2}
    with pytest.raises(ValidationError):
        parse_client_message(bad)


def test_hello_unknown_type_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_client_message({"v": 1, "type": "explode"})


def test_hello_extra_fields_rejected() -> None:
    bad = {**VALID_HELLO, "sneaky": True}
    with pytest.raises(ValidationError):
        parse_client_message(bad)


@pytest.mark.parametrize("field", ["native_language", "preferred_language"])
def test_hello_language_codes_validated(field: str) -> None:
    import copy

    bad = copy.deepcopy(VALID_HELLO)
    bad["config"][field] = "URDU"  # uppercase not allowed
    with pytest.raises(ValidationError):
        parse_client_message(bad)


def test_bad_audio_format_rejected() -> None:
    bad = {
        "v": 1,
        "type": "hello",
        "config": {
            "native_language": "ur",
            "preferred_language": "en",
            "audio": {"encoding": "mp3", "sample_rate": 16000, "channels": 1},
        },
    }
    with pytest.raises(ValidationError):
        parse_client_message(bad)


def test_ping_and_flush_parse() -> None:
    assert parse_client_message({"v": 1, "type": "ping", "ts": 5}).ts == 5
    assert parse_client_message({"v": 1, "type": "ping"}).ts is None
    assert parse_client_message({"v": 1, "type": "flush"}).type == "flush"


def test_flush_utterance_id_optional_and_validated() -> None:
    """BUG-001: optional client-generated correlation id on flush."""
    assert parse_client_message({"v": 1, "type": "flush"}).utterance_id is None
    msg = parse_client_message(
        {"v": 1, "type": "flush", "utterance_id": "c_0001_ab12cd"}
    )
    assert msg.utterance_id == "c_0001_ab12cd"
    # charset/length are enforced — free-form strings stay out
    with pytest.raises(ValidationError):
        parse_client_message({"v": 1, "type": "flush", "utterance_id": "bad id with spaces!"})
    with pytest.raises(ValidationError):
        parse_client_message({"v": 1, "type": "flush", "utterance_id": "x" * 65})


def test_protocol_version_constant() -> None:
    assert PROTOCOL_VERSION == 1


def test_event_serialization_shapes() -> None:
    err = ErrorEvent(code="x", message="y")
    assert err.model_dump() == {
        "type": "error", "v": 1, "code": "x", "message": "y",
        "fatal": False, "utterance_id": None,
    }
    attributed = ErrorEvent(code="x", message="y", utterance_id="c_0001_ab12cd")
    assert attributed.utterance_id == "c_0001_ab12cd"

    tr = TranscriptionEvent(id="u_1", text="hi", language="en", language_probability=0.9,
                            audio_duration_s=1.0, processing_ms=5)
    assert tr.is_final is True and tr.low_confidence is False
