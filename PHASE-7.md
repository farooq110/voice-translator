# Phase 7 — the app learns to sit in a Zoom meeting

*This file lives on the `phase-7` branch and tells the story of Phase 7
only — how a Zoom meeting's audio reaches the translator, how it was
built, how to use it, and what the numbers actually were. The
whole-product overview lives in `README.md`; the earlier stories are
`PHASE-3.md`, `PHASE-4.md`, `PHASE-5.md` and `PHASE-6.md` on their own
branches. Design reasoning:
[ADR-0019](docs/decisions/0019-zoom-rtms-bridge.md).*

## What Phase 7 actually is

Everything before this phase had one assumption baked in: the microphone
is *yours*. Phases 1–6 built a translator that captures a browser mic,
and Phase 5 let two browsers share one conversation. But a meeting is
different — the conversation lives inside Zoom, and no browser tab is
holding the microphone. Phase 7 is the phase where the translator stops
needing your microphone at all and learns to listen to the meeting
itself.

The mechanism is Zoom's **Real-Time Media Streams (RTMS)**: the official
WebSocket media plane Zoom built for exactly this class of app (live
captions, notetakers, translation). Nobody joins the meeting as a bot —
the meeting host starts the stream, Zoom's webhooks tell our backend,
and the backend connects to Zoom's signaling and media sockets and
receives the meeting's mixed audio as raw PCM at 16 kHz — byte-for-byte
the format the pipeline has consumed since Phase 1. No new codec, no
resampler, no new heavy dependency.

The design rule that kept the phase small is one sentence: **the bridge
orchestrates; the pipeline stays the product.** The bridge does not
transcribe, decide, translate or speak. It speaks the app's OWN
WebSocket protocol — hello as a room's host with the configured language
pair, binary PCM uploads, explicit flushes — into the very endpoint a
browser uses. Silero VAD gates the meeting noise exactly like phone
noise. The decision engine passes the Arabic greeting through untouched
and translates the Urdu sentences exactly like a two-person call. Piper
speaks the translation into a normal two-participant room, where the
only listener is the human who joined with the invite link. Every
verified behavior of the previous six phases applies to meeting audio
without a single change to the engine.

## The shape underneath

Four small modules under `backend/app/zoom/`. `signing.py` is pure
crypto: the join signature Zoom's handshake demands, the webhook
validation challenge answer, and the v0 signature check that rejects
forged webhook deliveries. `rtms.py` is the two-phase WebSocket client
(signaling handshake → media socket → media handshake → ready-ack) with
the keep-alive responder Zoom requires on both sockets and a graceful
`STREAM_CLOSE_REQ` on the way out. `pipeline_client.py` is the
protocol-v1 client of our own `/ws/audio` — reconnecting, re-helloing
with its room code, heartbeat-driven. `bridge.py` runs one worker per
meeting: a meeting-audio endpointer (RMS pause detection plus a hard
soft cap — calmer than the browser's, because a meeting mix is noisy),
a bounded drop-on-full queue that can never stall the RTMS keep-alive
loop, and the webhook router that dedupes Zoom's retries. `router.py`
mounts `POST /zoom/webhook` (acknowledging 200 before any slow work —
Zoom retries slow webhooks and a duplicate RTMS connection kicks the
first) and `GET /zoom/meetings` (the live meetings with their invite
codes).

What the phase deliberately does NOT do: nothing is sent back INTO the
meeting. RTMS has no per-listener addressing — a caption or a voice
injected into the meeting reaches everyone, which would break the
product's oldest routing rule ("the translated voice reaches only the
listener"). In a two-person browser room that rule is enforceable; in an
N-person meeting it is not, so v1 delivers the translation to the
translator user's own device — which is what the product has always
promised. Meeting-facing captions stay an honest open question
(KI-031).

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

The browser app works exactly as before — nothing in the UI changed.
The Zoom part needs a Zoom **General App** (marketplace.zoom.us) with
RTMS access and event subscriptions, whose credentials go into backend
env vars (`ZOOM_ENABLED=true`, `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`,
`ZOOM_SECRET_TOKEN`) — the full setup walk-through is
[docs/deployment.md §6](docs/deployment.md). Once a meeting's RTMS
stream starts, the meeting appears at `GET /zoom/meetings` with an
invite link; open it on your phone or desktop and the meeting's speech
arrives as a live, speaker-tagged, translated transcript with spoken
translations on your device.

For developers without Zoom credentials, the whole loop is exercisable
locally: `app/zoom/fake_rtms.py` is a signature-verifying fake of
Zoom's edge, and `backend/scripts/e2e_zoom_check.py` drives the real
server through signed webhooks, the fake RTMS handshake, a real Urdu
fixture as "meeting audio", and a real guest client — the run this
phase shipped with passed 5/5 in 17.9 s.

## What we measured (and what we didn't)

Backend **220 passed + 1 xfailed** (30 new Zoom tests: signature
vectors, webhook enforcement, endpointer behavior, RTMS protocol edges,
the bridge-worker integration, the HTTP surface). Frontend untouched:
**139/139**, typecheck/lint/build clean. Live on :3000: the Zoom E2E
**5/5** — url_validation challenge, worker live + invite published,
RTMS handshake (signature verified by the fake, L16/16 kHz/mono
negotiated), the 6.6 s Urdu fixture producing three utterances that
reached the guest as a speaker-tagged transcript, a real ur→en OPUS-MT
translation and 111,618 bytes of routed PCM (the greeting's original
audio relayed, the translations spoken) — then a graceful stop with the
close request observed and `/health`'s zoom section back to zero. The
full regression battery held: translation E2E 3/3, two-participant 4/4,
TTS 3/3, silence guard PASS, smokes 10/10 / 12/12 / 9/9.

What we did *not* measure, honestly: a real Zoom meeting. Every RTMS
claim above is "verified against a fake of Zoom's side, built from
Zoom's published protocol reference" — the handshake shapes Zoom does
not document in full are handled defensively and listed as the
real-Zoom verification points (KI-029). The human checklist that turns
them into truth is `docs/deployment.md §6`. Meeting-audio endpointing
is a measured knob, not a promise (KI-030), and nothing is injected
into the meeting (KI-031) — the worst thing the bridge can do to a
meeting is nothing at all.

Phase 7 is the phase where "the conversation" stopped meaning "two
browsers" and started meaning "wherever people talk". The extension and
the remaining meeting apps come next — that's Phase 8 and 9.
