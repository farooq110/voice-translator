# CURRENT_STATE

_Last updated: 2026-11-04 — Phase 5 implemented + verified (v0.5.0), pending approval_

## Phase

**Phase 5 — Complete Browser MVP (two participants)** — implemented and
verified (2026-11-04, ADR-0014). Phases 1–4 remain complete (Phases 3 and 4
are pending their own approval records). Work sits on `main` and is pushed
to `main` and the `phase-5` branch per the user's delivery instruction
(`PHASE-3.md`/`PHASE-4.md`/`PHASE-5.md` live ONLY on their own branches).

## Current task

Phase 5 complete: two-participant rooms (ADR-0014) — host creates a room at
hello (6-char invite code), guest joins via `?room=CODE` and configures
nothing (the room's pair is the host's); rooms linger 120 s
(`ROOM_LINGER_SECONDS`) after the last member leaves for reconnection with
the same code. Protocol v1 stays v1 additively: `hello.role`/`hello.room`,
`peer` joined/left events, `relay` events (original-audio pass-through,
16 kHz PCM16, one metadata + one binary frame), optional `speaker` tags on
transcription/translation/audio/error, fatal `room_required` /
`room_not_found` / `room_full` / `missing_languages`, `/health rooms`.
Routing: transcripts + translations broadcast to both members
(speaker-tagged); synthesized audio delivered ONLY to the other participant
(solo room falls back to the speaker — exact Phase 1–4 shape); pass-through
relays the ORIGINAL captured audio to the peer (never TTS, never replayed to
the speaker). Feedback-loop prevention: no server-side re-ingestion
(architectural) + client `PlaybackGuard` dropping uplink while remote audio
plays (+300 ms tail). Frontend: invite link flow, guest view with NO
language panel, host invite panel (code + copy link + peer status),
speaker-aware transcript keyed `(speaker, id)`, relay pairing in wsClient.
Verified: backend **171 passed + 1 xfailed** (+16 room tests), frontend
**101/101** + tsc + ESLint + build, live two-participant E2E **4/4**
(real STT + real MT + real Piper voices; routed delivery proven per
utterance via ping-pong probes), translation E2E 3/3, silence guard PASS,
`smoke_phase5` **12/12** (two real pages via the invite link),
`smoke_phase2` 10/10 (solo unchanged). APP_VERSION 0.5.0. **Phase 6 (real
devices) awaits explicit user approval (master prompt §23).**

## Next step

Await the user's approval verdict on Phases 3 (v0.3.1), 4 (v0.4.0) and 5
(v0.5.0). On approval of Phase 5, start Phase 6 per the master prompt §17
(mobile/browser compatibility on real devices).
