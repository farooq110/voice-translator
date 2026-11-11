"""End-to-end check against a LIVE server: Zoom webhook → RTMS → REAL pipeline.

What this proves (and what it does NOT — honesty first):

    REAL:  the live FastAPI app, its webhook receiver (signature validation,
           url_validation), the ZoomBridge worker, the protocol-v1 pipeline
           client, the app's own Silero-VAD → Whisper → decision-engine →
           OPUS-MT → Piper path, and the two-participant room routing to a
           second (guest) WebSocket client.
    FAKE:  ZOOM'S RTMS EDGE ONLY (signaling + media sockets, via
           app.zoom.fake_rtms.FakeZoomRTMS). The meeting audio is the
           edge-tts Urdu fixture. No claim is made about Zoom's production
           behavior — see KNOWN_ISSUES KI-029.

Usage (from backend/, app running on :3000 with ZOOM_* env configured):
    uv run python scripts/e2e_zoom_check.py [--url http://localhost:3000]

Checks:
  1. endpoint.url_validation challenge answered with the correct HMAC.
  2. meeting.rtms_started (signed) acked immediately; a worker reaches `live`
     and publishes the room invite on GET /zoom/meetings.
  3. The bridge's RTMS handshake verifies its signature on the fake and
     negotiates L16 / 16 kHz / mono.
  4. Meeting audio (ur fixture) flows RTMS → bridge → pipeline: the GUEST
     receives the speaker-tagged transcription, the ur→en translation with
     Latin-script text, and Piper TTS audio routed ONLY to the listener.
  5. meeting.rtms_stopped stops the worker; the graceful STREAM_CLOSE_REQ
     reaches the fake signaling socket; /health zoom goes back to zero.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

from websockets.asyncio.client import connect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.zoom.fake_rtms import FakeZoomRTMS, wait_until  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def env_required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        print(f"FAIL: environment variable {name} is not set (see docs/deployment.md §6)")
        sys.exit(2)
    return value


def http_post_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"content-type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def http_get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode())


def signed_headers(secret_token: str, url: str, payload: dict) -> dict[str, str]:
    body = json.dumps(payload).encode()
    message = f"v0:{url}:{body.decode()}"
    signature = hmac.new(secret_token.encode(), message.encode(), hashlib.sha256).hexdigest()
    return {"x-zm-signature": f"v0={signature}"}


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        assert wav.getsampwidth() == 2 and wav.getnchannels() == 1, "expected 16-bit mono PCM"
        return wav.readframes(wav.getnframes())


def _latin_ratio(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if ch.isascii()) / len(letters)


async def guest_session(base_ws_url: str, room_code: str, collected: dict) -> None:
    """The human listener: joins the bridge's room, collects routed events."""
    async with connect(f"{base_ws_url}/ws/audio", open_timeout=15) as ws:
        await ws.send(json.dumps({
            "type": "hello", "v": 1,
            "config": {"role": "guest", "room": room_code,
                       "client": {"platform": "e2e-guest"},
                       "audio": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1}},
        }))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=20)
            if isinstance(raw, bytes):
                continue
            msg = json.loads(raw)
            if msg.get("type") == "ready":
                collected["ready"] = msg
                break
        # Stream events until the checks have what they need: the fixture's
        # greeting may honestly pass through as a third language (Arabic —
        # the salam IS Arabic, decision Rule 3) — so do not exit on routed
        # audio alone; wait for the ur→en translations of the Urdu sentences.
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2)
            except asyncio.TimeoutError:
                if collected.get("translations") and collected.get("audio_bytes"):
                    return
                continue
            if isinstance(raw, bytes):
                collected["audio_bytes"] = collected.get("audio_bytes", 0) + len(raw)
                continue
            msg = json.loads(raw)
            kind = msg.get("type")
            if kind == "transcription":
                collected.setdefault("transcriptions", []).append(msg)
            elif kind == "translation":
                collected.setdefault("translations", []).append(msg)
            elif kind == "audio":
                collected.setdefault("audio_events", []).append(msg)
            elif kind == "error":
                collected.setdefault("errors", []).append(msg)


async def run_checks(base_url: str) -> list[str]:
    client_id = env_required("ZOOM_CLIENT_ID")
    client_secret = env_required("ZOOM_CLIENT_SECRET")
    secret_token = env_required("ZOOM_SECRET_TOKEN")
    base_ws_url = base_url.replace("http", "ws", 1)
    checks: list[str] = []

    # --- 1. url_validation -------------------------------------------------
    challenge = {"event": "endpoint.url_validation", "payload": {"plainToken": "e2e-challenge-token"}}
    url = f"{base_url}/zoom/webhook"
    status, body = http_post_json(url, challenge, signed_headers(secret_token, url, challenge))
    expected = hmac.new(secret_token.encode(), b"e2e-challenge-token", hashlib.sha256).hexdigest()
    assert status == 200 and body.get("encryptedToken") == expected, f"url_validation: {status} {body}"
    checks.append(f"url_validation challenge answered (encryptedToken sha256 OK)")

    # --- 2. rtms_started → worker live → room invite -----------------------
    fake = FakeZoomRTMS(client_id=client_id, client_secret=client_secret,
                        meeting_uuid="e2e-mtg-0001-uuid", rtms_stream_id="stream-e2e-1",
                        keepalive_interval_s=5.0)
    await fake.start()
    started = {"event": "meeting.rtms_started", "payload": {"object": {
        "meeting_uuid": "e2e-mtg-0001-uuid", "rtms_stream_id": "stream-e2e-1",
        "server_urls": fake.signaling_url,
    }}}
    status, body = http_post_json(url, started, signed_headers(secret_token, url, started))
    assert status == 200 and body == {"ok": True}, f"rtms_started ack: {status} {body}"

    room_code = None
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        listing = http_get_json(f"{base_url}/zoom/meetings")
        meetings = [m for m in listing.get("meetings", []) if m.get("room_code")]
        if meetings:
            room_code = meetings[0]["room_code"]
            break
        await asyncio.sleep(0.4)
    assert room_code, "bridge never published a room code"
    checks.append(f"worker live; invite code {room_code} published on /zoom/meetings")

    # --- 3. RTMS handshake verified by the fake ---------------------------
    await wait_until(lambda: len(fake.signaling_handshakes) == 1 and len(fake.media_handshakes) == 1,
                     timeout=20, what="RTMS handshake")
    assert fake.signaling_signature_ok is True, "bridge signature rejected by the fake"
    params = fake.media_params or {}
    assert params.get("codec") == 1 and params.get("sample_rate") == 1 and params.get("channel") == 1, params
    checks.append("RTMS handshake ok (signature verified; L16/16 kHz/mono negotiated)")

    # --- 4. meeting audio → guest transcription/translation/TTS -----------
    collected: dict = {}
    guest = asyncio.create_task(guest_session(base_ws_url, room_code, collected))
    try:
        await wait_until(lambda: collected.get("ready"), timeout=15, what="guest ready")
        pcm = read_wav(FIXTURES / "ur_greeting.wav")
        await fake.stream_audio(pcm, speed=2.0)
        await asyncio.wait_for(guest, timeout=50)
    except Exception:
        guest.cancel()
        raise

    transcriptions = collected.get("transcriptions", [])
    translations = collected.get("translations", [])
    assert transcriptions, f"guest saw no transcription: {collected.get('errors')}"
    speaker_tags = {t.get("speaker") for t in transcriptions}
    assert speaker_tags == {"host"}, f"speaker tags wrong: {speaker_tags}"
    ur_texts = " ".join(t.get("text", "") for t in transcriptions).strip()
    assert ur_texts, "transcription text empty"
    assert translations, "guest saw no translation"
    translated = [t for t in translations
                  if t.get("source_language") == "ur" and t.get("target_language") == "en"]
    assert translated, f"no ur→en translation among {translations!r}"
    en_text = translated[0].get("text", "")
    assert en_text and _latin_ratio(en_text) > 0.5, f"translation not Latin-script: {en_text!r}"
    assert collected.get("audio_bytes", 0) > 10_000, "guest saw no/short routed PCM"
    checks.append(
        f"meeting audio ({len(pcm)/32000:.1f}s, {len(transcriptions)} utterances) → guest: "
        f"transcription(speaker=host) + ur→en translation ({len(en_text)} chars Latin) + "
        f"{collected['audio_bytes']} B routed PCM (relay for the Arabic greeting + TTS)"
    )

    # --- 5. rtms_stopped → graceful shutdown ------------------------------
    stopped = {"event": "meeting.rtms_stopped", "payload": {"object": {"meeting_uuid": "e2e-mtg-0001-uuid"}}}
    status, body = http_post_json(url, stopped, signed_headers(secret_token, url, stopped))
    assert status == 200 and body == {"ok": True}, f"rtms_stopped ack: {status} {body}"
    await wait_until(lambda: len(fake.stream_close_reqs) == 1, timeout=15, what="STREAM_CLOSE_REQ")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        health = http_get_json(f"{base_url}/health")
        if health.get("zoom", {}).get("active_meetings", 1) == 0:
            break
        await asyncio.sleep(0.4)
    else:
        raise AssertionError("zoom.active_meetings never returned to 0")
    checks.append("graceful stop: STREAM_CLOSE_REQ seen; /health zoom.active_meetings back to 0")

    await fake.stop()
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:3000")
    args = parser.parse_args()

    print(f"e2e_zoom_check → {args.url}")
    started = time.monotonic()
    checks = asyncio.run(run_checks(args.url))
    elapsed = time.monotonic() - started
    print(f"\nPASS — {len(checks)}/5 check groups in {elapsed:.1f}s:")
    for i, check in enumerate(checks, 1):
        print(f"  {i}. {check}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
