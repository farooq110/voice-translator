# ADR-0006 — STT language pinned to the user's native language + hallucination guards

- **Status:** Accepted (2026-10-31, user-reported bugfix round)
- **Context:** KI-006 (user report): with Urdu selected as native language,
  transcripts sometimes appeared in Hindi/Persian-style script or as random
  digits, and transcription felt 2–3 s slow. Root causes found:
  1. the hello config's `native_language` was stored but **never passed to
     STT** — decoding ran with automatic language detection, which reliably
     mislabels short Hindustani-register utterances (measured: `hi` p=0.70
     on our Urdu fixture; KI-002) and, on silence/noise, hallucinates random
     languages and digit strings;
  2. every flush paid a full language-detection pass over the audio, and the
     default temperature ladder can re-decode a noisy utterance up to 6×;
  3. 2 s of pure silence measured **447 characters of hallucinated
     pseudo-Urdu in 1594 ms** — a segment-level filter alone does not catch
     all hallucinations;
  4. the UI showed nothing between "stop" and the transcript, making the
     wait feel longer.

- **Decision:** layered fix, all within Phase 1 scope (no VAD, no new deps):
  1. **Pin the language:** the WS layer derives a hint from
     `hello.config.native_language` (primary subtag, `"ur-PK"` → `"ur"`) and
     passes it as `language_hint` to `STTProvider.transcribe_pcm`;
     faster-whisper decodes with `language=hint` — the user's explicit
     selection is authoritative for their own mic audio. `None` (auto-detect)
     remains available and is exercised by real-model tests for the Phase 3
     path (the *other* participant's audio).
  2. **Dead-audio gate:** peak amplitude < 1e-4 (≈ -80 dBFS) is rejected
     before the model is invoked and surfaces as `no_speech_detected`.
  3. **No-speech segment filter:** segments with `no_speech_prob > 0.6` AND
     `avg_logprob < -1.0` (Whisper's own rule) are dropped; a fully filtered
     result is reported as `no_speech_detected`, never as an empty bubble.
  4. **Bounded decode latency:** greedy `beam_size=1` with a single
     `temperature=0.0` (no retry ladder).
  5. **Perceived latency:** the UI shows "Transcribing…" (status line +
     mic-button spinner) between flush and result.

- **Alternatives rejected:** switching to the `small` model (measured in
  KI-002: still labels `hi`, even outputs Devanagari, ~2× slower); enabling
  faster-whisper's internal `vad_filter` (that is Phase 2 scope — the
  dead-audio gate covers the measured failure without segmentation);
  post-hoc language re-routing of wrong-script output (treats symptoms,
  keeps the 2–3× decode cost).

- **Consequences:** language labels are deterministic (`language` = the
  configured language, `language_probability` = 1.0, `low_confidence` = false
  on the forced path); STT decode measured ~41–45% faster (e.g. Urdu fixture
  1252→745 ms in-process; live `processing_ms` 999→599); silence can no
  longer produce text. If the user speaks a language other than the selected
  one, output follows the selection — the selector is the source of truth
  (visible in the UI). Phase 3's decision engine still receives honest
  auto-detect signals for the other participant via `language_hint=None`.
