"""Health/readiness endpoint."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request

from ..models.protocol import (
    PROTOCOL_VERSION,
    SUPPORTED_CHANNELS,
    SUPPORTED_ENCODING,
    SUPPORTED_SAMPLE_RATE,
)

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    state = request.app.state
    stt = getattr(state, "stt", None)
    sessions = getattr(state, "sessions", None)
    started_at = getattr(state, "started_at", None)

    payload: dict[str, object] = {
        "status": "ok",
        "app": "ai-voice-translator",
        "version": getattr(state, "version", "unknown"),
        "protocol": PROTOCOL_VERSION,
        "audio": {
            "encoding": SUPPORTED_ENCODING,
            "sample_rate": SUPPORTED_SAMPLE_RATE,
            "channels": SUPPORTED_CHANNELS,
        },
    }
    if started_at is not None:
        payload["uptime_s"] = round(time.time() - started_at, 1)
    if sessions is not None:
        payload["sessions"] = sessions.stats()
    rooms = getattr(state, "rooms", None)
    if rooms is not None:
        payload["rooms"] = rooms.stats()
    if stt is not None:
        payload["stt"] = stt.describe()
    vad = getattr(state, "vad", None)
    if vad is not None:
        payload["vad"] = vad.describe()
    translator = getattr(state, "translator", None)
    if translator is not None:
        payload["translation"] = translator.describe()
    tts = getattr(state, "tts", None)
    if tts is not None:
        tts_info = tts.describe()
        # QA round (BUG-001): make a voiceless deployment LOUD — with TTS
        # enabled but zero voices loaded, translated audio can never reach a
        # browser (every utterance answers tts_unsupported_language).
        if tts_info.get("enabled") and not tts_info.get("languages"):
            tts_info["warning"] = (
                "TTS enabled but no voice models loaded — translated audio stays "
                "text-only. Provision with backend/scripts/download_tts_voices.py "
                "and restart."
            )
        payload["tts"] = tts_info
    stt_queue = getattr(state, "stt_queue", None)
    if stt_queue is not None:
        payload["stt_queue"] = stt_queue.stats()
    return payload
