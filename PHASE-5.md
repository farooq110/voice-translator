# Phase 5 — the browser MVP, finished: two people, one conversation

*This file lives on the `phase-5` branch and tells the story of Phase 5 only —
what it does for the two people using it, how it was built, how to use it, and
what the numbers actually were. The whole-product overview lives in `README.md`
on `main`; the Phase 3 and Phase 4 stories are `PHASE-3.md` and `PHASE-4.md` on
their own branches. Design reasoning: [ADR-0014](docs/decisions/0014-two-participant-rooms.md).*

## What Phase 5 actually is

Until now the app was a mirror with one face: one microphone, one transcript,
one voice. Everything worked — but only for the person who configured it.
Phase 5 is the moment the second person walks in.

Here is how it feels to use. You open the app, pick your languages, and the
page now shows you an invite code — six letters and numbers, something like
`TTQJRJ` — with a **copy invite link** button next to it. You send that link
to the person across the table (or across the world). They tap it, their
browser asks for the microphone, and that's it: no account, no download, no
settings screen. Their page quietly reads *"You do not need to configure
anything — the translator set the languages: Urdu ↔ English"* and shows a
small status line that goes from *waiting for the other participant* to a
quiet green *"Both participants connected"* when you're both live.

From then on it's a conversation. You speak Urdu; the other side hears a
steady voice saying it in English — coming from **their** speaker, not yours.
They answer in English; the Urdu voice plays on **your** device. Both of you
see the same transcript, each line wearing a small chip that says whose words
they are, with the translation underneath and the usual honest chips when a
language is a third one ("pass-through · third language") or a detection was
uncertain. When one of you speaks Spanish or Arabic — anything outside the
configured pair — the other person hears *your actual voice*: the server
relays the captured audio as-is instead of pretending a machine could speak
it better. Nothing is invented, nothing is regenerated.

## The shape underneath

The backend gained a small, deliberately boring module — `app/ws/rooms.py` —
that does nothing but keep track of who is in which room. A room holds at
most two WebSocket connections. The host creates theirs the moment they say
hello (so the invite code is ready before they've finished picking
languages); the guest presents the code to claim the second seat. If both of
you lose the network at once, the room doesn't vanish: it lingers for two
minutes (`ROOM_LINGER_SECONDS`), and whoever reconnects first simply says
"it's me again" with the same code and takes their seat back. After that the
code honestly dies — a stranger holding an old link gets a clear
*"room not found — ask for a new invite"*, not silence.

The protocol grew without breaking anything. Version 1 is still version 1:
a hello can now say which role it wants (`host` or `guest`) and which room it
belongs to; a `peer` event whispers *someone joined* / *someone left*; and
three of the familiar events (`transcription`, `translation`, `audio`) now
optionally carry a `speaker` tag so your browser knows whose sentence it's
looking at — which matters more than it sounds, because both of you could
happily generate an utterance called `c_0001`. Transcripts and translations
are shared with both sides; the spoken audio is not. That asymmetry is the
heart of the phase, straight from the master prompt: your speech is never
translated back at you. The voice you hear through your speaker belongs to
the other person.

For third languages there's a new `relay` event, and it's worth pausing on.
The master prompt is adamant that original audio passes through untouched —
no TTS may ever re-speak your Spanish. So the server takes the trimmed speech
region it already decoded from (16 kHz PCM16) and forwards it to the peer
verbatim, one metadata frame followed by one binary frame, exactly the
pairing pattern the Phase 4 voices already use. Your browser plays it through
the same never-overlapping queue. It's the most honest kind of translation:
the original one.

One more thing lives in this phase and it's the least glamorous and the most
important: the **feedback-loop guard**. Picture the loop — your device plays
their translated voice, your microphone hears that playback, transcribes it,
translates it back, their device plays *that*, and suddenly the two of you
are trapped in a machine conversation nobody asked for. The server can never
start this loop (it only ever listens to microphone uplink and never
re-ingests what it sends out — the tests assert it). But a laptop's
microphone sitting next to its own loudspeaker can. So while your device is
playing something that came from the *other* person — a voice clip or a
relay — the client simply stops feeding the uplink, and keeps it stopped for
a 300-millisecond tail after the sound ends. Your own translations (the solo
mode you already know) never trigger this; the guard only arms for remote
sound. The browser's echo canceller still does its job underneath; the guard
is the deterministic backstop for the devices where AEC has a bad day.

## How to use it

```bash
# one-time setup (unchanged from Phase 4)
cd backend && uv sync
uv run python scripts/download_model.py              # Whisper (~490 MB)
uv run python scripts/download_translation_models.py # offline MT (~900 MB)
uv run python scripts/download_tts_voices.py         # offline voices (~190 MB)

# every day
bash start.sh            # → http://localhost:3000
```

Open the page, choose your pair, copy the invite link, send it. One knob is
new in the environment: `ROOM_LINGER_SECONDS` (default 120) — how long an
empty room waits for a reconnecting friend. Two details worth knowing before
someone asks: deleting a sentence bubble removes it from *your* screen only
(sharing deletions is a deliberate non-goal for this phase), and changing the
host's languages takes effect on the next connection, same as it always has.

For developers: `backend/scripts/e2e_phase5_check.py` runs a real two-person
conversation against a live server — two WebSocket connections, real speech
fixtures, real models — and `frontend/scripts/smoke_phase5.mjs` drives two
real browser pages (host and invited guest) through headless Chromium.

## What we measured (and what we didn't)

On the 2-vCPU sandbox, the live two-person E2E passed all four of its
scenarios. The host's Urdu became an English voice on the guest's side
(RMS 0.185 — clearly audible, first clip paying the documented ~1.5 s voice
load); the guest's English came back as Urdu on the host's side (RMS 0.172);
the Spanish fixture crossed as a 112 KB relay of original audio with zero
translation and zero synthesis anywhere; and the same-language case (both
speaking Urdu) routed each voice to the correct opposite side. Per utterance,
a small ping-pong probe proved the speaker's own stream carries *nothing but
transcript and translation* — the guarantee that translated audio reaches
only the person it was made for. The full test round: backend 171 passed +
1 documented xfail, frontend 101/101 with clean typecheck, lint and build,
the Phase 3 translation E2E still 3/3, the silence guard still passing, and
the two-page browser smoke 12/12 with zero console errors — while the old
solo smoke still passes 10/10 untouched.

What we did *not* measure, honestly: real laptop speakers next to real
microphones. The loop guard is unit-tested and the architecture makes the
loop server-side impossible, but the acoustics of a phone on a table in a
noisy room belong to Phase 6, and no claim is made until then. Rooms are
in-memory — a server restart ends every conversation, and that's a Phase 10
problem. And the guard, by design, will drop the start of anything you say
*while* the other side's voice is playing on your machine; that trade is
written down, not hidden.

Phase 5 is the first version of this app that is genuinely for two people.
Everything after this — real devices, meeting rooms, the extension — builds
on it.
