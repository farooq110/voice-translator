# KNOWN ISSUES

Format: description / reproduction / root cause / fix / verification / status.

---

## KI-001 — No server-side VAD yet (by design, Phase 2 scope)

- **Description:** Phase 1 uses manual push-to-talk. Long silences while the mic
  is on are buffered (bounded at 30 s, then auto-flushed).
- **Root cause:** VAD is explicitly a Phase 2 deliverable (master prompt section 17).
- **Fix:** planned Phase 2 (Silero VAD / energy-based segmentation).
- **Status:** OPEN (planned, not a defect).

## KI-002 — Urdu STT quality on the `base` model is expected to be limited

- **Description:** Whisper `base` is weak for Urdu compared to `small`/`medium`;
  accuracy must be validated with real speech on real devices.
- **Root cause:** model capacity trade-off for CPU-only latency budget.
- **Fix:** configurable via `STT_MODEL` env (tiny/base/small); evaluate `small` when
  real-device latency budget is known. Re-evaluate in Phase 2/6.
- **Status:** OPEN (tracked; see docs/testing.md for measured numbers).

## KI-003 — Mid-utterance network loss discards the buffered audio

- **Description:** If the WebSocket drops while recording, buffered (unflushed)
  audio is discarded; the user must speak again after reconnection.
- **Root cause:** client-side capture buffer is intentionally not persisted
  (privacy: avoid unnecessary audio retention).
- **Fix:** acceptable for Phase 1; document in UI hint. Revisit in Phase 5
  (network reconnection scenario testing).
- **Status:** OPEN (accepted limitation).

## KI-004 — Platform background-tab / screen-lock behavior untested

- **Description:** Mobile browsers throttle/暂停 audio capture in background tabs
  and on lock screens; behavior is undocumented until tested on real devices
  (Phase 6 targets Android Chrome / iOS Safari / Desktop Chrome).
- **Status:** OPEN (planned Phase 6; must not be claimed to work).
