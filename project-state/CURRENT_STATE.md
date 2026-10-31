# CURRENT_STATE

_Last updated: 2026-10-31 — Phase 2 complete (implementation + verification)_

## Phase

**Phase 2 — Voice Activity Detection and Audio Optimization** — implementation,
verification and documentation COMPLETE. Phase 1 (+ user-reported defect rounds
KI-006/007/008) remains complete. Awaiting the user's explicit approval for
Phase 3 (master prompt §23).

## Current task

None — Phase 2 finished: server-side Silero VAD (gate → trim → segment),
bounded STT admission/backpressure, hands-free mode behind a feature flag,
before/after measurements, sandbox-scope real-device proxy testing, docs and
project-state updated, all committed.

## Completed work (Phase 2, this round)

- **P2-01** `backend/app/audio/vad.py`: Silero VAD (ONNX bundled with
  faster-whisper — no new dependency) — gate (< 120 ms speech →
  `no_speech_detected` before STT, asserted 0 provider calls), trim (speech
  region ± 200 ms; honest pre-trim duration reported), segment (24 s span cap /
  ≥ 700 ms pauses → ≤ 8 bounded decodes, texts joined). Kill switch
  `VAD_GATE_ENABLED=false` = exact pass-through (tested).
- **P2-03** `backend/app/ws/backpressure.py`: global 16 / per-session 4 decode
  admission caps → non-fatal `server_busy`; exception/timeout-safe release
  (regression-tested); stale-result dropping verified; client uplink drops
  chunks > 1 MiB buffered (`droppedChunkCount`); WS endpoint now reads
  `app.state.settings` (injectability bug found & fixed).
- **P2-02** Hands-free mode behind `VITE_FEATURE_HANDS_FREE` (default ON):
  toggle + `vt.hands_free` persistence + "Hands-free listening…" status;
  silence-drain flushes no longer spam `no_speech_detected` banners
  (`noSpeechPolicy.ts`); endpointing + tail capture stay on in both modes.
- **P2-04** `scripts/bench_vad.py`: gate 10–13 ms (any buffer type);
  silence/noise gated without dispatching decode work; trim accuracy-neutral
  (identical ur text); RSS +~170 MiB (Silero) — docs/testing.md §5a.
- **P2-05** `scripts/smoke_phase2.mjs`: headless Chromium with Chrome's fake
  microphone — 10/10 checks (render/connect/toggle/persistence/status wording/
  no banners/no console errors; screenshots `download/phase2-screens/`);
  manual desktop-Chrome checklist documented (docs/testing.md §8); full
  real-device matrix stays Phase 6 (KI-004).
- Protocol: new non-fatal `server_busy` code documented; VAD semantics +
  backpressure + client obligations added to `docs/audio-protocol.md`.
- Health: `/health` now reports `vad` + `stt_queue` blocks.
- Docs: ADR-0009; architecture/deployment/README updated; TASKS P2 rows DONE;
  KI-001 RESOLVED; DECISIONS D-009..D-011 (incl. backfilled 0007/0008);
  CHANGELOG 0.1.2 (backfill) + 0.2.0.
- **Backup:** the complete Phase 1 state was zipped (user request) to
  `download/Phase-One.zip` (2.3 MB, 759 files, integrity-verified, includes
  git history; excludes regenerable `.venv`/`node_modules`/model cache —
  manifest inside the zip).

## Verified work (evidence)

- `backend`: `uv run pytest` → **65 passed, 1 xfailed** (xfail = documented
  KI-002 auto-detect behavior) — includes 9 VAD-planner, 5 real-Silero,
  7 WS/backpressure, and a full-pipeline real-model test (ur speech over
  `/ws/audio` through the real gate → Arabic script).
- `frontend`: `tsc --noEmit` ✓ · `eslint --max-warnings 0` ✓ · `vitest run` →
  **33 passed** · `vite build` ✓.
- Live server (restart via `bash start.sh`): `/health` 200 with
  `vad.enabled=true, vad.loaded=true`, `stt_queue {queued:0,max:16}`;
  version 0.2.0.
- `scripts/e2e_synthetic_check.py` → **3/3 PASS** (en/es/ur through the VAD
  pipeline; all texts correct; `processing_ms` 3047–4315 — blob fixtures hit
  the segmentation safety net, real-app sentence buffers do not).
- `scripts/silence_guard_check.py` → **PASS** (2 s digital silence →
  `no_speech_detected` from the server-side Silero gate).
- Browser smoke → **10/10 PASS** with the fake microphone.
- Git history: one commit per coherent work block.

## Actual test results

See `docs/testing.md` §2–§9 (full tables, updated 2026-10-31 after ADR-0009).

## Known issues

`project-state/KNOWN_ISSUES.md`: KI-001 RESOLVED (this round); KI-002
mitigated (auto-detect only; Phase 3 design input); KI-003 accepted;
KI-004 OPEN (real devices — Phase 6; manual checklist provided);
KI-005/006/007/008 RESOLVED.

## Blockers

None.

## Next task

**Phase 3 — Translation Decision Engine and Bidirectional Translation** —
NOT started; requires the user's explicit approval of the Phase 2 completion
report.

## Phase approval required?

**YES** — per master prompt §23 the Phase 2 completion report ends with the
question _"The current phase has been completed and reviewed. Do you approve
starting the next phase?"_; no Phase 3 work may begin until the user answers
affirmatively.
