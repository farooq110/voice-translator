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
client ← {"v":1,"type":"ready","session_id":"s_…","audio":{…},"stt":{…}}
```

`hello.config`:

| Field | Type | Notes |
| --- | --- | --- |
| `native_language` | string | ISO-639-1, e.g. `"ur"` — translator user only. **Pinned as the STT language** (primary subtag, `"ur-PK"` → `"ur"`) — see ADR-0006 |
| `preferred_language` | string | ISO-639-1, e.g. `"en"` — translator user only |
| `audio.encoding` | `"pcm_s16le"` | only supported value |
| `audio.sample_rate` | int | must equal 16000 |
| `audio.channels` | int | must equal 1 |

`ready.stt`: `{provider, model, device, loaded}` — transparency about the engine.
(The server also exposes `vad` and `stt_queue` state on `GET /health`.)

Violations: binary before `hello` → fatal `hello_required`; second `hello` →
non-fatal `hello_already_sent`; identical languages → non-fatal
`identical_languages` warning (pass-through semantics apply from Phase 3).

## 3. Steady state

```
client → binary PCM16 chunk (typ. 100 ms = 3 200 bytes)
client → {"v":1,"type":"flush"}                  # end of utterance
client ← {"v":1,"type":"transcription", …}       # result (see below)
client → {"v":1,"type":"ping","ts":123}          # heartbeat (client, 20 s cadence)
client ← {"v":1,"type":"pong","ts":123}
```

`transcription` fields:

| Field | Meaning |
| --- | --- |
| `id` | utterance id (`u_NNNNNN_hex`) |
| `text` | transcription (empty string never sent; use `nothing_to_transcribe` / `no_speech_detected`) |
| `language` | Phase 1: the configured native language (primary subtag) — the STT language is **pinned** to the user's selection, not auto-detected (KI-006 fix, ADR-0006) |
| `language_probability` | 0..1; always `1.0` when the language is pinned from hello config |
| `audio_duration_s` | utterance length in seconds |
| `processing_ms` | server wall time from flush receipt to event send |
| `is_final` | always `true` in v1 (partials come with streaming in a later phase) |
| `low_confidence` | `language_probability < LOW_CONFIDENCE_THRESHOLD` (default 0.60) — always `false` on the pinned path |

Phase 1+ semantics: every received utterance is transcribed with the STT
language **pinned** to `hello.config.native_language` (the translator user's
selection is authoritative; auto-detection mislabels short utterances and
hallucinates on noise — KI-006). Since Phase 2 (ADR-0009) every flush first
crosses the **server-side Silero VAD pipeline**:

1. *Gate* — no recognizable speech (< `VAD_MIN_SPEECH_MS`) →
   `no_speech_detected` WITHOUT calling STT (~13 ms, not decode-scale);
2. *Trim* — the buffer is cut to its speech region ± 200 ms before decoding;
   `audio_duration_s` still reports the pre-trim length the user spoke;
3. *Segment* — speech spans > `VAD_SPLIT_MAX_SECONDS` (24 s) or pauses ≥
   `VAD_SPLIT_SILENCE_MS` (700 ms) split the utterance into bounded decodes;
   texts are joined in order into one `transcription` event.

Segments that look like classic no-speech hallucinations are additionally
dropped by the STT provider (KI-006/ADR-0007). The translation decision
engine (Phase 3) will use `language` + `low_confidence` + the hello config to
decide translate vs pass-through for **both** participants; v1 already
carries all required signals.

**Backpressure (ADR-0009):** at most `STT_MAX_PENDING_PER_SESSION` (4)
utterances per session and `STT_MAX_QUEUE_DEPTH` (16) globally are admitted
for decoding at once. A flush beyond either cap is answered `server_busy`
(non-fatal); the buffered audio is consumed (bounded memory) and the client
may re-flush later.

## 4. Errors

```json
{"v":1,"type":"error","code":"…","message":"…","fatal":false}
```

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
| `server_busy` | no | **Phase 2 backpressure (ADR-0009):** per-session or global STT admission cap reached — the flush was consumed, retry shortly |
| `stt_timeout` | no | inference exceeded `STT_TIMEOUT_SECONDS` |
| `stt_error` | no | provider failure (connection stays open) |

Fatal errors are followed by a WebSocket close (1008 policy violation, 1013
capacity, 1001 idle).

## 5. Client obligations

- Send `hello` exactly once, before any binary frame.
- Keep chunks ≤ 256 KiB (the reference client sends 100 ms chunks).
- Heartbeat with `ping` every 20 s; reconnect on missing `pong` (10 s) — the
  reference client reconnects with capped exponential backoff (600 ms → 10 s).
- Handle `server_busy` by re-flushing after a short wait (the reference
  client surfaces it as a non-fatal notice; audio chunks keep flowing).
- The reference client additionally drops outbound audio chunks while its
  socket send buffer exceeds 1 MiB (uplink backpressure; counted in
  `droppedChunkCount`) — control messages are never dropped.
- Buffered-but-unflushed audio is **discarded** on disconnect (privacy: no
  unnecessary audio retention).
