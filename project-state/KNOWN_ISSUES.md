# KNOWN ISSUES

Format: description / reproduction / root cause / fix / verification / status.

---

## KI-001 — No server-side VAD yet (by design, Phase 2 scope)

- **Description:** Phase 1 uses manual push-to-talk. Silence while the mic is
  open is buffered (bounded at 30 s → auto-flush).
- **Reproduction:** open mic, stay quiet 30 s → a `nothing_to_transcribe`-free
  empty/silence transcription attempt occurs at the cap.
- **Root cause:** VAD is explicitly a Phase 2 deliverable (master prompt §17).
- **Fix:** planned Phase 2 (Silero VAD / energy-based segmentation + hands-free mode).
- **Verification:** pending Phase 2 measurements (before/after latency + accuracy).
- **Status:** OPEN (planned scope, not a defect).

## KI-002 — Whisper labels short Urdu utterances as Hindi (`hi`) — ur/hi confusion

- **Description:** Urdu speech is transcribed *almost perfectly* in Arabic
  script, but `language` may come back `hi` (p≈0.70–0.78) on short utterances.
- **Reproduction:** run `uv run pytest -m stt_real` → `test_urdu_language_label_is_ur`
  xfail; or stream `tests/fixtures/ur_greeting.wav` via `scripts/e2e_synthetic_check.py`.
- **Root cause:** Whisper model behavior — Urdu and Hindi are registers of the
  same spoken language (Hindustani); short clips lack distinguishing cues.
  Measured on `base` AND `small` (`small` even outputs Devanagari script) —
  model size alone does not fix it.
- **Fix (planned, Phase 3):** the translation decision engine must not trust
  the label blindly for the ur/hi pair: candidates are script inspection of
  the transcription (Arabic vs Devanagari — reliable), utterance-context
  tracking, and configurable language sets (e.g. treat {ur,hi} as one
  detection cluster when native_language ∈ {ur,hi}).
- **Verification:** xfail test in place; will flip to strict when fixed.
- **Status:** OPEN (documented; drives Phase 3 design).

## KI-003 — Buffered audio is discarded on disconnect

- **Description:** if the WebSocket drops while recording, un-flushed audio is
  lost; the user repeats the utterance after reconnection.
- **Reproduction:** start recording, kill the server, observe: no transcription
  after reconnect; capture stopped by design (client handles `reconnecting`).
- **Root cause:** deliberate privacy posture — no audio retention client- or
  server-side beyond the active utterance.
- **Fix:** accepted for Phase 1; revisit buffering policy in Phase 5
  (network-interruption scenario) with user privacy as a constraint.
- **Status:** OPEN (accepted limitation).

## KI-004 — Real-device behavior untested (background tab, lock screen, iOS)

- **Description:** mobile OSes throttle/suspend capture in background tabs and
  on lock screens; iOS Safari quirks (AudioContext sampleRate, interruptions)
  are unverified. No compatibility claims are made (master prompt §6).
- **Reproduction:** n/a — cannot be tested in the sandbox (no real devices).
- **Root cause:** Phase 6 scope.
- **Fix:** Phase 6 test matrix (Android Chrome, desktop Chrome, iOS Safari).
- **Status:** OPEN (planned Phase 6).

## KI-005 — RESOLVED: initial TTS fixtures unusable for ur/es

- **Description:** the sandbox's default TTS voices (Chinese-oriented)
  produced unintelligible Urdu (detected as Thai) and heavily accented
  Spanish (detected as English).
- **Root cause:** voice language coverage, not the STT engine.
- **Fix:** replaced with Microsoft edge-tts **neural** voices
  (`ur-PK-Asad`, `es-ES-Alvaro`, `en-US-Aria`); detection/transcription now
  correct (see docs/testing.md §4).
- **Verification:** `uv run pytest -m stt_real` — 3 passed + 1 xfail.
- **Status:** RESOLVED (recorded for provenance of fixture choice).
