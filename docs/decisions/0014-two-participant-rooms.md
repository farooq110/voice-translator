# ADR-0014: Two-participant rooms, recipient-only audio routing and original-audio relay (Phase 5)

Date: 2026-11-04 · Status: accepted · Phase: 5

## Context

Phase 4 delivered translated TEXT and SPOKEN output on the translator user's
device. The master prompt (§17) Phase 5 completes the browser MVP: the
pipeline now ends in **Audio Routing → Browser Playback** for TWO people in
two browsers, with the standing rules intact — only the translator user
configures languages, the system "must not translate a participant's speech
back to that same participant unnecessarily", pass-through audio is forwarded
**as captured** (never regenerated), and feedback loops must not occur.

Constraints inherited from Phases 1–4: local CPU only, offline by default,
versioned WebSocket protocol evolved additively (ADR-0011/0012/0013
pattern), bounded resources everywhere, honest degradation, and the
established solo behavior (one mic → transcript → translation → local voice)
must keep working unchanged for the existing single-user flow.

## Decision

1. **Rooms, not user accounts.** A room ties exactly two WebSocket
   connections: the **host** (translator user) creates it at `hello` and
   owns the language pair; the **guest** joins with a 6-character
   unambiguous invite code (no I/L/O/0/1) and configures NOTHING — its
   decisions are governed by the ROOM's config. `RoomManager`
   (`app/ws/rooms.py`) is plain in-memory bookkeeping (dict + linger
   timers, single event loop); each connection keeps its own Session, VAD
   and STT pipeline, so the room layer cannot touch audio buffers.
   Rooms linger `ROOM_LINGER_SECONDS` (default 120 s) after the last member
   leaves — the reconnection window: a dropped client re-hellos with the
   same code and takes its slot back; after the linger the room is
   destroyed and the code dies (a guest gets an honest `room_not_found`).

2. **Protocol v1 stays v1 (additive).** `hello.config` gains optional
   `role` (`"host"` default — the Phase 1–4 shape) and `room`; the language
   fields become optional strings (required, validated, for hosts).
   New server→client events: `peer` (`joined`/`left` + role + peers) and
   `relay` (metadata + ONE binary PCM16 frame at the capture rate — the
   same pairing contract as `audio`). `transcription` / `translation` /
   `audio` / `error` gain an optional `speaker: host|guest` tag, present
   only in room mode; solo connections keep the exact Phase 1–4 wire shape
   (null fields are omitted — absent ≡ null, documented). New error codes:
   `room_required`, `room_not_found`, `room_full`, `missing_languages`
   (all fatal — retrying a dead room never succeeds, so the client stops
   its reconnect loop instead).

3. **Recipient-only audio routing.** Synthesized speech for an utterance is
   delivered to the room members EXCLUDING the speaker — "translated audio
   reaches only the intended recipient" (scenario 11) and "never translate
   a participant's speech back at them". A solo room (no guest yet) falls
   back to the speaker — byte-for-byte the Phase 1–4 local-playback
   behavior. Transcripts and translations are SHARED: both participants see
   every line tagged with its speaker; translation failures are shared
   utterance-scoped errors, while TTS failures go to the same recipients
   the audio would have reached. STT-level errors stay speaker-local.

4. **Original-audio relay for pass-through.** A `passthrough` decision
   (third language) relays the utterance's VAD-trimmed speech region
   (16 kHz PCM16, as captured — never regenerated, never re-spoken) to the
   peer as a `relay` metadata + binary pair. The speaker never receives
   their own relay (they ARE the original; replaying it would be a feedback
   loop). Solo rooms relay nothing (no peer to hear it). With translation
   disabled no decision exists and nothing is relayed.

5. **Feedback-loop prevention is two-layered.** Server-side: delivered
   audio is never re-ingested — the server only ever transcribes client
   uplink (architectural). Client-side: a `PlaybackGuard` drops the
   device's uplink chunks while a REMOTE clip (a peer's voice or a relay)
   is scheduled to be audible, plus a 300 ms tail — so the microphone
   cannot re-capture the loudspeaker and start a translation loop even
   where browser AEC is weak. Own-utterance TTS (the solo local playback)
   never arms the guard: muting the user's own mic while their own
   translation plays would break solo hands-free conversation. Guests'
   "you don't configure anything" is enforced by the UI (no language
   selectors) and by the server (guest language fields are ignored).

## Alternatives considered

- **User accounts / persistent rooms in a database:** unnecessary for the
  browser MVP (rooms live one conversation long); in-memory + linger is
  simpler, bounded, and stateless to deploy. Production hardening is
  Phase 10 scope.
- **WebRTC mesh (peer-to-peer audio):** better raw latency, but it bypasses
  the server pipeline that must remain the single place where decisions,
  translation and TTS happen (and the meeting-integration phases need
  server-side audio anyway). Rejected for Phase 5; revisit if latency
  demands it.
- **Relaying raw audio continuously (open mic):** violates the
  bounded-resource rules and the VAD work of Phase 2 — only decided,
  trimmed utterances travel.
- **`git revert`-style symmetric "replay original on speaker's device too"
  (master prompt table read literally):** the speaker hears themselves
  acoustically; replaying their own audio back adds a feedback path and no
  information. The table's intent (both participants perceive the original
  language) is satisfied: the speaker perceives it in the room, the peer
  through the relay.

## Consequences

- `/health` exposes `rooms {active, rooms}` next to `sessions`.
- Ids remain per-connection; clients key transcript entries and playback
  clips by `(speaker, id)` — a guest's `c_0001` and the host's `c_0001`
  coexist without collision. Deleting a line cancels only that line's
  local playback; line deletion is NOT synced to the peer (documented,
  KI-016).
- Changing the host's languages applies to the NEXT connection (config is
  read at hello, as in Phases 1–4); a mid-room change requires the host to
  stop and re-open (KI-017).
- The guest cannot outvote the host's pair — by design ("only the
  translator user configures").
- Latency budget: routing adds no server hops (same pipeline, one extra
  send per event); measured live in docs/testing.md §12.
