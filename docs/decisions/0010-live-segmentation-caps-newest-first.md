# ADR-0010 — Live segmentation caps for continuous-speech latency; newest-first transcript

Date: 2026-11-01 · Status: Accepted · Relates to: ADR-0007 (sentence
endpointing), ADR-0008 (stop tail capture), ADR-0009 (server VAD), KI-009

## Context (user report, KI-009, 2026-11-01)

Two reports from real use of the Phase 2 build:

1. *"I am just turning on the mic and keeping on speaking … Until I pause …
   it doesn't convert my voice into text. When I pause for a little while …
   it starts giving a response. The first response comes fast, but after
   that, if I keep on speaking, it takes quite a bit of time."*
2. *"Please show new sentence at the top instead of the bottom so that I
   don't have to scroll through a long list to see the new results
   instantly."*

## Root causes

1. The ADR-0007 endpointer closes a segment **only** at a ≥ 800 ms pause —
   or at the 20 s hard cap. Continuous speech (reading aloud, narrating)
   contains short micro-pauses of 200–500 ms that never reach 800 ms, so the
   client buffered up to 20 s and then flushed one blob whose decode costs
   ~6 s (ur RTF ≈ 0.31). Measured on the live server (docs/testing.md §5b):
   22.5 s of continuous Urdu speech → **first text after 22.2 s**, one
   blob, plus a mid-word-cut fragment from the cap flush.
2. `TranscriptPanel` rendered the stored (chronological) array directly and
   auto-scrolled to the bottom on every new entry — the newest sentence was
   the farthest from the user's eyes.

## Decision

1. **Client endpointing v2** (`useTranslatorSession.ts`):
   - **Soft cap 4.5 s + micro-pause 300 ms:** once ≥ 4.5 s of audio is
     buffered, the next natural micro-pause (≥ 300 ms silence) closes the
     segment — cuts land on word gaps, not mid-word, for normal speech.
   - **Hard cap 20 s → 9 s:** a truly pauseless stretch is flushed anyway,
     bounding the worst-case blob to 9 s ≈ 3 s decode (ur RTF 0.31).
   - The sentence-pause rule (≥ 800 ms closes any segment ≥ 1.2 s) is
     unchanged — short sentences behave exactly as before.
2. **Pending-decode counter** for the `Transcribing…` indicator: flushes
   overlap once segments stream every few seconds; the indicator now clears
   when the *last* in-flight decode lands, not on the first result.
3. **Newest-first transcript panel:** `TranscriptPanel` reverses the
   chronological array for display (state order unchanged — append +
   `MAX_TRANSCRIPTS` trim); the scroll-to-bottom effect is removed because
   the newest line is now the first line.

### Alternatives rejected

- **Lowering the 800 ms sentence pause** (e.g. 500–600 ms): splits normal
  sentences at hesitations for no latency benefit — the pain was confined to
  continuous speech, which the soft cap addresses.
- **True streaming partials** (transcribe the growing buffer every few
  seconds without clearing): re-decodes overlapping audio on a 2-vCPU box
  and risks duplicated/contradictory text; overkill for the measured need.
- **Server-driven segmentation push:** protocol + UX churn; the server VAD
  (ADR-0009) already bounds decode size per flush.

## Consequences

- **Positive (measured, live server):** first text during continuous speech
  **22.2 s → 6.6 s (−70 %)**; results arrive every ~4.5 s with flush→text
  1.9–2.2 s; all segmented utterances transcribed correctly (`ur` p=1.00).
- **Trade-offs:** continuous speech longer than one sentence now arrives as
  consecutive bubbles (server VAD still trims/splits inside each flush). A
  genuinely pauseless 9 s stretch is cut mid-word (bounded, rare — natural
  speech micro-pauses well below 9 s). Hands-free idle silence drains flush
  every 9 s instead of 20 s — 13 ms server gate, zero STT work.
- **Neutral:** wire protocol and server code untouched; `MIN_SEGMENT_MS`
  (1.2 s) and `SILENCE_MS` (800 ms) unchanged.

## Verification

- Backend `uv run pytest` → **65 passed + 1 xfail** (suite unchanged, all
  green); frontend `tsc` / `eslint --max-warnings 0` / **33 vitest** /
  `vite build` clean; built bundle carries the new constants and strings.
- New live check `scripts/e2e_continuous_speech_check.py` (mirrors the hook
  state machine; streams 22.5 s of compressed-silence Urdu speech in real
  time; reads events concurrently):
  - old machine → 2 flushes, first text 22.2 s (baseline reproduced);
  - new machine → **5/5 utterances, first text 6.6 s, flush→text 1.9–2.2 s,
    all texts correct** → PASS.

## Approval per master prompt §23

This is a Phase 2 defect round on the approved Phase 2 scope (same pattern
as the Phase 1 defect rounds KI-006/007/008); Phase 3 (translation) remains
gated on the user's explicit approval.
