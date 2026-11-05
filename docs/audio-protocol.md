# Audio WebSocket Protocol — v1

Status: **stable for Phase 2** · Endpoint: `GET /ws/audio` (WebSocket) ·
Code: `backend/app/models/protocol.py` (authoritative), `frontend/src/types/protocol.ts` (mirror)

Any breaking change must bump the protocol version (`v`) and add a section here.

## 1. Transport

- One WebSocket = one **session** (one participant; two-person pairing arrives in Phase 5).
- Control messages: JSON **text** frames.
- Audio: raw **binary** frames — PCM16 little-endian, **mono, 16 kHz** (32 000 bytes/s).
- Limits (server-enforced, configurable in `backend/.env.example`):

| Limit | Default | Behavior on violation |
| --- | --- | --- |
| `MAX_CHUNK_BYTES` | 256 KiB | chunk dropped, non-fatal `chunk_too_large` |
| `MAX_UTTERANCE_SECONDS` | 30 s | **auto-flush** when the buffer reaches the cap |
| `MIN_UTTERANCE_SECONDS` | 0.2 s | `nothing_to_transcribe` on flush below the floor |
| `WS_IDLE_TIMEOUT_SECONDS` | 180 s | server closes (`idle_timeout`) |
| `MAX_SESSIONS` | 50 | connect rejected (`session_limit_reached`) |

## 2. Handshake

```
client → {"v":1,"type":"hello","config":{...}}     # MUST be the first message
client ← {"v":1,"type":"ready","session_id":"s_…","audio":{…},"stt":{…},"room":{…}}
```

`hello.config`:

| Field | Type | Notes |
| --- | --- | --- |
| `role` | string | **Phase 5 (ADR-0014):** `"host"` (default — the Phase 1–4 shape) or `"guest"`. Only the host configures languages. |
| `room` | string | **Phase 5:** the invite code (4–10 chars `A-Z0-9`). A guest MUST present it; a host may re-send its own code to REJOIN within the linger window. |
| `native_language` | string | ISO-639-1, e.g. `"ur"` — **host only, required for hosts**. **Pinned as the STT default** (primary subtag, `"ur-PK"` → `"ur"`) — see ADR-0006. Guests send neither language (the room's pair governs — the host's). |
| `preferred_language` | string | ISO-639-1, e.g. `"en"` — host only, required for hosts. |
| `audio.encoding` | `"pcm_s16le"` | only supported value |
| `audio.sample_rate` | int | must equal 16000 |
| `audio.channels` | int | must equal 1 |

`ready.stt`: `{provider, model, device, loaded}` — transparency about the engine.
`ready.room` (Phase 5, present for every host/guest connection):
`{code, role, peers, native_language, preferred_language}` — the room's
invite code, this connection's role, how many OTHER participants are
connected, and the ROOM's language pair (the host's). Solo clients from
older builds tolerate the extra field.

Violations: binary before `hello` → fatal `hello_required`; second `hello` →
non-fatal `hello_already_sent`; host languages missing → fatal
`missing_languages`; identical host languages → non-fatal
`identical_languages` warning (pass-through semantics apply from Phase 3);
guest without a code → fatal `room_required`; unknown/expired code → fatal
`room_not_found`; both slots taken → fatal `room_full` — UNLESS the seat's
owner is a ghost (stale-seat eviction, below).

### 2a. Rooms and presence (Phase 5, ADR-0014)

Every host connection owns a room; a guest joins the room named by its
invite code. The room holds AT MOST two participants (a third connection
gets `room_full`). Membership changes are announced to the OTHER members:

```json
{"v":1,"type":"peer","event":"joined","role":"guest","peers":1}
{"v":1,"type":"peer","event":"left","role":"host","peers":0}
```

`peers` is the number of OTHER members from the recipient's perspective.
When the last member leaves, the room lingers `ROOM_LINGER_SECONDS`
(default 120 s): a dropped client re-hellos with the SAME code and takes
its slot back (the host stays the config authority); after the linger the
room is destroyed and the code dies. Rooms are in-memory and single-process
(a restart ends every room — Phase 10 scope).

**Stale-seat eviction (QA round, BUG-002, ADR-0015).** A proxy can swallow
a dead client's disconnect, keeping a seat occupied by a GHOST. The server
tracks the last RECEIVED frame per connection; when a hello finds its own
seat occupied by a member that has sent nothing for
`ROOM_STALE_SEAT_SECONDS` (12 s default), the seat is reclaimed: the ghost
receives a fatal `seat_taken` error and is closed server-side, the peer
gets the normal `peer left` event, and the returning participant takes the
seat. A LIVE occupant (frames within the threshold) is never evicted — a
genuinely occupied room still answers fatal `room_full`.

## 3. Steady state

```
client → binary PCM16 chunk (typ. 100 ms = 3 200 bytes)
client → {"v":1,"type":"flush","utterance_id":"c_0001_ab12cd"}   # end of utterance
client ← {"v":1,"type":"transcription", …}       # result (see below)
client → {"v":1,"type":"ping","ts":123}          # heartbeat (client, 20 s cadence)
client ← {"v":1,"type":"pong","ts":123}
```

`utterance_id` on `flush` is **optional** (added in the BUG-001 fix,
ADR-0011): a client-generated correlation id (`[A-Za-z0-9_-]{1,64}`) that
the server echoes as the `transcription.id` and on every flush-scoped
`error.utterance_id`, so the client can settle exactly the utterance a
result belongs to. Server-initiated auto-flushes omit it and the server
generates its own id (`u_NNNN_hex`).

`transcription` fields:

| Field | Meaning |
| --- | --- |
| `id` | utterance id — the client's echoed `utterance_id`, or a server-generated `u_NNNN_hex` for auto-flushes |
| `text` | transcription (empty string never sent; use `nothing_to_transcribe` / `no_speech_detected`) |
| `language` | Phase 1: the configured native language (primary subtag) — the STT language is **pinned** to the user's selection, not auto-detected (KI-006 fix, ADR-0006) |
| `language_probability` | 0..1; always `1.0` when the language is pinned from hello config |
| `audio_duration_s` | utterance length in seconds |
| `processing_ms` | server wall time from flush receipt to event send |
| `is_final` | always `true` in v1 (partials come with streaming in a later phase) |
| `low_confidence` | `language_probability < LOW_CONFIDENCE_THRESHOLD` (default 0.60) — always `false` on the pinned path |
| `decision` | **Phase 3 (ADR-0012, optional — present only when server translation is enabled):** `{action, reason, source_language, target_language?, detection}`. `action="translate"` is followed by a `translation` event with the same id; `action="passthrough"` means NO translation will arrive (the original is final). Reasons: `native_language`, `preferred_language`, `third_language`, `identical_config`, `low_confidence`, `undetected_language`, `unsupported_pair`, `text_too_long`. `detection` records how `source_language` was resolved: `whisper` (model label), `cluster` (ur/hi register collapse, KI-002), `pinned` (hello config), `fallback` (uncertain → native pin), `unknown` |

Phase 1–2 semantics: the STT language was **pinned** to
`hello.config.native_language` (KI-006). **Phase 3 (ADR-0012) default `auto`
mode:** a standalone `detect_language` pass resolves the spoken language
(ur/hi cluster + 0.60 confidence gate), the SINGLE decode is then pinned to
the resolved language (never auto-decoded — KI-002/KI-006), and the event
reports the RESOLVED language. `translation_detect_mode=pinned` restores the
Phase 1/2 behavior (the configured native language, probability 1.0). Since Phase 2 (ADR-0009) every flush first
crosses the **server-side Silero VAD pipeline**:

1. *Gate* — no recognizable speech (< `VAD_MIN_SPEECH_MS`) →
   `no_speech_detected` WITHOUT calling STT (~13 ms, not decode-scale);
2. *Trim* — the buffer is cut to its speech region ± 200 ms before decoding;
   `audio_duration_s` still reports the pre-trim length the user spoke;
3. *Segment* — speech spans > `VAD_SPLIT_MAX_SECONDS` (24 s) or pauses ≥
   `VAD_SPLIT_SILENCE_MS` (700 ms) split the utterance into bounded decodes;
   texts are joined in order into one `transcription` event.

Segments that look like classic no-speech hallucinations are additionally
dropped by the STT provider (KI-006/ADR-0007).

### 3a. Translation decision + `translation` event (Phase 3, ADR-0012)

After every transcription the decision engine applies the master prompt's
rules to the resolved detection:

| Detected language | Result |
| --- | --- |
| native (e.g. `ur`) | `translate` → preferred (`en`) — Rule 1 |
| preferred (e.g. `en`) | `translate` → native (`ur`) — Rule 2 |
| any third language (e.g. `es`, `ar`) | `passthrough` — original kept, provider never called — Rule 3 |

**Room mode (Phase 5, ADR-0014):** the rules are applied against the ROOM's
pair (the host's config) for BOTH participants — the guest configures
nothing. `transcription` and `translation` events carry an optional
`speaker: "host"|"guest"` (present only in room mode; solo connections keep
the exact Phase 1–4 shape) and are BROADCAST to every room member — both
sides see the whole conversation. Utterance ids are unique per CONNECTION:
clients key entries by `(speaker, id)`.

A `translate` decision is executed inside bounded limits (sliding-window 30
translations/min, ≤2 concurrent, 8 s timeout, dedicated worker thread) and
answered by a `translation` event:

```json
{"v":1,"type":"translation","id":"c_0001_ab12cd","text":"Hello, how are you?",
 "source_language":"ur","target_language":"en","provider":"local-opus-mt","duration_ms":144}
```

`id` is the transcription's id (the echoed `utterance_id`). Provider
failures/timeouts/rate-limits arrive instead as non-fatal `error` events
(below) — the client keeps the original text and shows a per-line warning.
The default `local` provider translates OFFLINE (CTranslate2 OPUS-MT models
under `models/translation/`); `libre` sends transcript TEXT (never audio) to
the configured LibreTranslate instance; `fake` (deterministic stub) and
`none` (no decisions) complete the settings matrix.

### 3b. Synthesized speech: `audio` event + binary frame (Phase 4, ADR-0013)

When the server has TTS enabled (`TTS_PROVIDER=piper|fake`), every successful
`translation` is followed by its spoken version — the translated text is
synthesized OFFLINE (Piper neural voices, no GPU, no keys) and delivered as
TWO frames in order:

```json
{"v":1,"type":"audio","id":"c_0001_ab12cd","language":"en",
 "encoding":"pcm_s16le","sample_rate":22050,"channels":1,
 "bytes":113664,"duration_ms":2577,"synthesis_ms":546,"provider":"piper"}
```

followed by exactly ONE BINARY WebSocket frame of `bytes` octets: raw PCM16
little-endian mono at `sample_rate` (server voices run at 22 050 Hz).
WebSocket ordering guarantees the pairing; the client pairs the next binary
frame it sees with the most recent `audio` metadata (and ignores binary
frames without pending metadata).

Rules:

- `id` equals the `transcription`/`translation` id for the same utterance.
- Only `translate` decisions produce audio — **original pass-through is
  NEVER regenerated with TTS** (master prompt §17).
- Synthesis is bounded like translation: sliding-window 30/min, ≤1
  concurrent, `TTS_TIMEOUT_S` (10 s) timeout, dedicated worker thread.
- A missing voice for the target language, a provider failure, a timeout or
  a rate-limit hit answers a non-fatal utterance-scoped `error` instead —
  the translated TEXT is always kept; only the audio is lost.
- With `TTS_PROVIDER=none` no `audio` events and no server→client binary
  frames exist at all (the protocol keeps the exact Phase 3 shape).

**Backpressure (ADR-0009):** at most `STT_MAX_PENDING_PER_SESSION` (4)
utterances per session and `STT_MAX_QUEUE_DEPTH` (16) globally are admitted
for decoding at once. A flush beyond either cap is answered `server_busy`
(non-fatal); the buffered audio is consumed and **dropped by design** —
there is nothing for the client to re-flush. The message carries a
queue-depth hint (e.g. `queue 16/16`) and the echoed `utterance_id`
(ADR-0011), so the client can settle that utterance and simply keep
speaking.

### 3c. Original-audio pass-through relay (Phase 5, ADR-0014)

For a `passthrough` decision in a room WITH a peer, the utterance's ORIGINAL
captured audio (the VAD-trimmed speech region) is relayed to the OTHER
participant exactly as captured — never regenerated with TTS (master prompt
Rules 3+4), never replayed to the speaker. Framing is the same
metadata + ONE binary frame pair as `audio`, at the capture rate:

```json
{"v":1,"type":"relay","id":"c_0001_ab12cd","speaker":"host","language":"es",
 "encoding":"pcm_s16le","sample_rate":16000,"channels":1,
 "bytes":112644,"duration_ms":3520}
```

The peer pairs the next binary frame with this metadata and plays it through
the same sequential, overlap-free playback queue. Solo rooms (no peer yet)
and translation-disabled deployments send NO `relay` events.

**Routing summary (Phase 5):** transcripts, translations and their errors
are SHARED (both members); synthesized voices and relays go ONLY to the
other participant; STT-level errors stay with the speaker. The server never
re-ingests delivered audio (feedback loops are impossible server-side); the
reference client additionally mutes its uplink while REMOTE audio is
audible (+300 ms tail — `PlaybackGuard`, ADR-0014).

## 4. Errors

```json
{"v":1,"type":"error","code":"…","message":"…","fatal":false,"utterance_id":"c_0001_ab12cd","speaker":"host"}
```

`speaker` (Phase 5) is present when an utterance-scoped error is delivered
to a room member OTHER than the speaker (shared translation outcomes; the
TTS errors that reach the audio's recipients). Null fields are omitted on
the wire — absent ≡ null (additive convention, ADR-0014).

`utterance_id` (ADR-0011, optional) correlates the error with the flush that
caused it; it is `null`/absent for connection-level errors and
server-initiated auto-flushes.

| Code | Fatal | Meaning |
| --- | --- | --- |
| `unsupported_protocol_version` | yes | mismatched `v` |
| `hello_required` | yes | binary before hello |
| `unsupported_audio_format` / `unsupported_sample_rate` / `unsupported_channels` | yes | hello format mismatch |
| `session_limit_reached` | yes | server capacity |
| `idle_timeout` | yes | no frames for 180 s |
| `invalid_message` | no | schema violation (payload kept alive) |
| `malformed_json` | no | non-JSON text frame |
| `hello_already_sent` | no | duplicate hello |
| `identical_languages` | no | native == preferred warning |
| `chunk_too_large` | no | chunk dropped |
| `invalid_chunk_alignment` | no | odd byte count dropped |
| `nothing_to_transcribe` | no | flush with empty/below-floor buffer |
| `no_speech_detected` | no | audio had no recognizable speech (server Silero gate, dead silence, or hallucination-only decode) — connection stays open |
| `server_busy` | no | **Phase 2 backpressure (ADR-0009):** per-session or global STT admission cap reached — the buffered audio was consumed and DROPPED by design (nothing to re-flush); message includes a queue-depth hint (OBS-005) |
| `stt_timeout` | no | inference exceeded `STT_TIMEOUT_SECONDS` |
| `stt_error` | no | provider failure (connection stays open) |
| `translation_failed` | no | **Phase 3:** translation provider error — original transcript kept (utterance-scoped) |
| `translation_timeout` | no | **Phase 3:** translation exceeded `TRANSLATION_TIMEOUT_S` — original kept |
| `translation_rate_limited` | no | **Phase 3:** sliding-window rate limit reached — original kept |
| `tts_failed` | no | **Phase 4 (ADR-0013):** voice synthesis failed — translated text kept (utterance-scoped) |
| `tts_timeout` | no | **Phase 4:** synthesis exceeded `TTS_TIMEOUT_S` — translated text kept |
| `tts_rate_limited` | no | **Phase 4:** sliding-window rate limit reached — translated text kept |
| `tts_unsupported_language` | no | **Phase 4:** no voice provisioned for the target language — translated text kept |
| `tts_text_too_long` | no | **Phase 4:** text above `TTS_MAX_TEXT_CHARS` is not spoken — translated text kept |
| `room_required` | yes | **Phase 5 (ADR-0014):** a guest hello without an invite code |
| `room_not_found` | yes | **Phase 5:** unknown or expired room code (the room's linger expired) |
| `room_full` | yes | **Phase 5:** both room slots are taken by LIVE members (a third connection, or a host rejoin collision). A reconnecting client that owns a STALE seat is instead evicted-and-rejoined (ADR-0015); the reference client retries room_full during a RECONNECT (≤90 s) before treating it as fatal |
| `seat_taken` | yes | **QA round (ADR-0015):** this connection's seat was reclaimed by a newer connection of the same participant (this tab became a ghost) — stop reconnecting; the session continues on the newer connection |
| `missing_languages` | yes | **Phase 5:** a host hello without both language fields |

Fatal errors are followed by a WebSocket close (1008 policy violation, 1013
capacity, 1001 idle).

## 5. Client obligations

- Send `hello` exactly once, before any binary frame.
- Keep chunks ≤ 256 KiB (the reference client sends 100 ms chunks).
- Heartbeat with `ping` every **4 s** (QA round, ADR-0015 — keeps the
  server's stale-seat detector from ever seeing a live member as silent);
  reconnect on missing `pong` (10 s, armed once per unanswered ping) — the
  reference client reconnects with capped exponential backoff (600 ms → 10 s).
- Send `flush` with a unique client-generated `utterance_id` (ADR-0011) and
  settle in-flight state strictly by the echoed id — never attribute an
  outcome to a newer flush.
- Treat `translation` events as updates to the utterance with the same id;
  a `passthrough` decision means NO translation will ever arrive for that
  line; on `translation_failed` / `translation_timeout` /
  `translation_rate_limited` keep the original text and surface a per-line
  warning (never re-flush, never re-request — ADR-0012).
- Treat `audio` events (Phase 4) as the spoken version of the translation
  with the same id: pair the next binary frame with the most recent `audio`
  metadata, play clips SEQUENTIALLY (never overlapping), keep the queue
  bounded, and cancel queued audio for a deleted line, a lost connection or
  a user stop. On `tts_*` errors keep the translated text and surface a
  per-line warning (never re-request — ADR-0013).
- Rooms (Phase 5): key transcript entries and playback clips by
  `(speaker, id)` — ids collide across participants; treat `peer` events
  as presence truth; on fatal `room_*` errors STOP reconnecting (a dead
  or expired room never recovers by retrying — get a fresh invite) and arm
  the uplink mute while REMOTE audio plays (feedback-loop guard).
  EXCEPTION (QA round, ADR-0015): `room_full` during a RECONNECT is
  retried with backoff (≤90 s) while the server reclaims a stale seat;
  a first-join `room_full` stays fatal. `seat_taken` is always fatal.
- Treat `relay` events (Phase 5) like `audio`: pair the next binary frame
  with the most recent `relay` metadata and play it sequentially. The
  relay is the peer's ORIGINAL third-language audio — it is never
  re-transcribed, re-translated or regenerated client-side.
- On `server_busy`, do NOT re-flush: that segment was dropped by design —
  keep speaking; the next segment is admitted normally (the reference client
  surfaces it as a non-fatal notice; audio chunks keep flowing).
- The reference client additionally drops outbound audio chunks while its
  socket send buffer exceeds 1 MiB (uplink backpressure; counted in
  `droppedChunkCount`) — control messages are never dropped.
- Buffered-but-unflushed audio is **discarded** on disconnect (privacy: no
  unnecessary audio retention).
