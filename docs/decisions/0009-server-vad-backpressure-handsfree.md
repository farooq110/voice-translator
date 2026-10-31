# ADR-0009: Server-side Silero VAD, bounded STT admission, hands-free mode

Date: 2026-10-31 · Phase: 2 (Voice Activity Detection and Audio Optimization) ·
Status: accepted

## Context

Phase 1 (with ADR-0006/0007/0008) delivers push-to-talk + live sentence
endpointing with good accuracy, but master prompt §9/§17 Phase 2 still
requires the server side to handle audio properly:

- Every flush was decoded unconditionally — the STT's dead-audio gate
  (peak < -80 dBFS) and decode-time VAD caught the worst cases, but the
  model/encoder still ran for any audible noise, and there was no
  server-side structure for segmentation.
- The `ThreadPoolExecutor` queue is unbounded: a burst of flushes (or many
  sessions) could queue dozens of 30 s utterances — unbounded memory growth
  and ever-staler results, exactly what §9 forbids.
- Continuous (hands-free) capture existed de facto (ADR-0007 endpointing)
  but was not an explicit, toggleable mode, and long idle periods produced
  confusing "no speech detected" banners when silence-only buffers hit the
  20 s drain cap.

## Decision

**1. Server-side VAD pipeline (gate → trim → segment), before STT.**
`backend/app/audio/vad.py` wraps the Silero v6 ONNX **bundled inside
faster-whisper's package assets** (no new dependency, no download, ~2 MB,
loaded lazily in the executor like the Whisper model):

- *Gate* — buffers with < `VAD_MIN_SPEECH_MS` (120 ms) of detected speech are
  answered `no_speech_detected` without ever invoking STT (asserted by test:
  0 provider calls). This replaces the Phase 1 dead-audio gate as the primary
  defense and covers real-world noise, not just digital silence.
- *Trim* — the buffer is cut to its speech region ± `VAD_TRIM_PAD_MS`
  (200 ms), so Whisper decodes speech, not silence; `audio_duration_s`
  continues to report the pre-trim length the user actually spoke.
- *Segment* — buffers whose speech span exceeds `VAD_SPLIT_MAX_SECONDS`
  (24 s) or containing pauses ≥ `VAD_SPLIT_SILENCE_MS` (700 ms) are split
  into at most `max_chunks` (8) pieces, decoded sequentially, texts joined.
  This is a bounded-decode safety net for clients without endpointing;
  over-segmentation is avoided by design (700 ms pauses are sentence
  boundaries, not intra-word events).
- Kill switch: `VAD_GATE_ENABLED=false` makes the pipeline an exact
  pass-through (asserted: no model load, byte-identical chunk).

**2. Bounded STT admission (backpressure) instead of an unbounded queue.**
`backend/app/ws/backpressure.py` adds a process-global `SttQueue`
(`STT_MAX_QUEUE_DEPTH=16`) plus a per-session pending cap
(`STT_MAX_PENDING_PER_SESSION=4`). A flush beyond either cap is answered
with a **non-fatal `server_busy`** error; the buffer is consumed (bounded)
and the client may re-flush. Acquire/release bracket the whole
transcribe-and-deliver task (released even on timeout/error — regression
tested). Cancellation semantics stay honest: `asyncio.wait_for` cancels the
*await*, the CPU thread finishes its current chunk; stale results after
close are dropped (existing check), and shutdown uses
`executor.shutdown(cancel_futures=True)`.

**3. Hands-free mode as an explicit, feature-flagged toggle (frontend).**
`VITE_FEATURE_HANDS_FREE` (default ON) gates a checkbox in the capture panel;
`handsFree` persists to `localStorage('vt.hands_free')`. Engine behavior is
identical in both modes (endpointing + tail capture from ADR-0007/0008 are
what fixed KI-007/KI-008 — they are not mode-dependent); the mode changes
the *contract with the user*: wording ("Hands-free listening…"), and
silence-drain handling — a flush whose segment contained no client-observed
speech (`speechSeen=false`, e.g. the 20 s idle cap) still drains the server
buffer but must not surface `no_speech_detected` (`noSpeechPolicy.ts`,
unit-tested) nor show the Transcribing indicator. A flush containing
observed sound that the server gates still surfaces — honest feedback.

**4. Uplink backpressure (client).** `wsClient.sendAudioChunk` drops audio
chunks while `ws.bufferedAmount > 1 MiB` (~32 s of audio), counting them
(`droppedChunkCount`); control JSON (flush/ping) always passes. Audio loss
under genuine congestion beats unbounded buffering; the heartbeat already
detects dead sockets.

**5. Settings reach the endpoint via `app.state.settings`.** The WS
previously read `get_settings()` (lru_cache) directly, silently ignoring
injected settings — found by the new backpressure tests and fixed.

## Alternatives considered

- *Energy-only server gate (RMS thresholds)* — no new ONNX runtime cost, but
  cannot distinguish speech from broadband noise and would mis-fire on
  mic AGC differences; Silero is speech-specific and already paid for
  (bundled, ~10–13 ms per flush measured).
- *Bounded ThreadPoolExecutor queue* — not supported by stdlib without a
  custom queue; an admission counter at the WS layer is simpler, testable,
  and gives the client a meaningful error instead of silent queuing.
- *Protocol version bump for `server_busy`* — not needed: it is a new
  non-fatal error *code* inside the existing error event; v1 clients that do
  not know the code still render the message generically.
- *Hands-free OFF disables endpointing (classic PTT)* — rejected: endpointing
  is integral to the KI-007/KI-008 accuracy fixes; regressing it per mode
  would reintroduce two user-reported bugs.

## Consequences

- Silence/noise flushes cost ~13 ms (Silero ONNX) instead of a decode
  dispatch; hallucination defense no longer depends on the STT internals.
- `proc_ms` on blob-shaped inputs (E2E fixtures) rises (3 sequential decodes)
  — documented in testing.md §6; real-app sentence buffers are unaffected.
- Silero ONNX adds ~170 MiB RSS to the server (measured 1233 MiB resident).
- New env vars: `VAD_GATE_ENABLED`, `VAD_THRESHOLD`, `VAD_MIN_SPEECH_MS`,
  `VAD_TRIM_PAD_MS`, `VAD_SPLIT_SILENCE_MS`, `VAD_SPLIT_MAX_SECONDS`,
  `STT_MAX_QUEUE_DEPTH`, `STT_MAX_PENDING_PER_SESSION`;
  frontend `VITE_FEATURE_HANDS_FREE`.
- Real-device VAD threshold tuning remains Phase 6 (KI-004); verified via
  fake-media browser smoke + fixtures (testing.md §7–§8).

## Verification (summary; full numbers in docs/testing.md)

Backend 65 passed + 1 documented xfail (new: 9 planner, 5 real-Silero,
7 WS/backpressure, 1 full-pipeline real model); frontend 33 passed +
typecheck/lint/build clean; live E2E 3/3 PASS (en/es/ur through the VAD
pipeline); silence guard PASS; browser smoke 10/10 with fake mic;
bench_vad.py recorded.
