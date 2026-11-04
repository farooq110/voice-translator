# CURRENT_STATE

_Last updated: 2026-11-04 — Phase 4 implemented + verified (v0.4.0), pending approval_

## Phase

**Phase 4 — Text-to-Speech and Browser Audio Playback** — implemented and
verified (2026-11-04, ADR-0013). Phases 1–3 remain complete (Phase 3 is
pending its own approval; both were requested together and delivered in one
working session). Work sits on `main` and is pushed to both `main` and
`phase-3` per the user's standing delivery instruction.

## Current task

Phase 4 complete: offline Piper TTS (ur/en/es voices under `models/tts/`,
ADR-0013), bounded TTSService (30/min, ≤1 concurrent, 10 s timeout, 800-char
cap), additive protocol v1 `audio` metadata event + one binary PCM16 frame
per translated utterance (metadata+binary sent under one lock), 5 new
utterance-scoped non-fatal error codes, per-connection synthesis
cancellation, browser SpeechPlayer (sequential scheduling — overlap
impossible by construction — bounded queue with oldest-drop and re-basing
after cancel, mute toggle, autoplay unlock, stop/cancel), /health `tts`
section, start.sh voice provisioning. Pass-through bypasses TTS (asserted in
tests, verified live). Verified: backend 155+1xfail, frontend 81/81 +
typecheck + lint + build, e2e_tts_check 3/3 live (real speech → real MT →
real synthesis, RMS-checked; es pass-through produced ZERO audio frames),
translation E2E 3/3, silence guard PASS, browser smoke 10/10. APP_VERSION
0.4.0. **Phase 5 (complete browser MVP — two-person sessions) awaits
explicit user approval (master prompt §23).**

## Next step

Await the user's approval verdict on Phase 3 (v0.3.1) and Phase 4 (v0.4.0).
On approval of Phase 4, start Phase 5 per the master prompt §17 (two-person
browser sessions, audio routing, session lifecycle).
