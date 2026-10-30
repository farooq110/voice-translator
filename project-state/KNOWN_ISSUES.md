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
  Partially arrived early via ADR-0007: the frontend hook now energy-gates
  pauses while the mic is open and flushes per sentence (client-side only —
  the server-side VAD for hands-free mode remains Phase 2 scope).
- **Verification:** pending Phase 2 measurements (before/after latency + accuracy).
- **Status:** OPEN (planned scope, not a defect).

## KI-002 — Whisper labels short Urdu utterances as Hindi (`hi`) — ur/hi confusion (AUTO-DETECT path only)

- **Description:** with *automatic* language detection (no hint), Urdu speech
  is transcribed *almost perfectly* in Arabic script, but `language` may come
  back `hi` (p≈0.70–0.78) on short utterances.
- **Reproduction:** run `uv run pytest -m stt_real` →
  `test_urdu_autodetect_label_is_ur` xfail.
- **Root cause:** Whisper model behavior — Urdu and Hindi are registers of the
  same spoken language (Hindustani); short clips lack distinguishing cues.
  Measured on `base` AND `small` (`small` even outputs Devanagari script) —
  model size alone does not fix it.
- **Fix (Phase 1 mitigation shipped, KI-006):** the production path no longer
  auto-detects for the translator user's own mic — the configured
  `native_language` is pinned as the STT language (ADR-0006). The ur/hi
  confusion therefore cannot surface for the user's own speech. The xfail
  test is kept to document the auto-detect behavior Phase 3 must not trust
  blindly for the *other* participant: candidates are script inspection of
  the transcription (Arabic vs Devanagari — reliable), utterance-context
  tracking, and configurable language sets (e.g. treat {ur,hi} as one
  detection cluster when native_language ∈ {ur,hi}).
- **Verification:** `test_urdu_forced_language_label_is_ur` passes
  deterministically (language=`ur`, p=1.00); live E2E ur→ur PASS.
- **Status:** MITIGATED for Phase 1 production path (2026-10-31); OPEN as a
  Phase 3 design input for the auto-detect path.

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

## KI-006 — RESOLVED: wrong language/script/digits in transcripts + 2–3 s latency (user report)

- **Description (user report, 2026-10-31):** with Urdu selected as native
  language, transcripts appeared in "weird languages — sometimes Hindi,
  sometimes Persian … occasionally even uncertain random numbers", and
  transcription had ~2–3 s of latency.
- **Reproduction (pre-fix):** stream `tests/fixtures/ur_greeting.wav` with
  hello `native_language=ur` → auto-detect labels it `hi` (p=0.70); stream 2 s
  of digital silence → a 447-character hallucinated pseudo-Urdu string in
  1594 ms; bench auto-mode latencies 1209–1252 ms in-process, 940–999 ms live.
- **Root cause:** (a) `hello.config.native_language` was stored but never
  passed to STT — decoding ran with automatic detection (KI-002 applies) and
  hallucinated random languages/digits on noise; (b) every flush paid a full
  language-detection pass and the default temperature ladder can re-decode up
  to 6×; (c) the UI showed nothing while decoding.
- **Fix (ADR-0006):** STT language pinned to the configured native language
  (primary subtag); dead-audio gate (peak < ≈ -80 dBFS rejected before the
  model → `no_speech_detected`); Whisper no-speech segment filter
  (`no_speech_prob > 0.6` AND `avg_logprob < -1.0`); single-temperature
  greedy decode (no retry ladder); "Transcribing…" pending indicator in the
  UI. New non-fatal error code `no_speech_detected` documented in the
  protocol spec.
- **Verification:** backend 42 passed + 1 documented xfail (incl. new hint,
  regional-subtag and no-speech regression tests); real-model tests: ur
  forced → language=`ur` p=1.00, Arabic-script ratio ≥ 0.8, dead silence →
  empty text; frontend typecheck/lint/23 tests/build clean; live E2E 3/3
  PASS with ur→ur; live silence check PASS; benchmark: forced 41–45% faster
  (ur 1252→745 ms), live `processing_ms` 940–999 → 537–600 ms.
- **Status:** RESOLVED (2026-10-31).

## KI-007 — RESOLVED: language label correct but content wrong / drifting ("weird things") (user report)

- **Description (user report, 2026-10-31):** "Now the text is appearing in
  selected native language, but it cannot write what I am speaking correctly.
  The accuracy is very poor — meaning it is writing completely weird things
  straying away from the sentences I am speaking."
- **Reproduction (pre-fix):** stream `tests/fixtures/ur_greeting.wav` with
  hint `ur` on model `base` → `اسلام علیکم آج آپ کیسے ہیں میں بلکل تک ہوا
  شکریا` — broken greeting, drifted words (the forced label is right, the
  content is not). Long mic pushes mix several sentences + silence into one
  buffer and the 30 s cap can cut mid-word.
- **Root cause:** (a) `base` (74 M params) is below the usable-accuracy
  threshold for Urdu — the label is forced but the content garbles;
  (b) greedy `beam_size=1` + single temperature cannot recover from a
  mid-sentence derailment; (c) no VAD anywhere — room noise/breaths are
  decoded into invented words; (d) blob-shaped utterances (whole push = one
  buffer) degrade accuracy and latency instead of clean sentence units.
- **Fix (ADR-0007):** default model `base` → `small` (correct text on the
  fixture, RTF 0.32 on 2 vCPU); beam search `beam_size=5` with Whisper's
  quality-gated temperature ladder; Silero VAD filter inside faster-whisper
  (`vad_filter=True`, 500 ms min silence, 150 ms pad) trims non-speech
  before decode; client-side sentence endpointing in the frontend hook — a
  ≥ 800 ms pause after ≥ 1.2 s of buffered audio flushes the sentence while
  the mic stays open (20 s hard cap), so results appear live and the stop
  button only closes the final segment.
- **Verification:** bench on the ur fixture — base-greedy garble vs
  small-beam+VAD correct `السلام علیکم، آج اب کیسے ہے؟ …`; backend suite
  green incl. real-model ur assertions; live E2E re-run (docs/testing.md
  §4–6); frontend typecheck/lint/tests/build clean.
- **Status:** RESOLVED (2026-10-31). Real-device accuracy still to be
  confirmed in Phase 6 (KI-004); `STT_MODEL`/`STT_BEAM_SIZE`/
  `STT_VAD_FILTER` allow dialing accuracy vs latency.

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
