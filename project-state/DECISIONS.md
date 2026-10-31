# DECISIONS

Decision log. Detailed reasoning lives in `docs/decisions/` (ADRs).

| # | Date | Decision | Why | ADR |
| --- | --- | --- | --- | --- |
| D-001 | 2026-10-31 | Follow the master prompt stack (React+Vite+TS frontend, Python+FastAPI backend) instead of the sandbox's default Next.js scaffold | Master prompt section 7 is explicit; platform preview only needs a server on :3000, which FastAPI provides in single-port mode | ADR-0004 |
| D-002 | 2026-10-31 | STT engine: faster-whisper (CTranslate2, CPU int8) | pip-installable, GPU-free, built-in language detection across 90+ languages incl. Urdu/English, actively maintained; whisper.cpp needs native builds | ADR-0001 |
| D-003 | 2026-10-31 | Audio format: PCM16 mono @ 16 kHz over WebSocket binary frames | Whisper's native rate; 32 kB/s uplink is mobile-friendly; browser-side downsample via AudioContext(16 kHz) + AudioWorklet | ADR-0002 |
| D-004 | 2026-10-31 | Phase 1 utterance segmentation = manual push-to-talk (start/stop → flush); server-side VAD deferred to Phase 2 | Explicit user intent gives clean utterance boundaries without a half-baked VAD damaging accuracy; continuous mode is a Phase 2 deliverable | ADR-0003 |
| D-005 | 2026-10-31 | Single-port serving: FastAPI serves the built SPA + WebSocket on :3000 | No CORS surface, one process for the platform preview, simple deployment | ADR-0005 |
| D-006 | 2026-10-31 | STT runs in a bounded ThreadPoolExecutor (default 1 worker) off the event loop | Master prompt: never block the event loop with CPU-heavy work; serializes model access safely | ADR-0001 |
| D-007 | 2026-10-31 | Test fixtures use Microsoft edge-tts neural voices (ur-PK-Asad / es-ES-Alvaro / en-US-Aria) | Sandbox default TTS voices produced unintelligible Urdu/Spanish (KI-005); neural voices are native-quality real speech, keeping the real-STT tests honest | docs/testing.md §4 |
| D-008 | 2026-10-31 | STT language pinned to the hello config's `native_language` (not auto-detected) + dead-audio gate + no-speech segment filter + single-temperature greedy decode | User report (KI-006): auto-detect mislabels Urdu as Hindi/Persian and hallucinates digits; pinning is deterministic AND 41–45% faster (skips the detection pass); silence previously decoded into 447 chars of pseudo-Urdu | ADR-0006 |
