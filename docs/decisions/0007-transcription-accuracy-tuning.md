# ADR-0007 — Transcription accuracy: `small` model, beam decoding, Silero VAD, client-side sentence endpointing

- **Status:** Accepted (2026-10-31, user-reported bugfix round; supersedes the
  "rejected alternatives" of ADR-0006, which were deferred while latency was
  the active complaint)
- **Context:** KI-007 (user report): after the KI-006 fix the transcript now
  appears in the selected native language, but the *content* is often wrong —
  "writing completely weird things straying away from the sentences I am
  speaking". Root causes found (all measured, see docs/testing.md §5):
  1. **Model tier too weak for Urdu.** `base` (74 M params) gets the
     *forced language label* right but garbles content: on the ur fixture it
     produced `اسلام علیکم … میں بلکل تک ہوا شکریا` (broken greeting, drifted
     words). Urdu is a low-resource language for Whisper; `base` sits below
     the usable-accuracy threshold for it.
  2. **Greedy decoding cannot recover from a derailment.** `beam_size=1` +
     single `temperature=0.0` means once the decoder leaves the correct
     hypothesis mid-sentence there is no fallback path back.
  3. **No VAD anywhere in the pipeline.** Leading/trailing room noise and
     breaths inside the buffered utterance are decoded like speech and come
     back as invented words mixed into an otherwise correct sentence.
  4. **Blob-shaped utterances.** The mic streams until the user taps stop (or
     the 30 s cap auto-flushes): one long buffer mixes several sentences with
     the silences between them, decodes slower, and the cap can cut a word in
     half. Whisper is measurably stronger on clean, sentence-sized segments —
     the user explicitly asked for it to "understand sentences".

- **Decision:**
  1. **Default model `base` → `small`** (`STT_MODEL`, config `stt_model`).
     Measured on the ur fixture (2 vCPU, int8): correct text
     `السلام علیکم، آج اب کیسے ہے؟ …` vs base's garble, RTF 0.32 (≈ 0.6–1 s
     decode for a typical 2–3 s sentence). `small` was already in the HF
     cache; `download_model.py` default updated to match.
  2. **Beam search with Whisper's standard quality gates:** `beam_size=5`,
     `temperature=[0.0 … 1.0]` ladder, `patience=1.0`,
     `compression_ratio_threshold=2.4`, `log_prob_threshold=-1.0`,
     `no_speech_threshold=0.6`. The ladder retries only segments that fail
     the gates, so typical-case latency is unchanged (+~15% vs greedy
     measured); worst case is bounded and preferable to confident nonsense.
     Configurable via `STT_BEAM_SIZE` / `stt_beam_size`.
  3. **Silero VAD filter inside faster-whisper** (`vad_filter=True`,
     `min_silence_duration_ms=500`, `speech_pad_ms=150`): non-speech is
     trimmed *before* decoding, so noise cannot be decoded into words and the
     decode budget is spent on speech. Configurable via `STT_VAD_FILTER` /
     `stt_vad_filter`.
  4. **Client-side sentence endpointing (frontend hook):** while the mic is
     open, the per-chunk RMS gate detects a ≥ 800 ms pause after ≥ 1.2 s of
     buffered audio and issues a server `flush` — each sentence is transcribed
     as a complete, pause-bounded unit *while the user keeps speaking*; a
     20 s hard cap bounds a continuous monologue. The stop button still
     performs the final flush (skipped when the endpointer already closed the
     segment, avoiding a spurious `nothing_to_transcribe`).

- **Alternatives rejected:** `medium` model (RTF > 1 on 2 vCPU — misses the
  real-time bar); `batched` inference pipeline (extra memory on a 2 GB-RAM
  budget, no accuracy gain); server-side Silero segmentation ahead of Phase 2
  (the client energy gate achieves the sentence segmentation with zero new
  dependencies; server VAD remains the Phase 2 deliverable for hands-free
  mode); `initial_prompt` in Urdu (risks prompt-repetition hallucinations).

- **Consequences:** transcripts arrive as sentence-sized bubbles while
  talking (perceived latency = decode time only, no stop-then-wait);
  accuracy on Urdu goes from "often garbled" to fixture-correct, and real
  noisy-mic audio benefits most from beam+VAD. Decode cost rises ~3× vs
  `base`-greedy (RTF 0.11 → 0.32 measured) — accepted as the accuracy/latency
  trade-off the user explicitly asked for ("fix this accuracy"); the
  `STT_MODEL`/`STT_BEAM_SIZE`/`STT_VAD_FILTER` env knobs allow dialing back
  on weaker hardware. Peak RSS rises (small int8 ≈ 700 MiB process total) —
  still within the 4.1 GB sandbox.
