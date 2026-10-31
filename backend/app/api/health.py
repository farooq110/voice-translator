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
    if stt is not None:
        payload["stt"] = stt.describe()
    return payload
