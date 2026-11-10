# ADR-0019: Zoom integration via an RTMS bridge into the existing pipeline

**Status:** Accepted (Phase 7, 2026-11-10)
**Context:** master prompt §17 Phase 7 — "Zoom integration (first meeting app)"

## Context

Phases 1–6 built a complete two-person browser translator: Silero VAD →
Whisper → decision engine → OPUS-MT → Piper, two-participant rooms with
listener-only voice routing, mobile-honest capture. Phase 7 must connect
that machine to Zoom meetings — the first meeting integration — without
compromising the product rules (AGENT.md §4: the translation engine stays
independent of meeting adapters; original pass-through audio is never
regenerated; audio routes to the correct recipient).

The sandbox has **no Zoom account, no Marketplace app, and no meeting to
join**. Whatever is built must be honest about that boundary (AGENT.md §6).

## Options considered

1. **A meeting BOT (headless client joining the meeting).** Rejected for
   v1: requires the Meeting SDK, a bot participant in the participant
   list, and media routing the sandbox can never exercise; heaviest
   possible surface with zero verifiable parts here.
2. **Closed-caption REST sidecar** (host enables CC, we POST captions).
   Rejected: audio ingestion still unsolved (needs RTMS anyway), and the
   legacy CC API is host-token-bound and deprecated in favor of RTMS.
3. **Virtual-audio-device bridge** (app as a Zoom "microphone"). Rejected:
   needs OS audio drivers — impossible in a sandbox, unverifiable, and it
   abandons the server pipeline (the browser path already exists).

**Chosen: 4 — the RTMS bridge.** Zoom's Real-Time Media Streams (RTMS) is
their documented, WebSocket-based media plane for exactly this use case
(AI notetakers, live translation): webhooks announce `meeting.rtms_started`
→ the app connects a signaling socket → a media socket → receives the
meeting's mixed audio. No bot joins the meeting; the host adds the app and
starts the stream. The protocol is public (developers.zoom.us/docs/rtms —
data type definitions, meetings quickstart; Zoom's own zoom-plugin-codex
RTMS guide), retrieved 2026-11-10.

## Decision

### Shape: bridge = orchestrator, pipeline stays the product

`backend/app/zoom/` adds four small pieces and **zero changes** to the
STT/decision/translation/TTS path or the WebSocket protocol:

- `signing.py` — pure crypto: the RTMS join signature
  (`HMAC-SHA256(client_secret, "client_id,meeting_uuid,rtms_stream_id")`),
  the `endpoint.url_validation` answer, and `x-zm-signature` (v0)
  verification of every webhook delivery.
- `rtms.py` — `RtmsClient`: the documented two-phase WebSocket handshake
  (signaling `msg_type 1` with `media_type=1` → media socket → media
  handshake `msg_type 3` → `CLIENT_READY_ACK 7`), keep-alive answering
  (`12 → 13`, both sockets, ~60 s/65 s tolerance), session/stream state
  handling, and the graceful `STREAM_CLOSE_REQ (21)`. Audio is negotiated
  as **raw L16 PCM, 16 kHz, mono, 20 ms frames** — byte-for-byte the
  pipeline's native format, so no codec/resample dependency exists (the
  spec's G.711/G.722/Opus paths are simply not requested).
- `pipeline_client.py` — `PipelineClient`: the bridge feeds Zoom audio
  into the product by being an ordinary **protocol-v1 client of the app's
  own `/ws/audio`** (hello as the room `host` with `zoom_native_language`
  → `zoom_preferred_language`, binary PCM16 uploads, explicit flushes,
  heartbeats, reconnect-with-backoff that re-hellos with its room code —
  the Phase 5 rejoin path). Every verified behavior of phases 1–6 applies
  unchanged: VAD gating, segmentation, decisions, relay, feedback-guard
  semantics, room model.
- `bridge.py` + `router.py` — webhook routing (`POST /zoom/webhook`:
  validation challenge, v0 signature check, immediate 200 ack — Zoom
  retries slow webhooks and a duplicate RTMS connection kicks the first —
  and one `ZoomMeetingWorker` per meeting), the meeting-audio endpointer
  (`UtteranceSegmenter`: RMS pause detection + soft cap; the server VAD
  still gates/trims/splits every flushed buffer), bounded queues with
  drop-on-full (a stalled consumer must never block the RTMS keep-alive
  loop), and `GET /zoom/meetings` publishing each meeting's room invite.

### The human is the room's guest

The bridge owns a normal two-participant room. The translator user opens
`…/?room=CODE` (from `/zoom/meetings`) and gets the full Phase 5
experience for the MEETING: speaker-tagged live transcript, translations,
and the translated voice spoken on THEIR device — routed only to the
listener, exactly as in a two-browser session. Pass-through utterances
(third languages — e.g. a greeting that is genuinely Arabic) are relayed
as original audio, never regenerated.

### Why not send captions/voice back INTO Zoom in v1

Zoom's RTMS reference documents media INGEST (audio/video/share/
transcript/chat are received). Injecting synthesized voice into the
meeting would reach every participant (RTMS has no per-listener audio
addressing), which breaks the product's routing rule
("translated audio reaches only the listener") — in a meeting of N
people the ur→en voice would play to the Urdu speaker too. Captions-out
has the same one-stream-for-everyone property and the reference does not
pin a caption-send message. So v1 delivers the translation to the
translator user's own device (the product's actual contract), and
meeting-facing caption/audio output is recorded as future work
(KI-031) pending a real Zoom app to test against.

### Honest boundary (KI-029, KI-030)

No Zoom account exists in this sandbox. Everything above is verified
against a **fake of Zoom's side only** (`app/zoom/fake_rtms.py` +
`scripts/e2e_zoom_check.py`): real webhook receiver, real signature
validation, real bridge, real pipeline (real Whisper + real OPUS-MT +
real Piper), real guest room. Field names Zoom does not publish in full
(the media URL key inside the signaling response; the exact audio frame
encoding) are handled defensively (probed keys; base64-or-binary decode)
and listed in KNOWN_ISSUES as the real-Zoom verification points. The
human checklist for the real deployment is written in
`docs/deployment.md §6`.

## Consequences

- The app's WebSocket protocol is untouched; browsers and the bridge are
  both just clients (one code path to test).
- The integration is OFF unless `zoom_enabled` + credentials are set; a
  disabled integration mounts no `/zoom/*` routes and `/health` reports
  `zoom.enabled=false`.
- Secrets (`zoom_client_secret`, `zoom_secret_token`) live only in
  backend env vars; logs mask meeting UUIDs.
- New knobs: `zoom_native_language` / `zoom_preferred_language` (the
  meeting rooms' pair), `zoom_ws_url` (self-connection target),
  endpointing/timeout tuning.
- Version 0.7.0.
