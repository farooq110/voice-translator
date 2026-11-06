# prompt.md — Phase 6 Scope & Delivery Record

**Phase:** 6 of 10 — Mobile/browser compatibility on real devices
**Status:** implemented — full verification round complete — **pending user approval** (§23 workflow)
**Version:** v0.6.0
**ADR:** [docs/decisions/0016-mobile-browser-compatibility.md](docs/decisions/0016-mobile-browser-compatibility.md)

> This repository documents the **current phase only** in this file. The
> complete master prompt (25 sections, 10-phase roadmap, approval workflow)
> is maintained by the product owner outside the repository. The overall
> delivery roadmap and phase statuses live in
> [README.md § Roadmap](README.md#roadmap-master-prompt-17); durable agent
> rules live in [AGENT.md](AGENT.md). Branch-local deep dives: `PHASE-3.md`
> (phase-3 branch), `PHASE-4.md` (phase-4 branch), `PHASE-5.md` (phase-5
> branch), `PHASE-6.md` (phase-6 branch) — each documents only its own
> phase. (Previous phases' records live on in their branch docs and the
> CHANGELOG; this file was the Phase 5 record until 2026-11-06.)

---

## 1. Phase 6 scope (master prompt §17)

Make the browser app behave correctly on real phones and modern mobile
browsers — the phase the roadmap labels "Mobile/browser compatibility on
real devices". Three known issues define the honest test surface:

- **KI-004** — capture behavior on real devices: background tab, lock
  screen, iOS interruptions.
- **KI-015** — playback on real devices: autoplay policies, silent
  switch, low-power mode.
- **KI-018** — acoustic feedback on real hardware (loudspeaker→mic).

The sandbox has no physical device, so Phase 6 delivers (a) every behavior
that CODE controls and that CAN be verified here, and (b) the step-by-step
human matrix for the hardware part — with no compatibility claims made
from the sandbox (AGENT.md §6).

## 2. Design (ADR-0016)

- **Honest capture lifecycle.** Mobile OSes suspend capture + timers when
  a tab is backgrounded; the old code kept a fake "recording" state
  streaming silence. Now: `visibilitychange → hidden` finalizes the
  recording exactly like a connection loss (capture stopped, buffer
  flushed, playback + feedback guard cleared) with a plain-language
  banner; `pagehide` finalizes quietly; the capture `AudioContext` gets a
  `statechange` watchdog (`onStateLost`) for iOS `interrupted` (calls,
  Siri); returning to the tab re-arms playback via a resume that NEVER
  creates an `AudioContext` (autoplay-safe `resumeIfCreated`).
- **Screen Wake Lock** (`services/wakeLock.ts`, progressive enhancement):
  acquired while the mic is open, released on finalize, OS-dropped
  sentinels forgotten; denials (low-power mode) degrade silently.
- **Coarse platform telemetry.** Protocol v1 stays v1 additively: optional
  `hello.config.client.platform` (lowercase tag ≤ 24 chars, e.g.
  `ios-safari` — never a raw user-agent); `/health` aggregates connected
  clients into `sessions.by_platform`, giving the human device matrix a
  ground-truth signal. Older clients omit it; never required; not echoed.
- **PWA-lite + mobile comfort CSS.** `manifest.webmanifest` + generated
  192/512 px icons + apple/mobile-web-app metas; `overscroll-behavior-y:
  none` (no pull-to-refresh reload mid-recording); 16 px form controls
  (no iOS focus zoom); sticky capture panel on small screens (mic always
  reachable); ≥ 44 px touch targets; no framework, no new dependency.

## 3. Delivery record (what was built)

Backend: `ClientInfo` protocol model (`client.platform`, optional) on
`HelloConfig`; `Session.platform` + `/health sessions.by_platform`
aggregation; one line in the hello handler. +7 tests (validation ×7
params, aggregation ×2, stats shape).

Frontend: `services/platform.ts` (UA → coarse tag, pure); 
`services/wakeLock.ts` (injectable wrapper); `audioCapture.onStateLost`
watchdog (disarmed on stop — our own close never fires it); hook
lifecycle effects (hidden/pagehide finalize + banner, visible →
`resumeIfCreated`), wake-lock tie-in, platform tag in every hello;
`SpeechPlayer.resumeIfCreated`; index.html metas + manifest; app.css
mobile block; stale "Phase 1/Phase 5" page copy refreshed; version 0.6.0.
+9 frontend tests (platform mapping, wake lock incl. OS-drop/deny,
`resumeIfCreated` never creates a context).

Tests/ops: `frontend/scripts/smoke_phase6.mjs` (full iPhone emulation
against the live server — 8 checks incl. the platform tag end-to-end and
the background-tab guard); smoke_phase2/5 restored onto `main` and re-run
as regressions; real-device matrix written into `docs/testing.md §13b`
(Android Chrome / iPhone Safari / two-phone room, ~15 min).

## 4. Verification (measured 2026-11-06)

| Check | Result |
| --- | --- |
| Backend suite | **177 passed + 1 xfailed** (fast markers; +7 Phase 6 tests) |
| Frontend suite | **123/123 vitest** (+9 Phase 6), tsc clean, ESLint 0, build clean (263 kB) |
| `smoke_phase6.mjs` (iPhone emulation, live :3000) | **8/8 PASS** — render, manifest, 0 px overflow, connect, `by_platform={"ios-safari":1}`, tap-start, tap-stop (honest `no_speech_detected` only), background-tab guard fires the honest banner |
| Regression: `smoke_phase2.mjs` (solo) | **10/10** |
| Regression: `smoke_phase5.mjs` (two pages) | **12/12** |
| Live E2E: translation (Phase 3 paths) | **3/3 PASS** |
| Live E2E: two-participant routing (Phase 5) | **4/4 PASS** — voices reach only the listener; relay without TTS; speaker stream carries text only |
| Silence guard (hallucination) | **PASS** |

## 5. Honest boundaries

- No physical phone exists in this sandbox: everything above is device
  EMULATION in headless Chromium. The human matrix
  ([testing.md §13b](docs/testing.md)) — Android Chrome, iPhone Safari
  (interruptions, silent switch, low-power mode, lock screen), acoustic
  coupling on loudspeakers — is written and ready but NOT yet run; no
  hardware claims are made (KI-004/KI-015/KI-018 stay open until it is).
- Wake lock support is progressive: iOS Safari historically lacks it
  (screen locks anyway); the app degrades to the honest-stop path.
- The platform tag is UA-heuristic: iPadOS 13+ masquerading as desktop
  Safari reports as desktop; accepted (observational only).
- Transcript line deletion stays local-only (KI-016) and host language
  changes still apply on the next connection (KI-017) — accepted Phase 5
  scope, untouched here.

## 6. QA round v0.6.1 — hello-handshake hardening (2026-11-07, ADR-0017)

The independent QA verification of this phase (Journal, 2026-11-07)
returned "CONDITIONAL": the compatibility work itself verified clean
(24/26 live checks, developer smoke reproduced 8/8, frontend 123/123),
but BUG-601 (HIGH) had to be fixed — after an uplink blackout the client
could stick in "Reconnecting…" forever (hello sent once per socket, the
server pong-answered before the handshake, hello-less sockets lived until
the idle timeout). BUG-602 (LOW) was a stale deployed version string.

Both are closed in v0.6.1. The handshake now saves itself on both sides
(idempotent re-hello until `ready` + client watchdog; server silence on
pre-hello pings, idempotent hello re-ack, 15 s hello timeout). Re-measured
after the fix:

| Check | Result |
| --- | --- |
| Backend suite (+4 BUG-601 tests) | **190 passed + 1 xfailed** |
| Frontend suite (+2 BUG-601 tests) | **125/125**, tsc/ESLint/build clean |
| `smoke_phase6.mjs` incl. new **M9 blackout-recovery check** | **9/9 PASS** |
| Regression: `smoke_phase2.mjs` / `smoke_phase5.mjs` | **10/10** / **12/12** |
| Live E2E: translation | **3/3 PASS** |
| `/health.version` after redeploy | **0.6.1** |

The real-device matrix (§13b) remains the gate for any hardware claim —
the M9 recovery row joins it as a human-run step on a real phone.
