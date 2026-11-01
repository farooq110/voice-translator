# ADR-0011 — Utterance-id error attribution + true push-to-talk mode

Date: 2026-11-01 · Status: Accepted · Inputs: independent Phase 2 QA report
(BUG-001, BUG-002, OBS-005), user instruction "See the QA report and fixed
the app"

## Context

The independent QA audit of Phase 2 (repo @ `6fc37f8`) confirmed two
Low-severity defects:

- **BUG-001** — the client attributed every flush outcome to "the most
  recent flush" (a single `lastFlushHadSpeechRef` + a shared counter). Under
  server load, outcomes arrive out of order: (a) a speech flush answered
  `no_speech_detected` after a newer silence-only drain skipped the settle
  path → the pending counter leaked → the `Transcribing…` indicator stuck
  forever; (b) a `server_busy`/`stt_timeout` for a silence-only drain
  settled unconditionally → the indicator cleared while a real speech decode
  was still in flight. Root cause: error events carry no utterance reference.
- **BUG-002** — the hands-free toggle changed only the status wording; with
  it OFF the engine still auto-flushed at every pause, while the label
  promised push-to-talk. ADR-0009 had deliberately kept endpointing on in
  both modes; the UI over-promised.

The audit also recommended re-wording `server_busy` (the buffered audio is
consumed by design — there is nothing to re-flush) and adding a queue-depth
hint (OBS-005).

## Decision

1. **Protocol v1 gains two OPTIONAL correlation fields (additive,
   non-breaking, version stays 1):**
   - `flush.utterance_id` — client-generated id (`[A-Za-z0-9_-]{1,64}`);
   - `error.utterance_id` — echoed on every flush-scoped error
     (`nothing_to_transcribe`, `server_busy`, `no_speech_detected`,
     `stt_timeout`, `stt_error`). `transcription.id` becomes the echoed
     client id when one was provided.
   The server keeps generating its own `u_NNNN_hex` ids for
   server-initiated auto-flushes (no client id available).
2. **Client settles strictly by id:** a per-flush `Map<id, hadSpeech>`
   (`FlushTracker`) replaces the shared ref + counter. The Transcribing…
   indicator is DERIVED ("any registered speech flush not yet settled"), so
   a stuck state is structurally impossible: every entry is removed by its
   own outcome or by `clear()` on connection loss. Silence-only outcomes
   (gate rejections, drains, backpressure drops) settle quietly and never
   raise a banner; `no_speech_detected` for a flush the client heard speech
   in is surfaced (unchanged policy, now attributed correctly).
3. **True push-to-talk when the toggle is OFF** (the better product outcome
   per the audit): pause-based and soft-cap auto-flushes are suppressed; the
   whole press becomes ONE utterance transcribed when the user taps stop
   (the 700 ms tail-capture window still applies — KI-008). A generous
   20 s safety cap bounds the buffer below the server's 30 s cap so the
   client always cuts cleanly with trailing silence. The toggle and status
   wording now describe the real behavior ("Push-to-talk recording…").
   This partially supersedes ADR-0009's "endpointing stays on in both
   modes": hands-free keeps endpointing v2 unchanged; PTT intentionally
   trades segment size for the classic press-to-transcribe contract.
4. **`server_busy` semantics re-worded** (audit recommendation): the message
   says the segment was dropped and includes a queue-depth hint
   (`1/1 in flight`, `queue 16/16`); the protocol spec's client obligation
   changes from "re-flush after a short wait" to "do not re-flush — keep
   speaking".

## Consequences

- The mis-attribution defect class is closed by construction; both audit
  reproduction modes are covered by unit tests (`flushTracker.test.ts`)
  and by backend echo tests (`test_ws_flow.py`, `test_ws_vad_backpressure.py`).
- Version skew (new frontend + pre-BUG-001 backend) breaks flushes outright
  (`invalid_message` — the old server forbids the new field). Accepted:
  the app is served by the same FastAPI process (ADR-0005), so client and
  server always ship together; the reconnect path also clears in-flight
  state.
- PTT presses longer than 20 s are cut at the cap (as in pre-Phase-2
  behavior); typical presses are far shorter.
- `.env` untracked (OBS-003), smoke script + screenshots re-provisioned into
  git (OBS-004) — recorded in the QA-fix round, no ADR needed.
