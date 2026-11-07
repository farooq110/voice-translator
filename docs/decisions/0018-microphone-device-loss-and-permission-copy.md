# ADR-0018: Microphone device-loss handling and honest permission copy

Date: 2026-11-07 (QA round v0.6.2)
Status: Accepted
Deciders: voice-translator maintainers (developer), independent QA (Journal, 2026-11-07 — "PASS WITH FINDINGS", GAP-604 medium, BUG-603 low)

## Context

The independent QA verification of Phase 6 (Journal, 2026-11-07, against
`phase-6` @ `7da3752`, deployment v0.6.1) returned **PASS WITH FINDINGS**
with two open defects:

- **GAP-604 (medium):** the "Device changes" verify item has no
  implementation — no `devicechange` listener and no `track.onended`
  handler anywhere (`devicechange` / `enumerateDevices`: 0 occurrences in
  the deployed bundle). If the active microphone disappears mid-recording
  (unplugged headset, Bluetooth earbud leaving range — a routine phone
  scenario), the MediaStreamTrack ends while the AudioContext **keeps
  running**: the statechange watchdog never fires, the UI keeps saying
  "Hands-free listening…", and the app streams digital silence — the exact
  zombie-recording failure mode Phase 6 eliminated for backgrounding.
- **BUG-603 (low):** a denied microphone permission surfaces as the
  generic "start failed — Not supported" banner in environments whose
  denial arrives as `DOMException NotSupportedError` (WebViews/kiosk
  builds; the Journal's own headless probe) instead of the intended
  permission guidance. Standard-browser denials (`NotAllowedError`) were
  already classified correctly — hence low severity.

The Journal also re-verified the v0.6.1 handshake fixes (BUG-601/BUG-602)
live — blackout recovery in 0.186 s solo and healthy in room mode — so
this round touches only the capture start/stop surface, not the protocol.

## Decision

**1. Device loss is the capture service's job, reported through the same
honest-stop channel as interruption (GAP-604).**

- `startMicCapture` attaches a **self-removing `ended` listener** to every
  audio track of the capture stream and reports
  `onStateLost('device_lost')` exactly once.
- The `onStateLost` callback gains a **reason parameter**
  (`'context_interrupted' | 'device_lost'`): the existing iOS/Android
  capture-graph watchdog reports `'context_interrupted'` unchanged.
- `stop()` removes the ended listener **before** stopping tracks, so the
  app's own teardown can never be mistaken for a device loss (and per
  spec `track.stop()` does not fire `ended` — the removal is the
  defensive belt for engine quirks).
- **`finalizeRecording()` is reused, not a new state machine** — the
  Journal's suggested shape. The hook keeps one code path: finalize,
  then set the banner for the reported reason.
- The user-facing copy lives next to the cause as a pure helper
  (`stateLostBanner(reason)`) so code and text cannot drift apart:
  - `device_lost` → "Recording stopped — the microphone was disconnected.
    Tap the mic to continue."
  - `context_interrupted` → the existing "the phone interrupted the
    microphone" text, byte-for-byte.
- A `devicechange` label-refresher is **not** added in this round: it
  refreshes selector labels only, affects no recording state, and the
  Journal marked it "optionally". The track-ended watchdog is the fix
  that closes the silent-failure gap.

**2. `NotSupportedError` from getUserMedia is permission guidance, not a
raw error (BUG-603).**

- `classifyGetUserMediaError` maps `NotSupportedError` to
  `permission_denied` with the Journal's suggested neutral text:
  "Microphone access was refused or is unavailable — check site
  permissions." (It must NOT claim "permission was denied" — some
  NotSupportedError cases are capability, not consent.)
- `NotAllowedError`/`SecurityError`/`NotFoundError`/`OverconstrainedError`
  mappings and the `start_failed` default are unchanged; the default keeps
  the browser's own message (`NotReadableError` = device busy stays an
  honest `start_failed`).

**3. Verification artifacts (per the Journal's regression-test row).**

- Unit: `audioCapture.test.ts` (new, 14 tests) — every DOMException name
  through the public `startMicCapture` path; track-ended →
  `device_lost` exactly once; own `stop()` never fires it;
  `context_interrupted` watchdog unchanged; banner copy pinned.
- Live probe: `frontend/scripts/probe_bug603_deny.mjs` reproduces the
  Journal's exact CDP-denial scenario against a live server (4/4) — kept
  out of the standing smoke suite deliberately (the standing guard is the
  unit test; the probe needs an explicit CDP deny that conflicts with the
  smoke's auto-grant flag).
- Manual: `docs/testing.md §13b` gains a device-change row for each phone
  (Bluetooth headset off mid-recording).

## Consequences

- Mid-recording mic loss now ends the recording with an honest,
  actionable banner on every platform — the zombie-recording class is
  closed for device changes the way it was closed for backgrounding.
- The callback signature of `onStateLost` changes (reason parameter) —
  the only caller is `useTranslatorSession`; no protocol change (client
  side only, `/health` and the wire format untouched).
- Version 0.6.2. No server behavior change; old/new client-server mixes
  are unaffected.
- The human matrix (§13b) remains the gate for hardware claims
  (KI-004/015/018 unchanged).

## Verification

- Frontend: **139/139** (125 + 14 new), tsc/ESLint clean, build 264.01 kB.
- Backend: **190 passed + 1 xfailed** (untouched code; APP_VERSION 0.6.2).
- Live: `/health.version` 0.6.2; e2e translation 3/3, TTS 3/3,
  two-participant 4/4, synthetic 3/3, silence guard PASS;
  smoke_phase2 10/10, smoke_phase5 12/12, smoke_phase6 9/9 (M9 holds).
- BUG-603 live probe: 4/4 (CDP-denied mic → "PERMISSION DENIED /
  Microphone access was refused or is unavailable — check site
  permissions.", no "Not supported" text anywhere).
