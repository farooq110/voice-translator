"""HTTP surface for the Zoom bridge (Phase 7, ADR-0019).

Two routes, mounted ONLY when the bridge is enabled and credentialed
(see `create_app`) — a disabled integration answers 404 instead of silently
accepting events:

- ``POST /zoom/webhook`` — Zoom's webhook receiver:
    * ``endpoint.url_validation`` → the exact JSON challenge answer;
    * ``meeting.rtms_started`` / ``meeting.rtms_stopped`` → acknowledged 200
      IMMEDIATELY (Zoom's documented duplicate-connection gotcha: a slow
      webhook response makes Zoom retry, and a second RTMS connection kicks
      the first). `handle_event` only SPAWNS the worker's tasks — the RTMS
      handshake happens on its own task, never inside this request — so the
      synchronous call stays sub-millisecond and needs no background task.
    * every other event → acknowledged and ignored.
  When `zoom_secret_token` is configured, deliveries failing the
  ``x-zm-signature`` check are rejected with 401 before any parsing.
- ``GET /zoom/meetings`` — the bridge's active meetings with their room
  invite codes, so the translator user can open `…/?room=CODE` and hear the
  live translated meeting (the Phase 5 room UX, unchanged).
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request

from ..config import Settings
from .bridge import ZoomBridge
from .signing import webhook_signature_valid, webhook_validation_response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/zoom")

_START_EVENTS = ("meeting.rtms_started", "meeting.rtms_stopped")


def _effective_url(request: Request, settings: Settings) -> str:
    """The URL Zoom believes it called — used for signature verification.

    A reverse proxy may rewrite scheme/host; `zoom_webhook_url_override`
    lets a deployment pin the public URL so the v0 signature still matches.
    """
    override = settings.zoom_webhook_url_override.strip()
    return override or str(request.url)


@router.post("/webhook")
async def webhook(request: Request) -> dict[str, object]:
    state = request.app.state
    settings: Settings = state.settings
    bridge: ZoomBridge | None = getattr(state, "zoom_bridge", None)

    raw = await request.body()
    if settings.zoom_secret_token:
        if not webhook_signature_valid(
            settings.zoom_secret_token,
            request.headers.get("x-zm-signature"),
            _effective_url(request, settings),
            raw,
        ):
            logger.warning("zoom.webhook.signature_rejected url=%s", _effective_url(request, settings))
            raise HTTPException(status_code=401, detail="invalid webhook signature")

    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="webhook body is not JSON") from None
    if not isinstance(doc, dict):
        raise HTTPException(status_code=400, detail="webhook body is not a JSON object")
    event = doc.get("event")
    payload = doc.get("payload")

    if event == "endpoint.url_validation":
        if not isinstance(payload, dict) or not isinstance(payload.get("plainToken"), str):
            raise HTTPException(status_code=400, detail="url_validation missing plainToken")
        return webhook_validation_response(payload["plainToken"], settings.zoom_secret_token)

    if bridge is None:
        return {"detail": "zoom bridge disabled"}

    if event in _START_EVENTS:
        # Ack BEFORE the slow work: handle_event only spawns the worker's
        # asyncio tasks (RTMS handshake + pipeline connection run there), so
        # the response is effectively immediate without background-task
        # plumbing (whose execution context varies across ASGI runners).
        bridge.handle_event(event, payload)
        return {"ok": True}

    logger.debug("zoom.webhook.unhandled event=%s", event)
    return {"ok": True, "ignored": event}


@router.get("/meetings")
async def meetings(request: Request) -> dict[str, object]:
    bridge: ZoomBridge | None = getattr(request.app.state, "zoom_bridge", None)
    if bridge is None:
        return {"meetings": [], "enabled": False}
    return {"meetings": bridge.meetings(), "enabled": True}
