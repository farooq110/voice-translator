# Phase 6 — the app learns to behave on a real phone

*This file lives on the `phase-6` branch and tells the story of Phase 6 only —
what changed for someone holding the app in one hand, how it was built, how
to use it, and what the numbers actually were. The whole-product overview
lives in `README.md` on `main`; the earlier stories are `PHASE-3.md`,
`PHASE-4.md` and `PHASE-5.md` on their own branches. Design reasoning:
[ADR-0016](docs/decisions/0016-mobile-browser-compatibility.md).*

## What Phase 6 actually is

Phases 1–5 built a complete translator for two people. But it had grown up
on desktops, and a phone treats a browser app very differently. Put the app
in your pocket's browser and the operating system has opinions: the moment
you switch to another app or lock the screen, it suspends the microphone and
throttles the page. Phase 5's app didn't know that. It kept saying
*"Hands-free listening…"* — while streaming digital silence to the server,
wearing out the reconnect logic, and handing you a mic that was quietly dead
when you came back. If a phone call arrived mid-sentence, the same invisible
failure. This phase is the one where the app stops pretending.

The rule we implemented is one sentence: **the UI may never claim the
microphone is live when the phone has taken it away.** So now, the instant
the tab goes to the background, the recording ends the same honest way a
dropped connection always has — whatever you were saying is flushed and
transcribed, the voice queue and the feedback guard are cleared, and when
you come back a banner says plainly: *"Recording stopped — the browser
suspended the tab in the background. Tap the mic to continue."* No fake
state, no silent failure. A phone call, Siri, or anything else that yanks
the audio hardware triggers the same treatment through a new watchdog that
watches the capture context itself (iOS reports interruptions as an
`interrupted` state — the app now notices). And when you return, the
playback side quietly re-arms itself in case the OS muted it — carefully
*without* creating an audio context out of thin air, which would trip the
browser's autoplay policy.

Two smaller conveniences ride along. While the microphone is open the app
asks the phone to keep the screen awake (the Screen Wake Lock API — a locked
phone suspends capture within seconds); if the phone refuses — low-power
mode, say — the app shrugs and moves on, because the honest-stop path now
covers that case anyway. And the app became installable: it gained a web
app manifest and icons, so *Add to Home Screen* on Android or iOS puts a
real launcher on the phone that opens full-screen, not a browser tab with
haystack around it.

## The shape underneath

The largest piece is lifecycle wiring in the session hook, and the design
decision was to reuse what already existed. When the page hides, the hook
calls the very same `finalizeRecording()` that the connection-lost path
calls — capture stops, the buffer flushes, in-flight decodes are settled.
`pagehide` (navigating away, app switch) finalizes quietly, because a banner
is pointless while the page is leaving. The capture service gained one
optional callback, `onStateLost`, fired by a `statechange` listener that is
armed only for the duration of a recording and disarmed in `stop()` — so
the app's own shutdown can never trigger it.

The phone also got small physical courtesies that only matter when you hold
the page in your hand. A swipe-down no longer reloads the app mid-recording
(`overscroll-behavior-y: none`). Tapping a language picker no longer makes
the page zoom (the classic iOS trap: any control under 16 px triggers
focus zoom — ours are pinned). The microphone panel now follows you down a
long transcript instead of scrolling away, and the control rows keep
comfortable 44-pixel thumb targets. None of this needed a framework; it's
one honest CSS block and a handful of metas.

One piece is about *seeing* rather than touching. The master prompt's
Phase 6 is "compatibility on real devices", but a sandbox has no devices —
so the server now at least knows who arrives. The hello message may carry a
short, coarse tag like `ios-safari` or `android-chrome` (never a raw
user-agent string — that's privacy noise), and `/health` rolls it up into a
`by_platform` tally of connected clients. When someone eventually runs the
real-device checklist in `docs/testing.md §13b`, the server log is the
ground truth that a real phone actually connected. Old clients simply don't
send the tag; nothing breaks.

And the verification story had to be honest too. The closest a sandbox gets
to a phone is full device emulation — a 390-pixel viewport, a touch screen,
a mobile Safari user agent — which is exactly what the new
`smoke_phase6.mjs` drives through headless Chromium with the fake
microphone: eight checks, from "no horizontal overflow" through "tap starts
recording" to "backgrounding the tab produces the honest stop". Everything
the emulator *cannot* answer — the silent switch, low-power mode, how
loudspeakers couple to a phone mic — is written out as a fifteen-minute
human checklist rather than claimed.

## How to use it

```bash
# one-time setup (unchanged from Phase 5)
cd backend && uv sync
uv run python scripts/download_model.py              # Whisper (~490 MB)
uv run python scripts/download_translation_models.py # offline MT (~900 MB)
uv run python scripts/download_tts_voices.py         # offline voices (~190 MB)

# every day
bash start.sh            # → http://localhost:3000
```

Nothing about the two-person flow changed: open the page, choose your pair,
share the invite link. On a phone you now have one new move — *Add to Home
Screen* puts the app on your launcher. Two behaviors are worth knowing
before they surprise anyone: backgrounding the tab while recording *stops*
the recording on purpose (that's the feature — tap the mic to continue),
and the screen is asked to stay awake only while the mic is open, so a
conversation normally survives the phone dozing between sentences.

For developers: `node frontend/scripts/smoke_phase6.mjs` runs the emulated
phone against a live server (needs `npx playwright install chromium` once);
the human checklist for physical devices — Android Chrome, iPhone Safari,
and a two-phone room — is `docs/testing.md §13b`, ready to run in about
fifteen minutes.

## What we measured (and what we didn't)

The emulated-phone smoke passed all eight checks against the live server,
including the two that matter most: the platform tag shows up end-to-end
(`/health` reported `{"ios-safari": 1}` from the emulated hello), and
hiding the tab mid-recording produced the honest banner instead of a zombie
recording. Nothing regressed: the solo smoke still passes 10/10, the
two-page room smoke 12/12, the two-participant live E2E 4/4, the
translation E2E 3/3, the silence guard passes, the backend sits at 177
passed + 1 documented xfail, and the frontend at 123/123 with clean
typecheck, lint and build.

What we did *not* measure, honestly: a physical phone. Every claim above is
"verified under device emulation in headless Chromium", and the checklist
that turns it into hardware truth is written but not yet run — the silent
switch on an iPhone, Chrome's battery saver, two phones sharing a table.
Those rows stay open (KI-004, KI-015, KI-018) until a human runs them, and
the app is built so that the worst answer they can give is the one it
already gives: a clear message and a stopped mic, never a silent one.

Phase 6 is the phase where carrying the app in your pocket stopped being a
-demo and started being the intended use. Next come the meeting rooms —
that's Phase 7, Zoom first.

## The QA round — the handshake learned to save itself (v0.6.1)

The independent QA pass over this phase came back *conditional*: every
compatibility behavior above verified clean against the deployed app, with
one HIGH defect — and it was a good catch, because it sat exactly where
this phase lives. On a phone, networks don't fail politely. When the
connection dropped in a dead zone, the app honestly said *connection
lost* and started reconnecting. But the reconnect's very first `hello`
traveled into the same dead zone and was never seen again. The client
only ever said hello once per socket. Worse, the server answered the
little keep-alive pings even though no hello had ever arrived — so from
the client's point of view the connection looked *alive*. Pings out,
pongs back, `Connected` never coming. Forever. Only a manual reload
escaped.

The fix (v0.6.1, ADR-0017) is one idea: **the handshake must be able to
save itself.** The client now repeats its hello on every heartbeat tick
until the server's `ready` arrives, and a watchdog recycles any socket
whose handshake stalls. The server, for its part, refuses to answer pings
before the handshake completes — silence is what tells a client its hello
never landed — re-answers a repeated hello with a fresh `ready` (same
session, same room), and closes sockets that never introduce themselves
within 15 seconds. Old app with new server heals; new app with old server
heals. No combination wedges anymore.

Measured after the fix: the smoke suite gained a ninth check — M9 holds a
real uplink blackout past the reconnect attempt and asserts the badge
returns to *Connected* — and it passes, recovery taking about five seconds
after the link returns (the QA budget was thirty). Backend 190 passed +
1 xfailed, frontend 125/125, solo and two-person regressions green, and
`/health` reports 0.6.1 after the redeploy.

## Re-verified from a clean sandbox (2026-11-07)

The QA round was closed by a full replay: the sandbox wiped every model
and the SSH toolchain, so everything was re-provisioned and the entire
battery re-run against this branch's unchanged tip. Backend 190 passed +
1 xfailed, frontend 125/125 with clean typecheck, lint and build; live:
translation E2E 3/3, TTS E2E 3/3, two-person E2E 4/4, synthetic 3/3,
silence guard PASS, smokes 10/10 / 12/12 / 9/9 — the ninth check being
the blackout-recovery regression — and `/health` still reports 0.6.1.
A fresh-eyes skim of the handshake paths found nothing to fix: the QA
Journal has no remaining open findings, and the only open items stay the
honest ones — the physical-device rows (KI-004/015/018) waiting for a
human, and the accepted limitations (KI-003/012).
