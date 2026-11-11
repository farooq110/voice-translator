# CURRENT_STATE

_Last updated: 2026-11-10 — Phase 7 (Zoom integration via the RTMS bridge, ADR-0019) implemented, verified live (e2e_zoom_check 5/5 + full regression battery green), pending user approval_

## Phase

**Phase 7 — Zoom integration (first meeting app)** — implemented and
verified (2026-11-10, ADR-0019). **Phases 1–6 are complete and closed**
(the Phase 6 approval record is closed by the user's "start phase 7"
instruction, per master prompt §23). Work sits on the `phase-7` branch
(created from the phase-6 tip, which carries the closed v0.6.3 QA state);
`prompt.md` now documents Phase 7 and clears the previous phase's record;
`PHASE-7.md` lives only on the phase-7 branch.

**What the bridge is:** when a Zoom meeting's RTMS stream starts
(webhook `meeting.rtms_started`, v0-signature-verified), one worker
connects to Zoom's documented two-phase WebSocket, negotiates raw
L16/16 kHz/mono audio, and feeds it into the app's OWN `/ws/audio` as an
ordinary protocol-v1 host of a two-participant room — the human joins
via the invite published at `GET /zoom/meetings` and hears the meeting
translated live on their own device. The engine is untouched; the
frontend is untouched; the integration is OFF unless enabled +
credentialed.

**Verification (2026-11-10):** backend **220 passed + 1 xfailed** (+30
Zoom tests); frontend untouched **139/139** + tsc/ESLint/build clean;
live `scripts/e2e_zoom_check.py` **5/5 in 17.9 s** against the
signature-verifying fake of Zoom's edge (url_validation; worker live +
invite published; handshake signature verified + L16/16 kHz/mono; 6.6 s
ur fixture → 3 utterances → guest transcription(speaker=host) + real
ur→en OPUS-MT + 111,618 B routed PCM; graceful stop with
STREAM_CLOSE_REQ); regression battery: e2e translation **3/3**,
two-participant **4/4**, TTS **3/3**, silence guard **PASS**, smokes
**10/10 / 12/12 / 9/9**; `/health.version` **0.7.0**.

**Honest boundary:** Zoom's production edge is NOT verified (no Zoom
account in the sandbox) — KI-029 with the human runbook in
`docs/deployment.md §6a`; meeting-audio endpointing is a measured knob
(KI-030); nothing is injected INTO the meeting in v1 (KI-031).

## Phase

**Phase 6 — Mobile/browser compatibility** — implemented and verified
(2026-11-06, ADR-0016). **Phases 1–5 are complete and closed** (the Phase 5
approval record is closed by the user's "start phase 6" instruction, per
master prompt §23). Work sits on `main` and is pushed to `main` and the
`phase-6` branch per the user's delivery instruction (`PHASE-N.md` deep
dives live ONLY on their own branches; `prompt.md` now documents Phase 6
and clears the previous phase's record).

**QA round (2026-11-07, ADR-0017, v0.6.1):** the independent QA
verification (Journal) returned "CONDITIONAL" with BUG-601 (HIGH): a
client whose reconnect `hello` was lost (mobile dead zone / Wi-Fi↔LTE)
stuck in "Reconnecting…" forever — hello sent once per socket, the server
pong-answered before the handshake, and hello-less sockets lived until the
idle timeout. Fixed on both sides: idempotent re-hello until `ready` +
hello-ack watchdog on the client; silence on pre-hello pings, idempotent
hello re-ack (fresh `ready`, same session/room) and a 15 s hello timeout on
the server. Re-verified live: smoke_phase6 9/9 (new M9 blackout-recovery
check), smoke_phase2 10/10, smoke_phase5 12/12, backend 190 passed + 1
xfailed, frontend 125/125, E2E translation 3/3. BUG-602 (stale deployed
version string) closed by redeployment + a deploy-checklist version
assertion. Deployment runs v0.6.1.

**QA round (2026-11-07, ADR-0018, v0.6.2):** the independent QA
verification (Journal, against `phase-6` @ `7da3752`) returned "PASS WITH
FINDINGS" with two open defects, both fixed: GAP-604 (medium) — no
microphone device-change handling, so a lost headset/Bluetooth device
kept a live-looking recording streaming silence — now the capture service
watches the track `ended` event, reports `device_lost` exactly once, and
the recording is finalized with the "microphone was disconnected" banner;
BUG-603 (low) — `NotSupportedError`-flavored permission denials showed
the raw "Not supported" text, now mapped to honest access guidance.
Re-verified live: frontend 139/139 (+14 audioCapture tests), backend 190
passed + 1 xfailed, BUG-603 CDP-denial probe 4/4, smokes 9/9 / 10/10 /
12/12, E2E 3/3 / 3/3 / 4/4 / 3/3, silence guard PASS,
`/health.version` 0.6.2. The Journal's PASS verdicts for the v0.6.1
handshake fixes (BUG-601 recovery 0.186 s, version stamp) are unchanged.

**QA re-verification (2026-11-07, clean-sandbox replay):** after the
sandbox wiped the model provisioning, the entire battery was replayed
from scratch against the unchanged v0.6.1 tip — backend 190 passed + 1
xfailed, frontend 125/125 + tsc/ESLint/build clean, live E2E translation
3/3, TTS 3/3, two-participant 4/4, synthetic 3/3, silence guard PASS,
smokes 10/10 / 12/12 / 9/9 (M9 blackout recovery, BUG-601 regression),
`/health.version` 0.6.1. A fresh-eyes skim of the new handshake paths
found no defects. **Zero open findings remain from the QA Journal**; the
only open KIs are the human hardware runs (KI-004/015/018) and accepted
limitations (KI-003/012).

**QA re-test round (2026-11-08, v0.6.3):** the independent QA re-test
(Journal (2), against `phase-6` @ `69941d1`, deployment v0.6.2) returned
"PASS WITH FINDINGS" — all four previous defects verified fixed live
(BUG-601: a 14 s uplink blackout recovers to Connected in 1.79 s) — and
reported two new low-severity defects, both fixed: BUG-604 — the solo
per-line "speaking" chip never rendered because the solo `audio` event
shipped without a `speaker` tag while the transcript carried `"host"`
(the client keys lines by `(speaker, id)`); the audio event now uses the
same `_speaker()` rule as transcription (room mode unchanged, guard
arming unchanged) — and BUG-605 — the "copy invite link" button measured
31 px at 390 px, below the 44 px touch-target standard; it joins the
44 px mobile control-row rule. The Journal's M5 observation (strict
platform-count equality is brittle on a shared deployment) is fixed in
the smoke script (`>= 1`). Re-verified live: backend 190 passed + 1
xfailed, frontend 139/139, smokes 9/9, chip + height probes PASS,
`/health.version` 0.6.3.

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
