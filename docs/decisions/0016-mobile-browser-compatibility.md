# ADR-0016: Phase 6 — Mobile/browser compatibility and capture lifecycle

Date: 2026-11-06 · Status: accepted · Phase: 6 (v0.6.0)

## Context

The master prompt's roadmap assigns Phase 6 to "mobile/browser compatibility
on real devices". Three known issues scope it: KI-004 (real-device capture
behavior untested — background tab, lock screen, iOS), KI-015 (playback on
real devices unmeasured — iOS autoplay/silent switch) and KI-018 (acoustic
feedback on real hardware). None of these can be measured inside the
sandbox: there is no physical phone, no iOS, and no acoustic room. What the
sandbox CAN do — and what earlier phases established as its proxy — is full
device emulation in headless Chromium (viewport, touch, mobile user agent,
fake microphone; the Phase 2 smoke pioneered this).

The code review found the real gaps that would break a phone user today:

1. **No capture lifecycle at all.** Mobile OSes suspend microphone capture
   and JS timers the moment a tab is backgrounded. The app happily stayed
   in the "recording" state, streaming silence to the server and tripping
   the idle/stale-seat logic; on return, the mic was dead while the button
   still claimed it was live. Nothing listened to `visibilitychange` or
   `pagehide`, and the capture `AudioContext` had no `statechange` watchdog
   for iOS's `interrupted` state (phone call, Siri, control center).
2. **No screen wake lock** — a locked phone suspends capture within
   seconds, so a hands-free conversation died silently.
3. **No way to see WHO connects.** The real-device matrix (a human task)
   had no server-side signal to confirm which platforms actually arrived.
4. **Small but real mobile-comfort gaps:** iOS zooms focused controls with
   font-size < 16 px; pull-to-refresh could reload the app mid-recording;
   the mic button scrolled away on long transcripts; no PWA manifest for
   add-to-home-screen.

## Decision

1. **Honest capture lifecycle (the core fix).** A hidden tab finalizes the
   recording exactly the way a connection loss does — capture stopped,
   buffered audio flushed, playback and the feedback guard cleared — with
   a plain-language banner ("Recording stopped — the browser suspended the
   tab in the background. Tap the mic to continue."). `pagehide` finalizes
   quietly (banners are pointless while the page goes away). A new
   `onStateLost` callback in `startMicCapture` fires when the capture
   `AudioContext` stops running mid-recording (statechange watchdog,
   armed only when the caller passes the callback, disarmed in `stop()`
   so our own close never triggers it). Returning to the tab re-arms
   playback via `SpeechPlayer.resumeIfCreated()` — which deliberately
   NEVER creates an `AudioContext` (autoplay policies would flag it).
2. **Wake Lock as progressive enhancement.** `services/wakeLock.ts` wraps
   `navigator.wakeLock` (injectable for tests): acquire on recording start,
   release on finalize, forget the sentinel when the OS drops it. Denied
   locks (low-power mode, hidden page) degrade to "no lock" silently —
   never an error surface.
3. **Coarse platform telemetry.** Protocol v1 stays v1 additively: an
   OPTIONAL `hello.config.client.platform` (lowercase tag ≤ 24 chars, e.g.
   `ios-safari`; never a raw user-agent string; `extra="forbid"` catches
   mistakes). The server stores it on the session and `/health` aggregates
   connected clients into `sessions.by_platform` — the human device matrix
   gets a ground-truth signal that a phone actually arrived. Absent on
   older clients; never required; not echoed into `ready`.
4. **PWA-lite + mobile comfort CSS.** `manifest.webmanifest` (+ generated
   192/512 px icons), apple/mobile-web-app metas, refreshed page
   description. `overscroll-behavior-y: none` kills pull-to-refresh
   reloads mid-recording; `font-size: 16px` on form controls stops iOS
   focus-zoom; the capture panel becomes sticky on small screens (mic
   always reachable); control rows get ≥ 44 px touch targets;
   `-webkit-tap-highlight-color: transparent` (components style their own
   pressed states). No framework, no new dependency, one CSS block.
5. **Verification = emulation + suites + a human matrix.** New
   `frontend/scripts/smoke_phase6.mjs` runs the app under full iPhone
   emulation (8 checks: render, manifest, no horizontal overflow, connect,
   platform tag end-to-end, tap-start/tap-stop, background-tab guard).
   Everything the sandbox cannot assert (speaker on a real phone, iOS
   silent switch, lock-screen, acoustic coupling) is written up as a
   step-by-step manual matrix in `docs/testing.md §13` for the human run —
   no compatibility claims are made from the sandbox (AGENT.md §6).

## Alternatives rejected

- **Keep recording while hidden, mute the uplink** — the mic graph is
  dead/suspended on mobile anyway; pretending otherwise leaves a stale UI
  state and a session the server evicts. Honest finalize beats silent
  muting.
- **Re-acquire the wake lock on visibilitychange** — recording is already
  finalized by then; re-acquiring would fight the lifecycle instead of
  following it.
- **Full user-agent strings in the protocol** — privacy noise for zero
  operational value; a coarse tag answers the only real question
  ("which platform is this?").
- **Auto-restart recording when the tab returns** — surprising and
  consent-sensitive (the mic is a hardware permission); the user taps.

## Consequences

- Mobile backgrounding/locking produces honest UI states and clean server
  sessions instead of ghost recordings (composes with the QA-round
  stale-seat eviction, ADR-0015).
- `/health` now answers "what devices are actually using this?" — inputs
  for Phases 7–9 meeting integrations.
- The real-device matrix remains a HUMAN task; until it is run, mobile
  claims stay limited to "verified under device emulation in headless
  Chromium" (documented in testing.md §13 and README limitations).
