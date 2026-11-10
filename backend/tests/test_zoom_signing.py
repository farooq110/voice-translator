"""Zoom signing unit tests — the crypto contract of the bridge (Phase 7).

The message formats here ARE the contract with Zoom (RTMS join signature and
the webhook v0 signature), so the tests pin them explicitly.
"""

from __future__ import annotations

import hashlib
import hmac

from app.zoom.signing import (
    rtms_join_signature,
    webhook_signature_valid,
    webhook_validation_response,
)


def _hmac_hex(key: str, message: str) -> str:
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def test_rtms_join_signature_message_format():
    # Format contract: HMAC-SHA256(client_secret, "client_id,meeting_uuid,stream_id"), hex.
    expected = _hmac_hex("shhh", "cid,mtg-uuid-123,stream-9")
    assert rtms_join_signature("cid", "mtg-uuid-123", "stream-9", "shhh") == expected
    assert len(rtms_join_signature("cid", "u", "s", "k")) == 64  # sha256 hex


def test_rtms_join_signature_changes_with_every_input():
    base = rtms_join_signature("cid", "u", "s", "k")
    assert base != rtms_join_signature("cid2", "u", "s", "k")
    assert base != rtms_join_signature("cid", "u2", "s", "k")
    assert base != rtms_join_signature("cid", "u", "s2", "k")
    assert base != rtms_join_signature("cid", "u", "s", "k2")


def test_webhook_validation_response_shape():
    resp = webhook_validation_response("plain-token-abc", "webhook-secret")
    assert resp["plainToken"] == "plain-token-abc"
    assert resp["encryptedToken"] == _hmac_hex("webhook-secret", "plain-token-abc")


def test_webhook_signature_valid_accepts_exact_match():
    url = "https://example.org/zoom/webhook"
    body = b'{"event":"meeting.rtms_started"}'
    header = "v0=" + _hmac_hex("st", f"v0:{url}:{body.decode()}")
    assert webhook_signature_valid("st", header, url, body) is True


def test_webhook_signature_valid_rejects_tampering():
    url = "https://example.org/zoom/webhook"
    body = b'{"event":"meeting.rtms_started"}'
    header = "v0=" + _hmac_hex("st", f"v0:{url}:{body.decode()}")
    assert webhook_signature_valid("st", header, url, b'{"event":"tampered"}') is False
    assert webhook_signature_valid("st", header, "https://evil.org/zoom/webhook", body) is False
    assert webhook_signature_valid("other-secret", header, url, body) is False


def test_webhook_signature_valid_rejects_malformed_headers():
    body = b"{}"
    assert webhook_signature_valid("st", None, "https://x/zoom/webhook", body) is False
    assert webhook_signature_valid("st", "", "https://x/zoom/webhook", body) is False
    assert webhook_signature_valid("st", "garbage", "https://x/zoom/webhook", body) is False
    assert webhook_signature_valid("st", "v1=abcdef", "https://x/zoom/webhook", body) is False
    # Empty secret configured → verification disabled, must fail closed.
    assert webhook_signature_valid("", "v0=abc", "https://x/zoom/webhook", body) is False
