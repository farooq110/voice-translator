# ADR-0001 — STT engine: faster-whisper (CTranslate2) on CPU

- **Status:** Accepted (2026-10-31)
- **Context:** Master prompt requires local, CPU-only speech recognition with
  automatic language detection covering Urdu + English (and more). Candidates:
  whisper.cpp (native build, GGML), faster-whisper (CTranslate2 via pip),
  Vosk (small Kaldi models, no built-in multi-language ID), cloud STT
  (rejected: privacy + not local).

- **Decision:** faster-whisper, int8 compute type on CPU, lazy model load,
  inference in a bounded worker executor off the event loop.

- **Reasons:**
  - pip-installable, no native toolchain, actively maintained;
  - Whisper's joint transcription + language detection over 90+ languages
    (ur, en, es, ar, fr, …) — the detection signal is required by the Phase 3
    decision engine anyway;
  - measured on the target-class hardware (2 vCPU): RTF 0.13–0.18 for `base`
    int8 → ~1 s for a 7 s utterance; 609 MiB peak RSS;
  - model size configurable (`tiny`/`base`/`small`) without code changes.

- **Consequences:**
  - Urdu/Hindi label confusion on short utterances is inherited from Whisper
    (KI-002) — mitigations belong to the Phase 3 decision engine;
  - whisper.cpp remains a valid swap-in: the provider interface
    (`app/stt/base.py`) isolates the engine behind `STTProvider`.
