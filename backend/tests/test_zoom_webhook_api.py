"""Zoom webhook HTTP surface tests (Phase 7).

Disabled → 404 (route never mounted) and /health reports zoom.enabled=false.
Enabled → url_validation challenge, signature enforcement, immediate ack for
rtms events, and the meetings listing. The bridge itself is tested in
test_zoom_bridge.py.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import FakeSTT, PassThroughVAD


def _sig(secret: str, url: str, body: bytes) -> str:
    message = f"v0:{url}:{body.decode('utf-8', errors='replace')}"
    return "v0=" + hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _zoom_settings(**overrides: object) -> Settings:
    # MT/TTS are irrelevant to the webhook surface — disable them so tests
    # never load the real CT2/Piper models (warmup threads would otherwise
    # pile up across tests and starve the suite).
    base: dict[str, object] = {
        "translation_provider": "none",
        "tts_provider": "none",
        "zoom_enabled": True,
        "zoom_client_id": "test-client",
        "zoom_client_secret": "test-secret",
        "zoom_secret_token": "test-webhook-secret",
        "zoom_ws_url": "ws://127.0.0.1:1/ws/audio",  # unreachable in tests
        "zoom_worker_start_timeout_s": 0.3,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _client(settings: Settings, stt: FakeSTT | None = None) -> TestClient:
    app = create_app(
        stt=stt or FakeSTT(language="ur", detect_language="ur"),
        vad=PassThroughVAD(),
        settings=settings,
    )
    return TestClient(app)


def _signed_post(client: TestClient, path: str, payload: dict, secret: str = "test-webhook-secret"):
    body = json.dumps(payload).encode()
    url = f"http://testserver{path}"
    return client.post(
        path,
        content=body,
        headers={"content-type": "application/json", "x-zm-signature": _sig(secret, url, body)},
    )


# ------------------------------------------------------------------ disabled


def test_disabled_zoom_answers_404_and_health_reports_disabled():
    with _client(Settings(translation_provider="none", tts_provider="none")) as client:
        resp = client.post("/zoom/webhook", json={"event": "endpoint.url_validation",
                                                  "payload": {"plainToken": "x"}})
        # The route is never mounted; with the built SPA mounted at "/" the
        # catch-all answers 405 (Method Not Allowed) for this POST, and a
        # bare API-only deployment answers 404 — both mean "no zoom API".
        assert resp.status_code in (404, 405)
        health = client.get("/health").json()
        assert health["zoom"] == {"enabled": False}


def test_enabled_without_credentials_does_not_mount():
    settings = _zoom_settings(zoom_client_id="", zoom_client_secret="")
    with _client(settings) as client:
        # Route not mounted; the SPA catch-all (when dist is built) answers
        # 405 for the POST, a bare API deployment answers 404.
        assert client.post("/zoom/webhook", json={}).status_code in (404, 405)
        assert client.get("/health").json()["zoom"] == {"enabled": False}


# ------------------------------------------------------------------ enabled


def test_url_validation_challenge_answered():
    with _client(_zoom_settings()) as client:
        resp = _signed_post(client, "/zoom/webhook", {
            "event": "endpoint.url_validation",
            "payload": {"plainToken": "challenge-token"},
        })
        assert resp.status_code == 200
        expected = hmac.new(
            b"test-webhook-secret", b"challenge-token", hashlib.sha256
        ).hexdigest()
        assert resp.json() == {"plainToken": "challenge-token", "encryptedToken": expected}


def test_webhook_without_or_bad_signature_is_rejected():
    with _client(_zoom_settings()) as client:
        payload = {"event": "meeting.rtms_started", "payload": {"object": {}}}
        # Missing header
        assert client.post("/zoom/webhook", json=payload).status_code == 401
        # Wrong secret
        resp = _signed_post(client, "/zoom/webhook", payload, secret="wrong")
        assert resp.status_code == 401


def test_rtms_started_is_acked_immediately():
    with _client(_zoom_settings()) as client:
        resp = _signed_post(client, "/zoom/webhook", {
            "event": "meeting.rtms_started",
            "payload": {"object": {
                "meeting_uuid": "mtg-api-1", "rtms_stream_id": "s",
                "server_urls": "ws://127.0.0.1:1/sig",
            }},
        })
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


def test_unknown_events_are_ignored_with_ack():
    with _client(_zoom_settings()) as client:
        resp = _signed_post(client, "/zoom/webhook", {
            "event": "meeting.participant_joined", "payload": {},
        })
        assert resp.status_code == 200
        assert resp.json()["ignored"] == "meeting.participant_joined"


def test_meetings_listing_shows_bridge_rooms():
    with _client(_zoom_settings()) as client:
        resp = _signed_post(client, "/zoom/webhook", {
            "event": "meeting.rtms_started",
            "payload": {"object": {
                "meeting_uuid": "mtg-list-1", "rtms_stream_id": "s",
                "server_urls": "ws://127.0.0.1:1/sig",
            }},
        })
        assert resp.status_code == 200
        listing = client.get("/zoom/meetings").json()
        assert listing["enabled"] is True
        assert len(listing["meetings"]) == 1
        meeting = listing["meetings"][0]
        assert meeting["meeting_uuid"].startswith("mtg-list")  # masked
        assert meeting["state"] in ("starting", "live", "error")


def test_health_reports_enabled_bridge():
    with _client(_zoom_settings()) as client:
        health = client.get("/health").json()
        assert health["zoom"]["enabled"] is True
        assert health["zoom"]["active_meetings"] == 0
        assert health["zoom"]["pair"] == "ur→en"
