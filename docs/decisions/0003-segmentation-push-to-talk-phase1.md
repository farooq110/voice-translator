# ADR-0003 — Phase 1 utterance segmentation: manual push-to-talk; VAD deferred to Phase 2

- **Status:** Accepted (2026-10-31)
- **Context:** STT needs utterance boundaries. Options for Phase 1: (a) build
  VAD now, (b) fixed-interval chunking, (c) manual start/stop (push-to-talk).

- **Decision:** (c) manual push-to-talk. The user taps the mic to open an
  utterance, taps again to stop; the client sends `flush` and the server
  transcribes the buffered audio. Server-side VAD, silence detection and
  continuous hands-free mode are Phase 2 deliverables (master prompt
  roadmap), with the 30 s auto-flush cap already bounding memory.

- **Reasons:**
  - explicit user intent gives clean utterance boundaries with zero VAD
    tuning risk — recognition quality is not damaged by premature
    segmentation (master prompt §9 warns against excessive segmentation);
  - provides an honest, measurable baseline (RTF/latency) to compare Phase 2
    VAD improvements against;
  - keeps Phase 1 scope tight and verifiable.

- **Consequences:** users must tap to speak in Phase 1 (documented in the UI
  and README); long silence while the mic is open is buffered up to the 30 s
  cap then auto-flushed (bounded, not perfect — improved by VAD in Phase 2).
