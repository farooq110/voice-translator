# CHANGELOG

All meaningful project changes. Format based on Keep a Changelog.

## [0.7.0] — Phase 7: Zoom integration — the RTMS bridge (2026-11-10)

First meeting integration (master prompt §17): the translator user adds
the app to a Zoom meeting, and the meeting's audio flows through the
SAME verified pipeline (Silero VAD → Whisper → decision engine →
OPUS-MT → Piper) into a normal two-participant room — the human joins
via the invite published at `/zoom/meetings` and hears the translated
voice on their own device. No WebSocket protocol changes; the frontend
is untouched.

### Added
- **`backend/app/zoom/`** (ADR-0019): `signing.py` (RTMS join signature,
  webhook url_validation answer, `x-zm-signature` v0 verification);
  `rtms.py` (documented two-phase WebSocket client — signaling
  handshake with `media_type=1`, media handshake negotiating raw
  L16/16 kHz/mono/20 ms — byte-for-byte the pipeline's native format,
  so no codec dependency; keep-alive 12→13 on both sockets;
  CLIENT_READY_ACK; stream/session state handling; graceful
  STREAM_CLOSE_REQ); `pipeline_client.py` (reconnecting protocol-v1
  client of the app's own `/ws/audio` — hello as room host with the
  configured pair, binary PCM uploads, segment+flush queue entries,
  heartbeats, drop-on-full backpressure); `bridge.py` (webhook routing,
  one `ZoomMeetingWorker` per meeting, duplicate-start dedupe, meeting
  audio endpointer — RMS pause + soft cap, server VAD still
  gates/trims/splits; stop matching accepts uuid-only payloads);
  `router.py` (`POST /zoom/webhook` — immediate 200 ack before the slow
  work; `GET /zoom/meetings` — live meetings + room invites).
- **Config:** `zoom_enabled` + `zoom_client_id`/`zoom_client_secret`/
  `zoom_secret_token` (secrets only in env), `zoom_native_language`/
  `zoom_preferred_language` (meeting room pair), `zoom_ws_url`,
  endpointing/timeout knobs. `/health` gained a `zoom` section
  (enabled/active_meetings/total_meetings/pair/bytes_ingested).
- **Tests:** 30 new backend tests — signing contract vectors, webhook
  signature enforcement, segmenter pause/cap/min-segment behavior, RTMS
  client protocol (rejection, session-stopped, defensive audio decode,
  keep-alives on both sockets), bridge integration (worker + pipeline +
  fake RTMS end-to-end in one process), webhook HTTP surface (disabled
  404, url_validation, 401, immediate ack, meetings listing, health).
- **Fake Zoom edge:** `app/zoom/fake_rtms.py` — a test double of Zoom's
  RTMS signaling/media sockets (signature-verifying, keep-alive-checking,
  audio-streaming) reused by tests AND the live E2E.
- **Live E2E:** `backend/scripts/e2e_zoom_check.py` — signed webhooks →
  fake RTMS → real pipeline → guest client; **5/5 PASS live (17.9 s)**:
  url_validation, worker live + invite published, RTMS handshake
  (signature verified, L16/16 kHz/mono), 6.6 s ur fixture → 3 utterances
  → guest transcription(speaker=host) + ur→en translation (real OPUS-MT)
  + 111,618 B routed PCM (Arabic-greeting relay + TTS), graceful stop
  with STREAM_CLOSE_REQ and /health back to zero.

### Verified (2026-11-10)
- Backend **220 passed + 1 xfailed** (+30), frontend untouched
  **139/139**, tsc/ESLint/build clean (264.01 kB).
- Full live battery on :3000: e2e translation **3/3**, two-participant
  **4/4**, TTS **3/3**, silence guard **PASS**, smokes phase2 **10/10** /
  phase5 **12/12** / phase6 **9/9**.
- Honest boundary: Zoom's production edge is NOT verified (no Zoom
  account in the sandbox) — KI-029; endpointing coarseness KI-030;
  no meeting-facing caption/audio output in v1 — KI-031.

