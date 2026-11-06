# CHANGELOG

All meaningful project changes. Format based on Keep a Changelog.

## [0.6.1] — QA round: hello-handshake hardening (2026-11-07)

Input: independent QA verification report (Journal, 2026-11-07) — verdict
"CONDITIONAL" with BUG-601 (HIGH) and BUG-602 (LOW). Fixes per ADR-0017.

### Fixed
- **BUG-601 (HIGH): the client could stick in "Reconnecting…" forever when
  the reconnect hello was lost** — exactly the mobile scenario Phase 6
  targets (Wi-Fi↔LTE transitions, dead zones). Three verified factors: the
  hello was sent once per socket and never retried; the server answered
  pings BEFORE hello completed, so the client's pong-based detector never
  fired; hello-less sockets lived until the 180 s idle timeout. Now the
  handshake can save itself on both sides: the client repeats hello on
  every heartbeat tick until `ready` (idempotent re-ack from the server:
  fresh `ready`, same session and room — the `hello_already_sent` error is
  gone) and a hello-ack watchdog recycles a socket whose handshake never
  completes (10 s); the server stays silent on pre-hello pings and closes
  sockets that never say hello after `ws_hello_timeout_seconds` (15 s,
  fatal `hello_timeout`). Measured recovery after the link returns: ≈ 5 s
  (QA acceptance budget: 30 s). Old client + new server and new client +
  old server both heal — neither combination can wedge.
- **BUG-602 (LOW, deployment): stale deployment reported**
  `/health.version = 0.5.1` on a Phase 6 build. No code change needed (the
  source has carried the right version since the Phase 6 commit); the
  deploy checklist now asserts `/health.version` after every redeploy
  (docs/deployment.md §2).

### Added
- `smoke_phase6.mjs` check **M9** — a real uplink blackout (all outgoing
  frames dropped) held past the pong timeout and one reconnect attempt,
  asserting return to Connected after the link returns; the suite is now
  9 checks.
- Settings: `ws_hello_timeout_seconds` (default 15 s).

### Tests
- Backend **190 passed + 1 xfailed** (+4: duplicate hello re-acks solo AND
  in-room, pre-hello ping unanswered, hello-timeout close).
- Frontend **125/125** (+2: re-hello until ready; watchdog recycles a
  pong-only socket), tsc/ESLint/build clean.
- Live re-verification: smoke_phase6 **9/9**, smoke_phase2 **10/10**,
  smoke_phase5 **12/12**, E2E translation **3/3**; `/health.version`
  **0.6.1** after redeploy.

## [0.6.0] — Phase 6: mobile/browser compatibility (2026-11-06)

Input: master prompt §17 Phase 6 + KI-004/KI-015/KI-018. Scope decision
(ADR-0016): everything the CODE controls is implemented and verified under
full device emulation; everything that needs physical hardware is a written
step-by-step human matrix — no hardware claims from the sandbox.

### Added
- **Honest capture lifecycle.** Backgrounding the tab, locking the phone or
  an incoming call now STOPS the recording the same honest way a connection
  loss does (buffer flushed, playback and feedback guard cleared) with a
  plain-language banner — instead of streaming silence behind a fake
  "recording" state and tripping the server's idle/stale-seat logic. The
  capture AudioContext watches its own state (iOS `interrupted`).
- **Screen wake lock** while the mic is open (progressive enhancement;
  denials degrade silently).
- **PWA-lite**: manifest + icons + apple metas — installs to a phone home
  screen; pull-to-refresh can no longer reload the app mid-recording;
  form controls no longer trigger iOS focus zoom; the mic panel stays
  reachable on long transcripts; ≥ 44 px touch targets.
- **Platform telemetry**: optional `hello.config.client.platform` (coarse
  tag, never a raw user-agent) aggregated into `/health →
  sessions.by_platform` — the human device matrix gets ground truth.

### Verified
- Backend 177 passed + 1 xfailed (+7 tests); frontend 123/123 (+9), tsc /
  ESLint / build clean; `smoke_phase6.mjs` (iPhone emulation) **8/8**
  including the platform tag end-to-end and the background-tab guard;
  regressions smoke_phase2 10/10, smoke_phase5 12/12; live E2E translation
  3/3, two-participant routing 4/4; silence guard PASS.
- Real-device matrix (Android/iOS/room, ~15 min) documented in
  docs/testing.md §13b — awaiting the human hardware run.

## [0.5.1] — QA round: deployment hardening (2026-11-05)

Input: independent QA verification report (Journal, 2026-11-05) — verdict
"NOT ACCEPTED yet" with BUG-001/002/003. Fixes per ADR-0015.

### Fixed
- **BUG-001 (blocker, deployment): no usable TTS voices.** HuggingFace 429s
  on the small `.onnx.json` voice configs left `.onnx`-only directories
  that start.sh silently accepted. `download_tts_voices.py` now retries
  transient failures (5 attempts, 5–60 s backoff) and has a `--verify`
  mode; `start.sh` verify-gates provisioning before boot; the app logs a
  `TTS PROVISIONING WARNING` when TTS is enabled but voiceless, and
  `/health.tts.warning` exposes the same gap.
- **BUG-002 (high): ghost seat blocked rejoin ~180 s.** Stale-seat
  eviction: a rejoin that finds its own seat held by a member silent for
  `ROOM_STALE_SEAT_SECONDS` (12 s) evicts the ghost (fatal `seat_taken`,
  server-side close, honest `peer left`) and takes the seat back; live
  occupants are never evicted (fatal `room_full` unchanged). Client: 4 s
  heartbeat (pong deadline armed once per unanswered ping), `room_full`
  retried during reconnects (≤90 s) instead of dying, `seat_taken` fatal.
- **BUG-003 (low): fatal errors did not close the WebSocket through the
  preview proxy.** App layer verified correct on both ends (server closes
  on every fatal path — verified locally; client closes on room-fatal
  errors, pong timeout reaps half-open sockets ≤14 s); the proxy gap is
  documented (KI-021) for the production deployment.

### Tests
- Backend: +6 tests (ghost eviction host/guest, live-seat protection,
  ghost-cleanup regression guard, /health TTS warning ×2) — full suite
  green. Frontend: 101/101, tsc clean, production build OK.
- Live probe (real Whisper/MT/Piper): TTS delivery to the opposite
  participant only, solo fallback, ghost-seat reclaim after 13 s silence,
  live-seat room_full, fatal-close — 5/5 PASS.

## [0.5.0] — Phase 5: complete browser MVP — two participants (2026-11-04)

Input: user approval "start phase 5 and complete it" (2026-11-04).

### Added
- **Two-participant rooms (ADR-0014)**: the host creates a room at hello
  (6-char unambiguous invite code) and owns the language pair; the guest
  joins via the shared `?room=CODE` link and configures nothing. Rooms
  linger `ROOM_LINGER_SECONDS` (120 s default) after the last member
  leaves — dropped clients rejoin with the same code — then are destroyed.
- **Recipient-only audio routing**: transcripts + translations are
  broadcast to both members with `speaker` tags; synthesized voices reach
  ONLY the other participant (a solo room falls back to the speaker — the
  exact Phase 1–4 shape); translation failures are shared utterance-scoped
  errors; TTS failures reach the audio's intended recipients.
- **Original-audio pass-through relay**: third-language utterances relay
  the VAD-trimmed captured audio (16 kHz PCM16, metadata + one binary
  frame) to the peer — never regenerated with TTS, never replayed to the
  speaker.
- **Feedback-loop prevention**: the server never re-ingests delivered
  audio; the client `PlaybackGuard` drops uplink chunks while a REMOTE clip
  is audible (+300 ms tail). Own-utterance TTS never arms the guard.
- Frontend: invite panel (code + copy link), guest view without language
  settings, speaker chips on the shared transcript, host reconnect re-joins
  its room, room-fatal errors stop the reconnect loop.
- `/health` gains `rooms {active, rooms}`.

### Changed
- Protocol v1 (additive): `hello.config.role`/`.room`, optional language
  fields (host-validated), `peer` and `relay` events, optional `speaker`
  tags; null fields omitted on the wire (absent ≡ null); new fatal codes
  `room_required`, `room_not_found`, `room_full`, `missing_languages`.
- APP_VERSION 0.4.0 → 0.5.0; start.sh unchanged (voices provisioned since
  v0.4.0).

### Verified
- Backend 171 passed + 1 xfailed (+16 room tests); frontend 101/101 vitest,
  tsc/ESLint/build clean; live two-participant E2E 4/4 (real STT + MT +
  Piper, routed delivery proven per utterance); translation E2E 3/3;
  silence guard PASS; two-page browser smoke 12/12; solo smoke 10/10.

## [0.4.0] — Phase 4: TTS + browser audio playback (2026-11-04)

Input: user approval "start pahse 4 and complete it" (2026-11-04).

### Added
- **Offline TTS engine (ADR-0013)**: Piper voices (VITS on onnxruntime —
  already shipped for faster-whisper; no torch/keys/GPU) for Urdu
  (`ur_PK-fasih-medium`), English (`en_US-lessac-medium`) and Spanish
  (`es_MX-ald-medium`) under `models/tts/<lang>/` (~190 MB, git-ignored),
  provisioned idempotently by `start.sh` (loud failure) and by
  `backend/scripts/download_tts_voices.py`.
- **TTS provider abstraction** (`backend/app/tts/`): `piper` (default, lazy
  per-language load), `fake` (deterministic sine stub), `none`; bounded
  `TTSService` — 30 syntheses/min sliding window, ≤1 concurrent, 10 s hard
  timeout, 800-char text cap, dedicated single-thread executor.
- **Protocol v1 additive**: `audio` JSON metadata event followed by exactly
  one binary PCM16 frame (22 050 Hz) per translated utterance — sent under
  the same per-connection lock; 5 new utterance-scoped non-fatal error
  codes (`tts_failed`, `tts_timeout`, `tts_rate_limited`,
  `tts_unsupported_language`, `tts_text_too_long`); synthesis tasks
  cancelled on disconnect.
- **Browser playback (ADR-0013)**: `SpeechPlayer` — strictly sequential Web
  Audio scheduling (overlap impossible by construction), bounded queue
  (4 clips, oldest dropped, remaining re-based — no silent gaps), cancel on
  line delete, stop-all on connection loss/user stop/mute, AudioContext
  unlocked on the mic gesture; "Spoken translations" toggle (persisted),
  pulsing `speaking` chip, per-line voice-error chips, `stop voice` button.
- **/health `tts` section** (provider, languages, voices, bounds) — a
  voice-less deployment is visible (BUG-003 lesson applied to TTS).
- `scripts/e2e_tts_check.py` — live E2E: real speech → real MT → real
  synthesis with PCM RMS/duration/size assertions; pass-through must
  produce ZERO audio frames.

### Verification
Backend 155 passed + 1 xfailed (+25 tests incl. `tts_real` real synthesis);
frontend 81/81 vitest + tsc + ESLint + build 254.5 kB; e2e_tts_check 3/3
PASS live (spoken en 2 577 ms audio in 1 573 ms incl. first voice load;
spoken ur 3 424 ms in 546 ms; es pass-through bypassed TTS); translation
E2E 3/3; silence guard PASS; browser smoke 10/10.

## [0.3.1] — QA BUG-003 fix — deployment provisioning + health visibility (2026-11-03)

Input: independent Phase 3 QA report (verdict FAIL — BUG-003 HIGH: deployed
instance cannot translate, CT2 models never provisioned).

### Fixed
- **start.sh provisions the offline MT models** (BUG-003 root cause): when
  `TRANSLATION_PROVIDER=local` and `models/translation/` is empty, the four
  CT2 OPUS-MT pairs (~600 MB) are downloaded idempotently before uvicorn
  starts; a provisioning failure logs a loud ERROR without killing the app
  (STT/VAD keep working; degradation stays honest).
- **/health reveals pair coverage** (BUG-003 observability gap):
  `TranslationService.describe()` merges the provider's describe() — a
  zero-pair deployment now shows `translation.pairs: []` instead of looking
  healthy.
- **docs/deployment.md**: new §4.1 (translation model provisioning, pair
  repos, disk footprint), `TRANSLATION_PROVIDER` / `TRANSLATION_DETECT_MODE`
  / `TRANSLATION_MODELS_DIR` env rows, the stale "translation/TTS providers
  arrive in Phases 3/4" data-flow note replaced with the honest per-provider
  data flow, two new troubleshooting rows (pair-unavailable chip,
  `translation.pairs: []`).

### Added
- Regression test `test_describe_surfaces_provider_pairs` (populated and
  empty-pairs shapes) — backend suite now 130 passed + 1 xfail.

### Verified
- `/health` lists `pairs: [en/es, en/ur, es/en, ur/en]`; live E2E
  translation 3/3 with real MT (ur→en 113 ms, en→ur 168 ms, es pass-through);
  synthetic 3/3; silence guard PASS; continuous speech 5/5 ≤ 4.0 s.
  APP_VERSION 0.3.1.

## [0.3.0] — Phase 3 — Translation Decision Engine and Bidirectional Translation (2026-11-01)

Input: user approval "After that start the phase 3 and complete it" (master
prompt §23). Design + measured findings in ADR-0012.

### Added
- **Translation decision engine** (pure, any configured pair): Rule 1 native
  → preferred (for the other participant), Rule 2 preferred → native (for
  the translator user), Rule 3 every third language passes through with the
  provider never called, plus honest pass-throughs for identical config,
  low-confidence detection, undetected language, unsupported pair and
  over-long text.
- **Detection architecture "detect-first, pin-the-decode"** (ADR-0012 v2):
  standalone `detect_language` pass (measured: en p=0.997, es p=0.989,
  ur→`hi` p=0.775 — the KI-002 signature), ur/hi cluster + 0.60 threshold +
  native fallback resolution, then ONE decode pinned to the resolved
  language. Full auto-decode was measured unusable for Urdu (`ar` p=0.97,
  mixed Arabic/Devanagari text, 3–4× slower) and is never used.
- **Translation providers**: `local` offline CTranslate2 OPUS-MT
  (ur↔en + es↔en, `scripts/download_translation_models.py`, measured EOS
  framing fix for the CT2 conversions), `fake` deterministic stub, `libre`
  HTTP (MockTransport-tested), `none` disables. Keyless web providers probed
  and rejected: MyMemory (quota), Google gtx (bot-wall), argos index (404),
  ct2fast (401).
- **Protocol v1 (additive)**: `transcription.decision`
  {action, reason, source_language, target_language?, detection}; new
  `translation` event (same utterance id); non-fatal utterance-scoped
  `translation_failed` / `translation_timeout` / `translation_rate_limited`.
- **Bounded execution**: dedicated worker thread, 30/min sliding-window rate
  limit, ≤2 concurrent, 8 s timeout, stale-drop + task cancellation on close,
  2000-char text cap. Unicode/RTL text never rewritten.
- **Frontend**: `VITE_FEATURE_TRANSLATION` flag; per-line direction badge
  (`UR → EN`) or pass-through chip; RTL-aware translated text; per-line
  error chips; immutable update-by-utterance-id; protocol parser tolerant of
  `decision: null`.
- **Verification**: backend **128 passed + 1 xfail** (incl. real offline MT
  tests), frontend **57 tests** + tsc/lint/build clean, live
  `e2e_translation_check.py` **3/3** (Rules 1–3, real speech + real MT),
  continuous speech **5/5 ≤ 4.0 s** with the detection pass included,
  synthetic E2E 3/3, silence guard PASS, browser smoke **10/10**.

## [0.2.2] — QA fix round — BUG-001 error attribution + BUG-002 true push-to-talk (2026-11-01)

Input: independent Phase 2 QA report (verdict PASS; 2 Low defects + 3
observations). User instruction: "See the QA report and fixed the app."

### Fixed
- **BUG-001 (KI-010, ADR-0011):** error events now carry the flush's
  client-generated `utterance_id` (protocol v1 additive optional fields:
  `flush.utterance_id` in, `error.utterance_id` + `transcription.id` echo
  out). The client settles in-flight state strictly by id
  (`FlushTracker`); the `Transcribing…` indicator is DERIVED from the
  per-flush map, so the QA's two stuck-indicator modes (mis-attributed
  `no_speech_detected` after a newer drain / unconditional settle for a
  silence-drain `server_busy`) are closed by construction. Regression tests
  cover both modes (backend 69+1xfail, frontend 41).
- **BUG-002 (KI-011, ADR-0011):** the capture toggle now does what it says.
  OFF = true push-to-talk: no auto-flush at pauses, the whole press is
  transcribed when the user taps stop (700 ms tail capture kept, 20 s
  safety cap below the server's 30 s cap). ON = hands-free, unchanged
  (ADR-0010 endpointing). Toggle and status wording updated
  ("Push-to-talk recording…").
- **OBS-005 (partial):** `server_busy` messages now state that the segment
  was dropped BY DESIGN (nothing to re-flush) and include a queue-depth
  hint (`1/1 in flight`, `queue 16/16`); protocol spec's client obligation
  updated accordingly.

### Changed
- `.env` untracked from git (OBS-003; content was a non-sensitive
  `DATABASE_URL` placeholder).
- README screenshot link fixed to the tracked
  `docs/screenshots/phase2/` (OBS-004); `frontend/scripts/smoke_phase2.mjs`
  re-provisioned (playwright + Chrome fake mic) and re-run live: **10/10,
  zero console errors**; desktop + mobile screenshots committed.

### Verification
- backend `uv run pytest` → **69 passed, 1 xfailed**; frontend
  tsc/lint/**41 tests**/build clean; live E2E synthetic **3/3**, silence
  guard **PASS**, continuous speech **5/5** (first text 6.28 s,
  flush→text 1.84–1.98 s); browser smoke **10/10**. `APP_VERSION` 0.2.1 →
  0.2.2.

## [0.2.1] — Phase 2 defect round — KI-009: continuous-speech latency + newest-first transcript (2026-11-01)

### Fixed
- **Continuous-speech latency (KI-009, ADR-0010):** the client endpointer
  now closes a segment at the first natural micro-pause (≥ 300 ms) after
  4.5 s of audio (soft cap), and the hard cap drops 20 s → 9 s. Continuous
  speech yields a result every ~4.5 s (measured flush→text ≈ 2 s) instead
  of one blob after the 20 s cap + ~6 s decode — measured on the live
  server: first text 22.2 s → **6.6 s (−70 %)**. The 800 ms sentence-pause
  rule is unchanged for normal speech.
- **Transcript panel (KI-009):** the newest sentence renders at the TOP of
  the list (stored order stays chronological); the scroll-to-bottom effect
  is removed — fresh results are readable instantly.
- **`Transcribing…` indicator:** a pending-decode counter keeps it visible
  until the LAST in-flight decode lands (flushes overlap once segments
  stream every few seconds).

### Added
- `scripts/e2e_continuous_speech_check.py` — live old-vs-new endpointing
  benchmark on compressed-silence continuous speech, with a fixture-sanity
  gate (the old machine must NOT flush below the 20 s cap).

### Verification
- Backend **65 passed + 1 xfail**; frontend typecheck/lint/**33 tests**/
  build clean; live E2E 3/3 PASS, silence guard PASS, continuous-speech
  check PASS (docs/testing.md §5b). `APP_VERSION` 0.2.0 → 0.2.1.

## [0.2.0] — Phase 2 — Voice Activity Detection and Audio Optimization (2026-10-31)

### Added
- **Server-side Silero VAD pipeline** (`backend/app/audio/vad.py`, ADR-0009):
  every flush is gated (silence/noise → `no_speech_detected` in ~13 ms
  WITHOUT calling the STT — asserted 0 provider calls), edge-trimmed to its
  speech region (±200 ms; `audio_duration_s` still reports what the user
  spoke), and over-long speech spans (24 s) or pauses ≥ 700 ms segment the
  utterance into bounded decodes with texts joined in order. The Silero v6
  ONNX ships inside faster-whisper's assets — no new dependency.
- **Bounded STT admission / backpressure** (`backend/app/ws/backpressure.py`):
  global cap 16 + per-session cap 4 on utterances in flight; overflow answers
  a non-fatal `server_busy` (documented in the protocol spec) instead of
  queueing without bound; slot release is exception/timeout-safe
  (regression-tested).
- **Hands-free continuous mode** behind `VITE_FEATURE_HANDS_FREE` (default
  ON): toggle in the capture panel, persisted (`vt.hands_free`), status line
  "Hands-free listening…". Silence-only drains (20 s idle cap) no longer
  spam `no_speech_detected` banners (`noSpeechPolicy.ts`, unit-tested);
  endpointing/tail-capture stay on in both modes (they fixed KI-007/008).
- **Client uplink backpressure**: audio chunks are dropped while the socket
  send buffer exceeds 1 MiB (counted in `droppedChunkCount`); control JSON
  always passes.
- `/health` now reports `vad` (provider/enabled/loaded) and `stt_queue`
  (queued/max). New env vars: `VAD_GATE_ENABLED`, `VAD_THRESHOLD`,
  `VAD_MIN_SPEECH_MS`, `VAD_TRIM_PAD_MS`, `VAD_SPLIT_SILENCE_MS`,
  `VAD_SPLIT_MAX_SECONDS`, `STT_MAX_QUEUE_DEPTH`,
  `STT_MAX_PENDING_PER_SESSION`.
- `scripts/bench_vad.py` (gate cost, silence rejection, trim/accuracy) and
  `scripts/smoke_phase2.mjs` (headless browser smoke with Chrome's fake
  microphone; screenshots in `download/phase2-screens/`).

### Fixed
- The WS endpoint read the cached global `get_settings()` instead of the
  injected app settings — per-app overrides silently ignored (found by the
  new backpressure tests); it now reads `app.state.settings`.

### Verification
- Backend **65 passed + 1 documented xfail** (new: 9 VAD-planner, 5 real-Silero,
  7 WS/backpressure, 1 full-pipeline real-model test); frontend typecheck/
  lint clean, **33 tests** (+10), build ok.
- Live E2E 3/3 PASS (en/es/ur through the VAD pipeline), silence guard PASS,
  browser smoke **10/10** with the fake microphone.
- `bench_vad.py`: gate 10–13 ms; trim accuracy-neutral (identical Urdu text);
  peak RSS 1250 MiB (bench) / 1233 MiB resident (server, +170 MiB for Silero).
- Docs: ADR-0009, protocol spec (`server_busy`, VAD semantics, client
  obligations), architecture/testing/deployment/README, KI-001 → RESOLVED,
  TASKS/CURRENT_STATE/DECISIONS updated.

## [0.1.2] — Phase 1 bugfix rounds — KI-007 + KI-008 (2026-10-31)

User-reported: "language correct but content wrong/drifting"; then "tapping
stop immediately after speaking writes completely wrong things".

### Fixed (KI-007, ADR-0007)
- Model default `base` → `small` (measured: `base` garbles the ur fixture and
  drifts mid-sentence; `small` produces the correct text at RTF ≈ 0.32).
- Greedy `beam_size=1` → beam search `beam_size=5` with Whisper's
  quality-gated temperature ladder (recovers mid-sentence derailments).
- Decode-time Silero VAD (`vad_filter=True`, 500 ms min silence, 150→250 ms
  pad) trims non-speech before decode.
- Client-side sentence endpointing in the frontend hook: a ≥ 800 ms pause
  after ≥ 1.2 s of buffered audio flushes the sentence while the mic stays
  open (20 s hard cap) — results appear live, buffers are sentence-sized.

### Fixed (KI-008, ADR-0008)
- 700 ms tail-capture window on stop (UI: "Finishing… capturing the last
  words"; re-taps ignored; connection loss finalizes immediately).
- Server-side 250 ms edge padding before decode; VAD `speech_pad_ms` 150→250.
- Per-line ✕ delete button in the transcript list.

## [0.1.1] — Phase 1 bugfix round — KI-006 (2026-10-31)

User-reported: Urdu selected but transcripts appeared in Hindi/Persian-style
script and "random numbers"; 2–3 s perceived latency.

### Fixed
- **Wrong language/script/digits:** STT was running with automatic language
  detection although the user's native language was configured — the hint is
  now derived from `hello.config.native_language` (primary subtag) and pinned
  in the engine (`language_hint` on `STTProvider.transcribe_pcm`). Labels are
  deterministic (`language` = configured language, p=1.00); wrong-script
  output from ur/hi/fa confusion can no longer occur for the user's own mic.
- **Silence hallucination:** 2 s of digital silence decoded into a
  447-character pseudo-Urdu string (1594 ms) — a dead-audio gate now rejects
  peak-silent buffers before the model runs, and Whisper's no-speech segment
  filter drops classic hallucination segments. Fully filtered decodes surface
  as the new non-fatal `no_speech_detected` error (documented in
  `docs/audio-protocol.md`) instead of an empty/garbage transcript.
- **Latency:** language pinning skips the per-flush detection pass and the
  temperature-retry ladder is removed (`temperature=0.0`, `beam_size=1`) —
  measured 41–45% faster (ur fixture 1252→745 ms in-process; live
  `processing_ms` 940–999 → 537–600 ms). The UI now shows a "Transcribing…"
  indicator (status line + mic-button spinner) while a flush is in flight.

### Changed
- `e2e_synthetic_check.py`: per-fixture connection with the fixture's
  `native_language` (mirrors the real app; expectations now deterministic).
- `bench_stt.py`: benchmarks auto-detect vs pinned language side by side.
- Tests: 3 new WS-flow regression tests (hint propagation, regional-subtag
  reduction, no-speech error) + 3 new real-model tests (forced-label,
  dead-silence gate, auto-detect xfail renamed); backend 42 passed + 1
  documented xfail. Frontend unchanged tests (23 passed).
- Docs: ADR-0006, protocol spec (pinned language + `no_speech_detected`),
  testing results, KNOWN_ISSUES (KI-002 mitigated, KI-006 resolved).

## [0.1.0] — Phase 1 — Browser STT MVP (2026-10-31)

### Added
- **Foundation:** master prompt stored as `prompt.md`; `AGENT.md` agent rules;
  project-state tracking (CURRENT_STATE/TASKS/DECISIONS/KNOWN_ISSUES/CHANGELOG);
  platform dev shim (`.zscripts/dev.sh` → `start.sh`, single-port :3000);
  `.gitignore`.
- **Backend (Python 3.12, FastAPI, uv):**
  - audio WebSocket protocol **v1** (versioned pydantic contracts, strict
    validation, documented error codes — `docs/audio-protocol.md`);
  - `/ws/audio` state machine: hello/ready handshake, binary framing rules,
    bounded buffering with 30 s auto-flush, flush→transcription flow,
    idle-timeout reap, stale-result dropping;
  - bounded session manager (`MAX_SESSIONS`, cleanup on disconnect);
  - `STTProvider` abstraction + `FasterWhisperSTT` (CPU int8, lazy load,
    beam=1, automatic language detection, low-confidence flag);
  - `/health` (status, engine info, session stats, uptime);
  - centralized settings (env/.env), privacy-conscious logging;
  - scripts: `download_model.py`, `bench_stt.py`, `e2e_synthetic_check.py`.
- **Frontend (React 19 + Vite 6 + TypeScript 5 strict, no `any`):**
  - AudioWorklet mic capture: AudioContext(16 kHz) + streaming linear
    resampler fallback → PCM16, 100 ms chunks + input level;
  - `TranslatorWsClient`: hello handshake, backoff reconnect, ping/pong
    heartbeat with dead-connection detection, runtime-validated server events;
  - `useTranslatorSession` state hook (business logic separated from UI);
  - mobile-first UI: 18-language selectors (defaults Urdu/English,
    localStorage-persisted), mic button with states, level meter, RTL-aware
    transcript with language/confidence/latency metadata, connection badge,
    dismissible error banner; graceful mic-permission/no-device errors.
- **Tests:** backend 38 passed + 1 documented xfail (protocol/validation/
  sessions/WS flow with injected FakeSTT + real-model STT tests); frontend
  23 passed (protocol parser, languages, WS client state machine); strict
  typecheck and lint clean; production build clean.
- **Real-audio validation:** edge-tts neural fixtures (en/ur/es) transcribed
  by the real model; benchmark recorded (RTF 0.13–0.18, ~1 s per 7 s
  utterance, 609 MiB RSS); live E2E 3/3 PASS; headless-browser smoke test
  (render/connect/error/persistence/responsive).
- **Docs:** README, docs/architecture.md, docs/audio-protocol.md,
  docs/testing.md (measured results + honesty list), docs/deployment.md,
  ADR-0001…0005.

### Fixed
- Protocol events: `type` discriminators needed defaults for construction
  (caught by tests).
- E2E script re-sent `hello` per fixture — corrected to one handshake +
  multi-utterance session (matches protocol and browser behavior).

### Known issues at release
KI-001 (no VAD — Phase 2), KI-002 (ur/hi label confusion — Phase 3 design
input), KI-003 (buffered audio discarded on disconnect — accepted), KI-004
(real devices untested — Phase 6). See project-state/KNOWN_ISSUES.md.
