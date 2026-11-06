# CURRENT_STATE

_Last updated: 2026-11-06 — Phase 6: mobile/browser compatibility implemented + verified (v0.6.0), pending approval_

## Phase

**Phase 6 — Mobile/browser compatibility** — implemented and verified
(2026-11-06, ADR-0016). **Phases 1–5 are complete and closed** (the Phase 5
approval record is closed by the user's "start phase 6" instruction, per
master prompt §23). Work sits on `main` and is pushed to `main` and the
`phase-6` branch per the user's delivery instruction (`PHASE-N.md` deep
dives live ONLY on their own branches; `prompt.md` now documents Phase 6
and clears the previous phase's record).

**QA round (2026-11-05, ADR-0015):** the independent QA verification
(Journal) blocked acceptance with BUG-001 (deployment had no usable Piper
voices — HF 429 during boot provisioning), BUG-002 (a ghost seat blocked
rejoin ~180 s after an unclean drop through the preview proxy) and BUG-003
(fatal errors' close frames swallowed by the same proxy). All three are
fixed and re-verified live: voices provisioned + verify-gated boot +
`/health.tts.warning`; stale-seat eviction (12 s silence threshold, live
occupants protected); client 4 s heartbeat + `room_full` reconnect retry.
Deployment runs v0.5.1 with the full ur/en/es voice set.

## Current task

Phase 6 complete: mobile/browser compatibility (ADR-0016). The code-
controlled half is implemented and live-verified:

- **Honest capture lifecycle** — backgrounding the tab (`visibilitychange
  → hidden`), navigating away (`pagehide`) or an OS interruption (capture
  AudioContext leaves `running` — iOS `interrupted`, calls, Siri) now
  finalizes the recording exactly like a connection loss: capture stopped,
  buffered audio flushed, playback + PlaybackGuard cleared, honest banner
  on return ("Recording stopped — the browser suspended the tab…").
  Return-to-tab re-arms playback via `resumeIfCreated()` (never creates an
  AudioContext — autoplay-safe).
- **Screen wake lock** while the mic is open (`services/wakeLock.ts`;
  progressive enhancement, denials degrade silently).
- **Platform telemetry** — optional `hello.config.client.platform` (coarse
  tag like `ios-safari`, never a raw UA) aggregated into
  `/health sessions.by_platform`; verified end-to-end under emulation.
- **PWA-lite + mobile comfort** — manifest + generated icons + apple
  metas; `overscroll-behavior-y: none` (no pull-to-refresh reload
  mid-recording); 16 px form controls (no iOS focus zoom); sticky capture
  panel on small screens; ≥ 44 px touch targets; stale phase copy
  refreshed; frontend version 0.6.0.

Verified 2026-11-06: backend **177 passed + 1 xfailed** (+7 Phase 6
tests), frontend **123/123** (+9) + tsc + ESLint + build;
`smoke_phase6.mjs` under full iPhone emulation **8/8** (incl. the
platform tag end-to-end and the background-tab guard); regressions
`smoke_phase2` 10/10, `smoke_phase5` 12/12; live E2E translation 3/3,
two-participant routing 4/4, silence guard PASS. The hardware half —
Android Chrome, iPhone Safari (silent switch, low-power, lock screen),
loudspeaker acoustics — is the written human matrix in testing.md §13b
(KI-004/015/018 stay open until it runs). APP_VERSION 0.6.0.

## Next step

Await the user's approval verdict on Phase 6 (v0.6.0). The real-device
matrix (testing.md §13b) runs when physical phones are available. Next
phase per the master prompt §17: Phase 7 — Zoom integration (first
meeting app), which awaits explicit user approval (§23).
