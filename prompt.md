# prompt.md — Phase 5 Scope & Delivery Record

**Phase:** 5 of 10 — Complete Browser MVP (two participants)
**Status:** implemented — full verification round complete — **pending user approval** (§23 workflow)
**Version:** v0.5.0
**ADR:** [docs/decisions/0014-two-participant-rooms.md](docs/decisions/0014-two-participant-rooms.md)

> This repository documents the **current phase only** in this file. The
> complete master prompt (25 sections, 10-phase roadmap, approval workflow)
> is maintained by the product owner outside the repository. The overall
> delivery roadmap and phase statuses live in
> [README.md § Roadmap](README.md#roadmap-master-prompt-17); durable agent
> rules live in [AGENT.md](AGENT.md). Branch-local deep dives: `PHASE-3.md`
> (phase-3 branch), `PHASE-4.md` (phase-4 branch), `PHASE-5.md` (phase-5
> branch) — each documents only its own phase.

---

## 1. Phase 5 scope (master prompt §17)

Complete the pipeline:

**Microphone → VAD → STT → Language Detection → Translation Decision →
Translation (when required) → TTS (when required) → Audio Routing →
Browser Playback**

Requirements:

- Two-person browser sessions.
- Only translator user configures language preferences.
- Automatic source-language detection.
- Correct translation decision.
- Correct original audio pass-through.
- Correct translated audio delivery.
- Session creation and cleanup.
- Cancellation of outdated processing.
- Network reconnection.
- Error recovery.
- Latency measurements.

Test scenarios (master prompt §17 Phase 5): 1. translator speaks native ·
2. other speaks preferred · 3. translator speaks third language · 4. other
speaks third language · 5. both speak the same language · 6. low detection
confidence · 7. unsupported language detected · 8. network interruption ·
9. STT/translation/TTS provider failure · 10. pass-through without TTS ·
11. translated audio reaches only the intended recipient · 12. no feedback
loop.

## 2. Design (ADR-0014)

- **Rooms.** The host creates a room at `hello` (6-char invite code, e.g.
  `TTQJRJ`); the guest joins via the shared link `?room=CODE` and
  configures nothing — the room's language pair IS the host's pair.
  In-memory `RoomManager`, rooms linger 120 s (`ROOM_LINGER_SECONDS`) after
  the last member leaves so a dropped connection rejoins with the same
  code; then the room is destroyed.
- **Protocol v1 stays v1 (additive):** `hello.config.role` + `.room`,
  `peer` events (joined/left), `relay` events (original-audio pass-through:
  one metadata JSON + one binary PCM16 frame @ 16 kHz), optional `speaker`
  tags on transcription/translation/audio/error (present only in room
  mode); new fatal codes `room_required` / `room_not_found` / `room_full` /
  `missing_languages`; `/health` gains `rooms`.
- **Routing.** Transcripts + translations are broadcast to BOTH members
  (speaker-tagged); synthesized speech goes ONLY to the other participant
  (a solo room falls back to the speaker — the exact Phase 1–4 behavior);
  pass-through relays the ORIGINAL captured audio to the peer (never TTS,
  never replayed to the speaker); translation failures are shared
  utterance-scoped errors, TTS failures reach the audio's recipients,
  STT-level errors stay speaker-local.
- **Feedback-loop prevention.** Server-side: output audio is never
  re-ingested. Client-side: `PlaybackGuard` drops uplink while a REMOTE
  clip is audible (+300 ms tail); own-utterance TTS (solo) never arms it.
  Browser AEC stays enabled.

## 3. Delivery record (what was built)

Backend: `app/ws/rooms.py` (Room, RoomManager, linger); `audio_ws.py`
room-aware hello/join/rejoin, `_broadcast_text`, `_audio_recipients`,
`_send_error_to`, pass-through relay, room-aware effective config
(guest decoded against the host's pair), leave/linger cleanup; protocol
models (`RoomInfo`, `PeerEvent`, `RelayEvent`, `speaker` tags, optional
languages with validation); `ROOM_LINGER_SECONDS` setting; `/health rooms`.

Frontend: invite link flow (`?room=CODE` → guest mode with NO language
panel, room panel with the host's pair), host invite panel (code + copy
link + peer status), speaker-aware transcript (entries keyed
`(speaker, id)`, "Guest"/"Host" chips, RTL intact), wsClient relay pairing,
PlaybackGuard + uplink drop while remote audio plays, room-fatal errors
stop the reconnect loop, host reconnect re-sends its room code.

Tests: `test_ws_rooms.py` (16 room-level tests covering scenarios
1–11 with fakes), protocol/translation/wsClient/playbackGuard test
extensions, `scripts/e2e_phase5_check.py` (real two-connection E2E),
`frontend/scripts/smoke_phase5.mjs` (two real pages).

## 4. Verification (measured 2026-11-04)

| Check | Result |
| --- | --- |
| Backend suite | **171 passed + 1 xfailed** (+16 Phase 5 room tests; the previously-skipped `tts_real` test now runs with provisioned voices) |
| Frontend suite | **101/101 vitest** (+20: protocol parsing, relay pairing, speaker matching, PlaybackGuard), tsc clean, ESLint 0, build clean |
| `e2e_phase5_check.py` (live :3000, real models) | **4/4 PASS** — S1 host ur → guest en voice (RMS 0.185, synth 1 677 ms incl. first-load), host receives NO audio · S2 guest en → host ur voice (RMS 0.172) · S3 host es → guest ORIGINAL relay 112 644 B @ 16 kHz RMS 0.091, zero TTS · S4 guest ur (same language both sides) → host en voice (RMS 0.200) |
| Routing honesty (scenario 11) | asserted per utterance via ping-pong probes: the speaker's stream carries transcript + translation ONLY |
| Latency (live, 2 vCPU) | flush→transcription ≈ 6.3–7.0 s per fixture utterance (real decode incl. detect pass), MT 143–173 ms, voice delivery 149–1 684 ms (first-load documented, KI-014) |
| Regression: translation E2E (Phase 3 paths) | **3/3 PASS** |
| Regression: silence guard | **PASS** |
| Browser smoke `smoke_phase5.mjs` (two pages) | **12/12** — invite link join, guest has NO language settings, peer presence both sides, both record, zero console errors |
| Regression: browser smoke `smoke_phase2.mjs` | **10/10** (solo flow unchanged) |

## 5. Honest boundaries

- The acoustic feedback loop (scenario 12) is prevented architecturally
  (no server re-ingestion — asserted) and client-side (PlaybackGuard,
  unit-tested); real-room acoustic behavior on real hardware is a Phase 6
  measurement (KI-004/KI-015).
- Transcript line deletion is local-only (not synced to the peer) —
  KI-016. Host language changes apply on the next connection — KI-017.
- Rooms are in-memory and single-process — restarting the server ends all
  rooms (Phase 10 scope for persistence).
