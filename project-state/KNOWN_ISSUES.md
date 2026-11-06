# KNOWN ISSUES

Format: description / reproduction / root cause / fix / verification / status.

---

## KI-001 — RESOLVED: server-side VAD + hands-free mode (was "by design, Phase 2 scope")

- **Description:** Phase 1 used manual push-to-talk; silence while the mic
  was open was buffered (bounded at 30 s → auto-flush) and decoded.
- **Resolution (Phase 2, ADR-0009):** server-side Silero VAD (bundled with
  faster-whisper, no new dependency) now gates every flush — silence/noise
  answered `no_speech_detected` in ~13 ms WITHOUT invoking STT (tested: 0
  provider calls) —, trims utterances to their speech region, and splits
  over-long speech at natural pauses. Hands-free continuous capture shipped
  behind `VITE_FEATURE_HANDS_FREE` with silence-drain suppression.
- **Verification:** backend 65 passed + 1 xfail (9 planner, 5 real-Silero,
  7 WS/backpressure tests); live E2E 3/3 PASS through the pipeline; silence
  guard PASS; browser smoke 10/10; benchmarks in docs/testing.md §5a.
- **Status:** RESOLVED (2026-10-31). Real-noise threshold tuning remains a
  Phase 6 input (KI-004).

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
- **Phase 3 resolution (ADR-0012, 2026-11-01):** the auto path now uses the
  STANDALONE `detect_language` pass (measured on the ur fixture: `hi` p=0.775,
  `ur` second — exactly this issue) and resolves it via the ur/hi cluster +
  0.60 threshold BEFORE decoding; the decode itself stays pinned (never
  auto-decoded — the full auto-decode measured WORSE: `ar` p=0.97 with mixed
  Arabic/Devanagari text). The event reports the RESOLVED language;
  `decision.detection="cluster"` records the correction. The xfail remains:
  it documents the RAW whisper label, which is exactly what the cluster
  normalizes. Residual corner (OPEN): Urdu speech reaching a config where
  neither configured language is in the {ur,hi} cluster (e.g. en-native
  listener) decodes as Hindi-orthography text as a confident third language.
- **Status:** MITIGATED for the production paths (2026-10-31 pinned;
  2026-11-01 cluster-resolved auto mode, ADR-0012); residual third-language
  corner documented above.

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
- **Fix (Phase 6, ADR-0016):** the code-controlled half is implemented and
  verified under FULL device emulation (`smoke_phase6.mjs`, iPhone-class
  viewport/touch/UA + fake mic, 8/8): honest capture lifecycle on
  backgrounding, wake lock, PWA install, 16 px controls, sticky mic panel.
  The hardware half is a written step-by-step human matrix
  (docs/testing.md §13b: Android Chrome ×7, iPhone Safari ×7, two-phone
  room ×4) — plus `/health sessions.by_platform` as ground truth that real
  devices connected.
- **Status:** OPEN (matrix ready — awaiting the human hardware run).

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

## KI-008 — RESOLVED: tapping stop immediately after speaking produces garbage; waiting 2–3 s is fine (user report)

- **Description (user report, 2026-10-31):** stopping the recording 2–3 s
  after finishing speech transcribes 70–90 % correctly, but stopping
  immediately writes "completely wrong things — something entirely different
  from what I actually said."
- **Reproduction (pre-fix):** record an utterance and tap stop the instant
  the last word ends → the buffer hard-cuts the final word (the worklet's
  pending partial chunk is dropped, no trailing silence) → Whisper derails
  on the truncated tail. Tapping stop after 2–3 s hides the bug because the
  client endpointer (ADR-0007) already flushed the sentence at a natural
  pause with clean boundaries.
- **Root cause:** asymmetric flush quality — pause-closed sentences get real
  trailing silence; stop-tapped ones do not. A word cut at the buffer edge is
  acoustically ambiguous and the decoder resolves it into wrong content.
- **Fix (ADR-0008):** (a) 700 ms tail-capture window on stop — the mic stays
  open and streams real trailing silence (UI: "Finishing… capturing the last
  words"; re-taps ignored; connection loss finalizes immediately);
  (b) server-side `EDGE_PAD_S = 250` ms silence padding at both utterance
  edges before decode (after the dead-audio gate); (c) VAD `speech_pad_ms`
  150 → 250. Same root-cause family as KI-007; users also received a
  per-line ✕ delete button for the transcript list (ADR-0008 §4).
- **Verification:** backend suite green (edge padding covered by the
  dead-silence gate ordering; real-model ur/en/es assertions unchanged);
  frontend typecheck/lint/23 tests/build clean; live E2E re-run after
  restart (docs/testing.md §4–6). Real-device confirmation of the tail
  window remains Phase 6 (KI-004).
- **Status:** RESOLVED (2026-10-31).

## KI-009 — RESOLVED: nothing converts while speaking continuously — results only appear after a pause; newest lines buried at the bottom (user report)

- **Description (user report, 2026-11-01):** with the mic on and continuous
  speech, nothing was converted "until I pause for a second or two"; the
  first response came fast, but during long continuous speech results took
  "quite a bit of time". Second request: "show new sentence at the top
  instead of the bottom" so fresh results are visible without scrolling.
- **Reproduction (pre-fix):** `scripts/e2e_continuous_speech_check.py` —
  `ur_greeting.wav` with every silence run ≥ 250 ms compressed to 200 ms,
  ×5 (22.5 s continuous speech), real-time streamed: the pre-fix state
  machine emitted **1 blob flush at the 20 s cap; first text after 22.2 s**
  (plus a mid-word-cut fragment from the cap flush).
- **Root cause:** the ADR-0007 endpointer closed segments only at a
  ≥ 800 ms pause (or the 20 s hard cap) — continuous speech with 200–500 ms
  micro-pauses never triggered it, so one 20 s blob decoded in ~6 s. The
  transcript panel additionally rendered oldest-first with auto-scroll to
  the bottom.
- **Fix (ADR-0010):** client endpointing v2 — soft cap 4.5 s (the next
  ≥ 300 ms micro-pause closes the segment) and hard cap 20 s → 9 s
  (worst-case blob ≈ 3 s decode); a pending-decode counter keeps the
  `Transcribing…` indicator up until the last overlapping flush lands; the
  transcript panel renders newest-first (chronological state unchanged) and
  no longer auto-scrolls. Sentence-pause rule (800 ms) unchanged.
- **Verification:** backend 65 passed + 1 xfail; frontend typecheck/lint/
  33 tests/build clean; live check — new mode **5/5 utterances, first text
  6.6 s (−70 %), flush→text 1.9–2.2 s, all texts correct Urdu**; old-mode
  baseline reproduced the complaint (docs/testing.md §5b).
- **Status:** RESOLVED (2026-11-01). Device-level tuning of the new caps
  stays a Phase 6 input (KI-004).

## KI-010 — RESOLVED: Transcribing… indicator can stay stuck after a mis-attributed error under server load (QA BUG-001)

- **Description (independent QA audit, 2026-11-01, BUG-001):** error events
  carried no utterance reference, so the client attributed every outcome to
  "the most recent flush" via one shared ref. Under load, outcomes arrive
  out of order: (a) a speech flush answered `no_speech_detected` after a
  newer silence-only drain overwrote the ref skipped the settle path — the
  pending counter leaked and the Transcribing… indicator never cleared;
  (b) a `server_busy`/`stt_timeout` for a silence-only drain settled
  unconditionally, clearing the indicator while a real speech decode was
  still in flight.
- **Root cause:** no correlation id on error events + single-shared-ref
  attribution + unconditional settle on non-no_speech errors.
- **Fix (ADR-0011):** protocol v1 gains optional `flush.utterance_id`
  (client-generated, echoed by the server on `transcription.id` and
  `error.utterance_id` for every flush-scoped error); the client keeps a
  per-flush `Map<id, hadSpeech>` (`FlushTracker`) and settles strictly by
  id; the Transcribing… indicator is DERIVED from the map (a stuck state is
  structurally impossible); connection loss clears the map.
- **Verification:** backend 69 passed + 1 xfail (new: id echoed on
  transcription / `nothing_to_transcribe` / VAD-gate rejection /
  `server_busy`); frontend 41 passed (new: `flushTracker.test.ts` covers
  both audit reproduction modes + unknown-id + clear; protocol/wsClient
  builders and parsers cover the new field); live E2E re-run — synthetic
  3/3, silence guard PASS, continuous speech 5/5 (first text 6.28 s,
  flush→text 1.84–1.98 s); browser smoke 10/10 with zero console errors.
- **Status:** RESOLVED (2026-11-01).

## KI-011 — RESOLVED: hands-free toggle had no behavioral effect; label implied push-to-talk when off (QA BUG-002)

- **Description (independent QA audit, 2026-11-01, BUG-002):** toggling
  hands-free OFF changed only the status wording — endpointing and
  auto-flush stayed on, while the label promised push-to-talk.
- **Fix (ADR-0011):** true push-to-talk implemented when OFF — pause-based
  and soft-cap auto-flushes suppressed, the whole press is transcribed on
  stop (tail capture kept, 20 s safety cap below the server's 30 s cap);
  toggle and status wording now describe the real behavior
  ("Push-to-talk recording…"). Hands-free mode is unchanged (ADR-0010).
- **Verification:** frontend 41 passed; browser smoke C6/C9 assert the two
  status wordings live (10/10); manual real-device PTT check remains in the
  docs/testing.md §8 checklist (KI-004).
- **Status:** RESOLVED (2026-11-01).

## KI-012 — OPEN: bundled local OPUS-MT models are everyday-sentence grade (Phase 3)

- **Description:** the offline CTranslate2 OPUS-MT models (ur↔en, es↔en,
  ~74 M params, Tatoeba-scale) translate short everyday sentences correctly
  and terminate cleanly (after the measured EOS framing fix), but nuanced or
  long Urdu→English output is weak — e.g. the greeting fixture translates as
  "How's that okay today? Thank you very much." (meaning recognizable,
  phrasing off).
- **Root cause:** model scale/training data, not the pipeline — decisions,
  limits, protocol and text handling are verified production paths
  (docs/testing.md §10).
- **Fix path:** the provider abstraction (ADR-0012) makes a stronger engine a
  settings change: `TRANSLATION_PROVIDER=libre` (+ URL/key), or any future
  provider implementation. No API keys exist in the deployment sandbox and
  every keyless web provider was probed and blocked (MyMemory quota, Google
  gtx bot-wall, argos index 404, ct2fast 401).
- **Status:** OPEN (accepted for Phase 3; honest quality claims only).

## KI-013 — RESOLVED: deployed instance cannot translate — MT models never provisioned (QA BUG-003, HIGH)

- **Description:** on any fresh git-based deployment every Rule-1/Rule-2
  utterance was transcribed and detected correctly, but the decision came
  back `passthrough/unsupported_pair` — the UI showed "pass-through · pair
  unavailable" and no translation was ever produced (independent QA verdict:
  FAIL, 2026-11-03).
- **Reproduction:** fresh checkout + `bash start.sh` → `/health` reported the
  translation block "healthy" (enabled, provider local-opus-mt) while zero
  pairs existed; speak English with ur→en config → pair-unavailable chip.
- **Root cause:** the local provider discovers its pairs by scanning
  `models/translation/<src>-<tgt>/` at startup; that directory is a
  git-ignored runtime artifact and nothing recreated it during deployment —
  `start.sh` provisioned only the Whisper STT model. Aggravated by an
  observability gap: `TranslationService.describe()` omitted the provider's
  own describe() fields, so a zero-pair deployment looked perfectly healthy
  in `/health`.
- **Fix (v0.3.1):** (1) `start.sh` provisions the four OPUS-MT pairs via
  `backend/scripts/download_translation_models.py` (idempotent, ~600 MB)
  whenever `models/translation/` is empty and `TRANSLATION_PROVIDER=local`;
  a provisioning failure logs a loud ERROR but does not kill the app (STT/VAD
  keep working, degradation stays honest). (2) `TranslationService.describe()`
  now merges the provider's describe() — `/health` exposes `pairs`,
  `compute_type`, `beam_size`. (3) `docs/deployment.md` gained §4.1
  (provisioning + footprint), env-var rows and troubleshooting entries.
- **Verification:** backend 130 passed + 1 xfail (new regression test
  `test_describe_surfaces_provider_pairs` covers the populated and the
  empty-pairs shapes); `/health` lists `pairs: [en/es, en/ur, es/en, ur/en]`;
  live E2E translation 3/3 with real MT (ur→en 113 ms, en→ur 168 ms, es
  pass-through); Phase 2 regressions green (synthetic 3/3, silence guard
  PASS, continuous speech 5/5 ≤ 4.0 s); browser smoke not re-run — frontend
  bundle unchanged (QA verified the identical bundle 10/10).
- **Status:** RESOLVED (2026-11-03, v0.3.1).

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

---

## KI-014 — ACCEPTED: first synthesis into a language pays the voice load (Phase 4)

- **Description:** the first translated utterance into a language with no
  loaded voice takes ~1.5 s longer (Piper voice lazy-load, measured in the
  live E2E: Rule 1 synthesis 1 573 ms incl. load vs 546 ms for the second
  language).
- **Root cause:** `PiperTTSProvider.ensure_loaded()` is a deliberate no-op —
  eager loading of all three voices would cost ~4.5 s of boot time and
  ~300 MB RSS for latency the user rarely notices (synthesis is otherwise
  RTF ≈ 0.06–0.08).
- **Fix / mitigation:** documented trade-off (ADR-0013); the load happens
  during the user's own utterance decode + translation window, so perceived
  delay is minimal; `/health` shows provisioned languages so missing voices
  are visible.
- **Status:** ACCEPTED (revisit if a future phase needs guaranteed
  constant synthesis latency).

---

## KI-015 — OPEN (Phase 6 scope): audio playback untested on real devices

- **Description:** spoken-output playback is verified in headless Chromium
  (smoke 10/10) and by unit tests with an injected fake AudioContext, but
  iOS Safari (low-power mode blocks autoplay, silent-switch behavior),
  Android Chrome battery saver and background-tab/lock-screen audio are
  unmeasured.
- **Repro:** n/a in sandbox (no real devices).
- **Phase 6 mitigation (ADR-0016):** the app-side autoplay story is now
  explicit — playback unlocks on the mic gesture (Phase 4) and re-arms when
  the tab becomes visible again via `resumeIfCreated` (never creates a
  context, so no autoplay warnings); backgrounding stops capture honestly.
  Device-side behaviors (silent switch, low-power mode) are rows B5/B6 of
  the human matrix (testing.md §13b).
- **Status:** OPEN (app side done; hardware rows await the human run).

## KI-016 — ACCEPTED (Phase 5): transcript line deletion is local-only

- **Description:** deleting a sentence bubble removes it (and stops its
  playback) on the deleting device only — the other participant keeps the
  line. There is no cross-device deletion sync in the protocol.
- **Root cause:** deliberate Phase 5 scope control: a `delete` control
  message, peer-side cancellation and conflict rules add protocol surface
  that the master prompt does not require for the MVP.
- **Status:** ACCEPTED for the MVP; revisit with the meeting-integration
  phases if shared-moderation becomes a requirement.

---

## KI-017 — ACCEPTED (Phase 5): host language changes apply on the next connection

- **Description:** the room's language pair is captured at the host's
  `hello` (it is the room's config authority). Changing the selectors
  mid-room does not re-greet the server; the new pair applies when the
  host's client reconnects (config is read at connect time — unchanged
  since Phase 1).
- **Repro:** open a room, change languages, speak — decisions still use the
  room's original pair until reconnect.
- **Status:** ACCEPTED (consistent with the Phase 1–4 config-at-connect
  behavior); a live re-hello control message is a possible future
  extension.

---

## KI-018 — OPEN (Phase 6 scope): two-participant acoustics on real hardware

- **Description:** the feedback loop (scenario 12) is prevented
  architecturally (the server never re-ingests delivered audio — asserted
  in the room tests and the live E2E) and client-side (PlaybackGuard mutes
  the uplink while remote audio is audible, unit-tested). What is NOT
  measured: loudspeaker→mic coupling on real laptop/phone hardware, the
  guard's effect on natural conversation overlap, and AEC quality across
  devices.
- **Repro:** n/a in the sandbox (no real devices).
- **Phase 6 status:** unchanged by design — the architectural + client-side
  guards are already verified; the acoustic measurement rows (loudspeaker
  coupling, AEC quality, overlap behavior) are section C of the human
  matrix (testing.md §13b).
- **Status:** OPEN (awaiting the human hardware run).

---

## KI-019 — RESOLVED (QA round, BUG-001): HuggingFace 429 left the deployment's TTS voiceless

- **Description (QA Journal 2026-11-05):** the deployed server had ZERO
  usable Piper voices (`/health tts.languages=[]`) — every translated
  utterance answered `tts_unsupported_language` and no synthesized audio
  ever reached any browser. En/ur directories held a voice `.onnx` each but
  NOT their `.onnx.json` configs; `es/` was empty.
- **Root cause:** HuggingFace answers **HTTP 429** for the small voice
  config right after a ~63 MB voice download; the boot-time provisioning
  run "finished" with onnx-only directories, and `start.sh`'s
  directory-is-non-empty check silently accepted the incomplete state.
- **Resolution (ADR-0015):** `download_tts_voices.py` retries transient
  failures (5 attempts, 5→60 s backoff) and gained `--verify` (exit 0 only
  when every voice has BOTH files); `start.sh` verify-gates the boot and
  (re-)provisions any incomplete state; the app logs a `TTS PROVISIONING
  WARNING` at startup and `/health.tts.warning` flags the same condition.
- **Verification:** all 6 voice files present (ur/en/es onnx+json);
  `/health` reports `tts.languages=["en","es","ur"]` with no warning;
  live probe: guest receives 110,592 B synthesized PCM for a host Urdu
  utterance, zero self-audio on the host; solo fallback intact.

---

## KI-020 — RESOLVED (QA round, BUG-002): ghost seat blocked rejoin for ~180 s after an unclean drop

- **Description (QA Journal):** after a tab crash / killed mobile browser
  (no close frame reaches the server — the preview proxy does not
  propagate client-side closes), the departed participant kept its seat
  until the 180 s idle timeout; rejoining with the same code answered a
  FATAL `room_full` and the app stopped reconnecting.
- **Root cause:** disconnect detection relied solely on the server-side
  socket state, which the proxy keeps alive after the client is gone.
- **Resolution (ADR-0015):** the server tracks the last RECEIVED frame per
  connection; a rejoin that finds its own seat held by a member silent for
  `ROOM_STALE_SEAT_SECONDS` (12 s default) evicts the ghost (fatal
  `seat_taken` + server-side close + honest `peer left` to the peer) and
  takes the seat. Live occupants are never evicted (fatal `room_full`
  unchanged). Client: 4 s heartbeat (pong deadline armed once per
  unanswered ping), `room_full` retried during reconnects (≤90 s) instead
  of dying, `seat_taken` added to the always-fatal codes.
- **Verification:** backend suite + 4 new eviction tests (ghost evicted,
  live seat protected, guest variant, ghost-cleanup regression guard);
  live probe: rejoin succeeds after 13 s silence with left→joined events.

---

## KI-021 — MITIGATED (QA round, BUG-003): the preview proxy does not forward WebSocket close frames

- **Description (QA Journal):** server-initiated closes after fatal
  protocol errors (e.g. `room_not_found`) never reach the client through
  the preview proxy (locally the close arrives immediately with code 1008).
  The same gap causes KI-020's ghost seats.
- **Root cause:** platform preview proxy limitation — not fixable in the
  application.
- **Mitigation (ADR-0015):** the app layer is correct on both ends (server
  closes + tears down on every fatal path; the reference client closes its
  own socket on room-fatal errors and its 10 s pong deadline reaps a
  half-open socket within ≤14 s), so behavior is correct even when the
  close frame is swallowed.
- **Status:** MITIGATED — production reverse proxies forward close frames;
  re-verify on the Phase 10 production deployment.

## KI-022 — RESOLVED (QA round, BUG-601): client stuck in "Reconnecting…" forever after a lost reconnect hello

- **Description (QA Journal, 2026-11-07):** after an uplink blackout (mobile
  dead zone / Wi-Fi↔LTE transition) the client could never complete the
  handshake again — status frozen at "Reconnecting…", mic disabled, only a
  manual page reload recovered it. Deadlock observed > 50 s (probe
  abandoned, no recovery).
- **Reproduction:** connect + start recording → black out the uplink
  (message-level frame drop) → pong reaper fires (~10 s + 4 s heartbeat),
  socket closes, honest banner (correct) → reconnect attempt opens a new
  socket while the link is still black, its hello is dropped → restore the
  link → the app never reconnects: pings flow, pongs flow, `ready` never
  comes.
- **Root cause (three factors, each verified live by QA):** the client sent
  `hello` exactly once per socket (a lost hello was never retried); the
  server answered `ping` even BEFORE `hello` completed, so the client's
  pong-based dead-connection detector never fired; the server kept
  hello-less sockets open until the 180 s idle timeout.
- **Fix (ADR-0017, v0.6.1):** client — the hello is repeated on every
  heartbeat tick until `ready` arrives, and a hello-ack watchdog (10 s)
  recycles a socket whose handshake never completes even when pongs keep
  flowing; server — `ping` is not answered before `hello` completes, a
  repeated hello is re-acked idempotently with a fresh `ready` (same
  session and room), and a socket that never says hello is closed after
  `ws_hello_timeout_seconds` (15 s, fatal `hello_timeout`).
- **Verification:** backend **190 passed + 1 xfailed** (+4: duplicate hello
  re-acks solo AND in-room with the same session/room, pre-hello ping gets
  no pong, hello-timeout closes a silent socket); frontend **125/125**
  (+2: re-hello until ready, watchdog recycles a pong-only socket);
  `smoke_phase6.mjs` **9/9** — the new M9 check blackouts the uplink for
  14 s (past the pong timeout + one reconnect attempt) and asserts return
  to Connected (measured ≈ 5 s after restore, budget 30 s);
  `smoke_phase2` 10/10 and `smoke_phase5` 12/12 stay green (the rejoin
  path shares this code).
- **Status:** RESOLVED (2026-11-07).

## KI-023 — CLOSED (QA round, BUG-602): stale deployment reported /health.version 0.5.1 on a Phase 6 build

- **Description (QA Journal, 2026-11-07):** the deployed preview answered
  `/health` with `version=0.5.1` while serving Phase 6 features
  (`sessions.by_platform`, Phase 6 bundle) — a combination no committed
  tree has. Observational only; all Phase 6 behavior verified functional.
- **Root cause:** stale/partially synced deployment working tree (the
  version string in `main.py` did not match the rest of the built code).
- **Fix:** no code change needed — the source has carried the correct
  version since 5c5f2fa (0.6.0; 0.6.1 as of this round). Redeploy from the
  branch tip and assert `/health.version` in the deploy checklist.
- **Verification:** after rebuild + redeploy for this QA round, `/health`
  reports **0.6.1**.
- **Status:** CLOSED (2026-11-07).
