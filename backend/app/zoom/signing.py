"""Zoom credential handling — signing and webhook verification (Phase 7).

Pure functions only: every secret stays in backend env vars (AGENT.md §5),
nothing here performs I/O, and every answer is a plain string/bool so the
webhook router and the RTMS client can unit-test the logic without a server.

Two distinct secret materials exist in a Zoom General App (do not confuse):

- **Client secret** (`zoom_client_secret`) — the OAuth client secret. It signs
  the RTMS *join* signature: ``HMAC-SHA256(client_secret,
  "client_id,meeting_uuid,rtms_stream_id")`` (hex). The same signature is
  required on BOTH the signaling handshake and the media-data handshake.
- **Secret token** (`zoom_secret_token`) — the webhook validation token. It
  answers Zoom's ``endpoint.url_validation`` challenge and verifies the
  ``x-zm-signature`` header Zoom sends with every webhook delivery:
  ``v0=`` + ``HMAC-SHA256(secret_token, "v0:" + full_url + ":" + raw_body)``.

Reference: Zoom RTMS docs (developers.zoom.us/docs/rtms) — data type
definitions and the meetings quickstart — as retrieved 2026-11-10.
"""

from __future__ import annotations

import hashlib
import hmac

__all__ = [
    "rtms_join_signature",
    "webhook_validation_response",
    "webhook_signature_valid",
]


def _hmac_hex(key: str, message: str) -> str:
    """Lowercase hex HMAC-SHA256 of `message` under `key` (UTF-8 both)."""
    return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def rtms_join_signature(client_id: str, meeting_uuid: str, rtms_stream_id: str, client_secret: str) -> str:
    """The RTMS handshake signature (client secret as the HMAC key)."""
    return _hmac_hex(client_secret, f"{client_id},{meeting_uuid},{rtms_stream_id}")


def webhook_validation_response(plain_token: str, secret_token: str) -> dict[str, str]:
    """Body Zoom requires for an ``endpoint.url_validation`` webhook.

    Zoom posts ``{"event": "endpoint.url_validation", "payload":
    {"plainToken": "…"}}`` and expects (within seconds) the exact JSON
    ``{"plainToken": <echo>, "encryptedToken": <hex>}`` — else the webhook
    endpoint is not activated.
    """
    return {
        "plainToken": plain_token,
        "encryptedToken": _hmac_hex(secret_token, plain_token),
    }


def webhook_signature_valid(
    secret_token: str,
    signature_header: str | None,
    full_url: str,
    raw_body: bytes,
) -> bool:
    """Verify the ``x-zm-signature`` header of a webhook delivery.

    Expected header shape: ``v0=<hex>`` where the HMAC message is
    ``"v0:" + <the full request URL Zoom called> + ":" + <raw body>``.
    Comparison is constant-time. Missing/malformed headers are invalid.
    """
    if not secret_token or not signature_header:
        return False
    message = f"v0:{full_url}:{raw_body.decode('utf-8', errors='replace')}"
    expected = "v0=" + _hmac_hex(secret_token, message)
    return hmac.compare_digest(expected, signature_header.strip())
