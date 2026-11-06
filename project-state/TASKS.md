# TASKS

Status vocabulary: `TODO`, `IN PROGRESS`, `BLOCKED`, `DONE`.
A task may only be marked `DONE` after implementation **and** verification.

## Phase 1 — Project Foundation and Browser STT MVP — COMPLETE

| ID | Task | Status |
| --- | --- | --- |
| P1-01 | Inspect existing repo + environment; report state and gaps | DONE |
| P1-02 | Foundation: master prompt (`prompt.md`), `AGENT.md`, project-state files, platform dev shim, `.gitignore` | DONE |
| P1-03 | Backend scaffold: uv project, settings, logging, health endpoint | DONE |
| P1-04 | Backend: audio protocol v1 models + validation | DONE |
| P1-05 | Backend: session manager | DONE |
| P1-06 | Backend: WebSocket endpoint (`/ws/audio`) — hello / binary audio / flush / transcription flow | DONE |
| P1-07 | Backend: STT provider abstraction + faster-whisper (CPU) implementation | DONE |
| P1-08 | Backend: model download script + model pre-downloaded (base, cached in-project) | DONE |
| P1-09 | Backend tests: protocol, validation, sessions, WS flow (fake STT) — 38 passing | DONE |
| P1-10 | Backend real-STT tests + latency/memory benchmark (edge-tts neural fixtures) | DONE |
| P1-11 | Frontend scaffold: Vite + React + strict TS + ESLint + vitest | DONE |
| P1-12 | Frontend: protocol types (runtime-validated) + 18-language config | DONE |
| P1-13 | Frontend: WS client (reconnect + heartbeat) + AudioWorklet mic capture (PCM16/16 kHz) | DONE |
| P1-14 | Frontend: UI — language selectors, mic button, transcript panel (RTL), connection status, error banner | DONE |
| P1-15 | Frontend tests (23) + typecheck + lint + production build | DONE |
| P1-16 | E2E: synthetic speech over live WebSocket — 3/3 PASS on :3000 | DONE |
| P1-17 | Browser smoke test (headless Chromium) — render/connect/error-path/persistence/responsive | DONE |
| P1-18 | Documentation: README, docs/*, 5 ADRs, project-state finalized, CHANGELOG | DONE |
| P1-19 | Phase 1 completion report + request approval | DONE |
| P1-20 | **KI-006 fix (user report):** pin STT language to configured native language; dead-audio gate + no-speech hallucination filter; single-temperature decode; "Transcribing…" indicator; `no_speech_detected` protocol code; ADR-0006; re-verify (backend 42+1xfail, frontend 23, live E2E 3/3 with ur→ur, silence guard PASS, 41–45% faster) | DONE |

## Phase 2 — Voice Activity Detection and Audio Optimization — COMPLETE

| ID | Task | Status |
| --- | --- | --- |
| P2-01 | Server-side VAD (Silero, bundled with faster-whisper) + silence detection + segmentation (gate → trim → split, ADR-0009) — 21 new backend tests incl. real-Silero + full-pipeline real-model | DONE |
| P2-02 | Continuous (hands-free) capture mode behind `VITE_FEATURE_HANDS_FREE` — toggle, persistence, silence-drain suppression, status wording; endpointing/tail-capture stay on in both modes | DONE |
| P2-03 | Bounded buffers/queues review → global (`STT_MAX_QUEUE_DEPTH=16`) + per-session (`STT_MAX_PENDING_PER_SESSION=4`) admission caps answered `server_busy`; exception-safe slot release; stale-result dropping verified; client uplink drop policy > 1 MiB; settings-injectability bug found & fixed | DONE |
| P2-04 | CPU/memory/latency measurements before vs after VAD (`scripts/bench_vad.py`): gate 10–13 ms, trim accuracy-neutral (identical ur text), RSS +~170 MiB for Silero; trade-offs documented in docs/testing.md §5a/§6 | DONE |
| P2-05 | Real-device capture tests — sandbox proxy: headless Chromium with Chrome's fake microphone, 10/10 smoke checks (`scripts/smoke_phase2.mjs`, screenshots `download/phase2-screens/`); manual desktop-Chrome checklist documented (docs/testing.md §8); full real-device matrix remains Phase 6 (KI-004) | DONE (sandbox scope; human checklist provided) |

## Phase 2 completion

- Approval: user message "After that start the work for Phase Two and
  complete it" (2026-10-31) — explicit approval per master prompt §23.
- Verification: backend 65 passed + 1 documented xfail; frontend 33 passed +
  typecheck/lint/build clean; live E2E 3/3 PASS; silence guard PASS; browser
  smoke 10/10; benchmarks recorded (docs/testing.md).
- Docs: ADR-0009; protocol spec updated (server_busy, VAD semantics);
  architecture/deployment/README updated; KI-001 RESOLVED.

## Phase 2 defect round 2 — KI-009 (user report, 2026-11-01) — COMPLETE

| ID | Task | Status |
| --- | --- | --- |
| P2-R2-01 | Continuous-speech latency (ADR-0010): client endpointing v2 — soft cap 4.5 s + 300 ms micro-pause, hard cap 20 s → 9 s; pending-decode counter keeps the Transcribing… indicator honest while flushes overlap | DONE |
| P2-R2-02 | Transcript panel newest-first (user request); scroll-to-bottom removed; per-line ✕ delete unaffected | DONE |
| P2-R2-03 | `scripts/e2e_continuous_speech_check.py` — live old-vs-new benchmark on 22.5 s compressed-silence ur speech; old baseline reproduced (first text 22.2 s), new PASS (5/5 utterances, first text 6.6 s, flush→text 1.9–2.2 s) | DONE |
| P2-R2-04 | Re-verification: backend 65 passed + 1 xfail; frontend tsc/lint/33 tests/build clean; live E2E 3/3 PASS; silence guard PASS; built bundle carries ADR-0010 constants | DONE |
| P2-R2-05 | Docs: ADR-0010; testing.md §1/§2/§3/§5b/§6/§7/§9; KI-009 RESOLVED; CHANGELOG 0.2.1; DECISIONS D-012; CURRENT_STATE; APP_VERSION 0.2.1 | DONE |
| P2-R2-06 | Recreate lost `download/Phase-One.zip` (sandbox wiped `download/` between sessions): Phase-1-final tree @ `3c1b5c2` (tag `phase-one-final`) + git-history bundle + MANIFEST, integrity verified | DONE |

## Repository & delivery — GitHub setup (2026-11-01) — COMPLETE (push pending credentials)

| ID | Task | Status |
| --- | --- | --- |
| GIT-01 | Repo-local Git identity set to the user's Repository Details (`user.name "m Farooq"`, `user.email khanbhi88@gmail.com`) and verified before any commit/push (user rule §3) | DONE |
| GIT-02 | Pre-publish history alignment — all 11 commits' author/committer → `m Farooq <khanbhi88@gmail.com>` (repo never pushed; `filter-branch`; old-hash doc references updated; tag `phase-one-final` re-pointed) | DONE |
| GIT-03 | Working-tree normalization — 14 files with accidental +x mode bits (content unchanged, 0 insertions/deletions) reverted to 644 | DONE |
| GIT-04 | `origin` remote → `https://github.com/farooq110/voice-translator.git`; branch `phase-2` created from `main` | DONE |
| GIT-05 | Recreate `download/Phase-One.zip` (sandbox wiped `download/` again): Phase-1-final tree @ tag `phase-one-final` + git-history bundle + MANIFEST, integrity verified | DONE |
| GIT-06 | Push to origin — authenticated via the user-provided SSH key (ed25519, fingerprint `SHA256:2lsCE9…`) through a paramiko `core.sshCommand` shim (sandbox has no ssh binary; GitHub's published host-key fingerprints pinned; remote URL switched to `git@github.com:farooq110/voice-translator.git`). Remote `main` (web-UI bootstrap: one-commit README "# voice-translator") superseded by force-push; `main` + `phase-2` both pushed | DONE |

## QA fix round — Phase 2 independent audit (2026-11-01) — COMPLETE

Input: user-provided QA report ("See the QA report and fixed the app") —
verdict PASS with 2 Low defects + 3 observations. No new feature scope; all
fixes local to the Phase 2 code.

| ID | Task | Status |
| --- | --- | --- |
| QA-01 | BUG-001 (ADR-0011): protocol v1 optional `flush.utterance_id` + `error.utterance_id` echo (backend, additive); client `FlushTracker` per-flush attribution — Transcribing… indicator derived, stuck state structurally impossible; regression tests both audit modes (backend 69+1xfail, frontend 41) | DONE |
| QA-02 | BUG-002 (ADR-0011): true push-to-talk when hands-free OFF — pause/soft-cap auto-flushes suppressed, flush on stop (tail capture kept, 20 s safety cap); toggle + status wording describe the real behavior | DONE |
| QA-03 | OBS-005 (partial): `server_busy` messages now say the segment was dropped BY DESIGN and carry a queue-depth hint (`1/1 in flight`, `queue 16/16`); protocol spec client-obligation updated (no re-flush) | DONE |
| QA-04 | OBS-003: `.env` untracked (`git rm --cached`), already gitignored | DONE |
| QA-05 | OBS-004: README image link fixed to tracked `docs/screenshots/phase2/`; `frontend/scripts/smoke_phase2.mjs` re-provisioned (playwright, fake mic) and re-run live: 10/10, zero console errors; screenshots committed | DONE |
| QA-06 | Re-verification: backend 69 passed + 1 xfail; frontend tsc/lint/41 tests/build clean; live E2E 3/3 + silence guard PASS + continuous speech 5/5 (first text 6.28 s); docs: ADR-0011, protocol spec, testing.md, KI-010/011, CHANGELOG 0.2.2, APP_VERSION 0.2.2 | DONE |
| QA-07 | OBS-005 remainder (WS origin allow-listing, authentication) — stays Phase 10 per the audit and docs | DEFERRED (Phase 10) |

## Phase 3 — Translation Decision Engine and Bidirectional Translation — COMPLETE

Approval: user message "After that start the phase 3 and complete it"
(2026-11-01) — explicit approval per master prompt §23.

| ID | Task | Status |
| --- | --- | --- |
| P3-01 | Decision engine (pure, ADR-0012): master prompt Rules 1–4 for any pair — native→preferred, preferred→native, third-language pass-through with NO provider call, identical-config, low-confidence/undetected/unsupported/too-long honest pass-throughs; 17 unit tests | DONE |
| P3-02 | Detection architecture "detect-first, pin-the-decode": standalone `detect_language` pass (measured: en 0.997 / es 0.989 / ur→hi 0.775 = KI-002) + ur/hi cluster + 0.60 threshold + native fallback; ONE decode pinned to the resolved language (never auto-decoded — auto measured unusable: `ar` p=0.97, mixed scripts, 3–4× slower); 8 s detection cap (greeting-prefix finding) | DONE |
| P3-03 | Provider abstraction + 3 implementations: `local` CT2 OPUS-MT (ur↔en + es↔en offline, download script, measured EOS framing fix), `fake` (deterministic), `libre` (httpx + MockTransport-tested); keyless web providers probed and rejected (MyMemory quota / Google bot-wall / argos 404 / ct2fast 401) — documented | DONE |
| P3-04 | Protocol v1 additive: `transcription.decision` (action/reason/source/target/detection), `translation` event, 3 utterance-scoped non-fatal error codes (`translation_failed/timeout/rate_limited`); client tolerates `decision: null` | DONE |
| P3-05 | Bounded execution: dedicated worker thread, sliding-window rate limit 30/min, ≤2 concurrent, 8 s timeout, stale-drop on close + task cancellation, text cap 2000 chars; Unicode/RTL never rewritten | DONE |
| P3-06 | Frontend: `VITE_FEATURE_TRANSLATION` flag, per-line direction badge / pass-through chips, RTL-aware translated text, per-line error chips; immutable update-by-id (`services/translations.ts`); protocol parser (null-tolerant) + 10 service tests + 7 parser tests | DONE |
| P3-07 | Verification: backend **128 passed + 1 xfail** (incl. `translation_real` — real ur→en/en→ur through CT2 models); frontend tsc/lint/**57 tests**/build clean; live `e2e_translation_check.py` **3/3** (Rules 1–3 with real MT); continuous speech **5/5 ≤ 4.0 s** with the detect pass; synthetic 3/3; silence guard PASS; browser smoke **10/10** | DONE |
| P3-08 | Docs: ADR-0012; protocol spec §3a; testing.md §2/§3/§10; .env.example; README (status/architecture/limitations); KI-002 update + KI-012; DECISIONS D-014; CHANGELOG 0.3.0; CURRENT_STATE; APP_VERSION 0.3.0 | DONE |

## Phase 4 — Text-to-Speech and Browser Audio Playback — COMPLETE (pending approval)

Approval: user message "start pahse 4 and complete it" (2026-11-04) —
explicit approval per master prompt §23.

| ID | Task | Status |
| --- | --- | --- |
| P4-01 | TTS engine evaluation + decision: Piper (offline VITS on onnxruntime — already shipped for faster-whisper; no torch/keys/GPU); voices ur_PK-fasih / en_US-lessac / es_MX-ald (22 050 Hz) measured RTF 0.06–0.08 on 2 vCPU | DONE |
| P4-02 | TTSProvider abstraction (`app/tts/base.py`) + `piper_local.py` (lazy per-language load, discovery) + `fake.py` (deterministic sine, seeded) + `none`; bounded `TTSService` (30/min sliding window, ≤1 concurrent, 10 s timeout, 800-char cap, dedicated executor) — 12 service tests | DONE |
| P4-03 | Protocol v1 additive: `audio` metadata event + exactly one binary PCM16 frame (metadata+binary under one send lock); 5 utterance-scoped non-fatal codes (`tts_failed/timeout/rate_limited/unsupported_language/text_too_long`); synthesis task tracking + cancellation on disconnect; pass-through NEVER reaches TTS (asserted) | DONE |
| P4-04 | Frontend: wsClient audio⇄binary pairing (stray-frame counter, stale-meta clear), SpeechPlayer (sequential Web Audio scheduling — overlap impossible, bounded queue w/ oldest-drop + re-base after cancel, mute cancels, autoplay unlock on mic gesture, stop-all), "Spoken translations" toggle (persisted), speaking chip + voice-error chips + stop-voice button | DONE |
| P4-05 | Ops: `scripts/download_tts_voices.py` (idempotent, ~190 MB), `start.sh` provisioning step (loud failure), `/health` `tts` section (provider/languages/voices/bounds) | DONE |
| P4-06 | Verification: backend **155 passed + 1 xfailed** (+25 tests, incl. `tts_real` real synthesis); frontend **81/81** + tsc + ESLint + build 254.5 kB; live `e2e_tts_check.py` **3/3** (real speech → real MT → real synthesis, RMS-checked; es pass-through → ZERO audio frames); translation E2E 3/3 + silence guard PASS + browser smoke 10/10 (zero console errors) | DONE |
| P4-07 | Docs: ADR-0013; protocol spec §3b + error table; architecture.md (diagram, threading, language path, testing); deployment.md §4.2 + env vars; testing.md §11; README (status/diagram/limitations/roadmap); prompt.md (Phase 4 record); DECISIONS D-015; CHANGELOG 0.4.0; CURRENT_STATE; APP_VERSION 0.4.0 | DONE |

## Phase 5 — Complete Browser MVP (Two Participants) — COMPLETE (pending approval)

Approval: user message "start phase 5 and complete it" (2026-11-04) —
explicit approval per master prompt §23.

| ID | Task | Status |
| --- | --- | --- |
| P5-01 | Room model (ADR-0014): `app/ws/rooms.py` Room + RoomManager (6-char unambiguous invite codes, in-memory, 120 s linger + rejoin, `ROOM_LINGER_SECONDS`), room-aware hello (host creates/rejoins, guest joins configuring nothing), `peer` joined/left events, fatal `room_required`/`room_not_found`/`room_full`/`missing_languages`, leave/linger cleanup, `/health rooms` — 16 WS room tests (scenarios 1–11 with fakes) | DONE |
| P5-02 | Routing: transcripts + translations broadcast to both members with `speaker` tags; synthesized audio ONLY to the other participant (solo fallback = exact Phase 1–4 shape); translation failures shared, TTS failures to the audio's recipients, STT errors speaker-local; pass-through relay of the ORIGINAL captured audio (16 kHz, never TTS, never replayed to the speaker); guest decoded against the host's pair | DONE |
| P5-03 | Protocol v1 additive: optional `role`/`room` in hello + optional language fields (validated), `speaker` on transcription/translation/audio/error, `RelayEvent` + binary pairing, null fields omitted (absent ≡ null, documented); frontend protocol parser extended | DONE |
| P5-04 | Frontend: invite-link flow (`?room=CODE`), guest view with NO language panel + host's pair, host invite panel (code + copy link + peer status), speaker-aware transcript keyed (speaker, id) with Guest/Host chips, wsClient relay pairing, room-fatal errors stop reconnect, host rejoin via stored code | DONE |
| P5-05 | Feedback-loop prevention (scenario 12): server never re-ingests output audio (asserted) + client `PlaybackGuard` dropping uplink while REMOTE audio plays (+300 ms tail; own-utterance TTS never arms it) — 6 unit tests | DONE |
| P5-06 | Verification: backend **171 passed + 1 xfailed**; frontend **101/101** + tsc + ESLint + build; live `e2e_phase5_check.py` **4/4** (two real connections, real STT/MT/Piper: routed voice only to the listener, relay without TTS, same-language both ways); translation E2E 3/3 + silence guard PASS; `smoke_phase5.mjs` **12/12** (invite-link join, guest sees no settings, both record, zero console errors); `smoke_phase2.mjs` 10/10 | DONE |
| P5-07 | Docs: ADR-0014; README (status/diagram/limitations/roadmap); protocol spec (rooms/peer/relay/speaker + error table); architecture.md; testing.md §12; deployment.md (ROOM_LINGER_SECONDS); prompt.md (Phase 5 record); DECISIONS D-016; KNOWN_ISSUES KI-016/017/018; CHANGELOG 0.5.0; CURRENT_STATE; APP_VERSION 0.5.0; branch-local PHASE-5.md (phase-5 branch only) | DONE |

## Phase 5 QA round — Deployment hardening — COMPLETE (pending approval)

Input: independent QA verification report (Journal, 2026-11-05) — verdict
"NOT ACCEPTED yet", blockers BUG-001/BUG-002, advisory BUG-003.

| ID | Task | Status |
| --- | --- | --- |
| P5-QA-1 | BUG-001: TTS provisioning integrity — `download_tts_voices.py` retry/backoff for HF 429 + `--verify` (all voices complete or exit 1); `start.sh` verify-gates the boot; startup `TTS PROVISIONING WARNING` log + additive `/health.tts.warning`; voices re-provisioned live (ur/en/es onnx+json) | DONE |
| P5-QA-2 | BUG-002: stale-seat eviction — `last_seen` per connection refreshed on every received frame; rejoin evicts its own seat's ghost after `ROOM_STALE_SEAT_SECONDS` (12 s) with fatal `seat_taken` + server-side close + honest `peer left`; `leave_room()` identity guard (an evicted ghost can never eject the returning member); live occupants keep fatal `room_full` | DONE |
| P5-QA-3 | BUG-002 client side: 4 s heartbeat (pong deadline armed once per unanswered ping), `room_full` retried during RECONNECT ≤90 s with honest banner (first-join room_full stays fatal), `seat_taken` added to fatal codes | DONE |
| P5-QA-4 | BUG-003: app-layer verified on both ends (server closes on every fatal path — confirmed locally; client closes on room-fatal errors; half-open sockets reaped ≤14 s by the pong timeout); proxy close-frame gap documented (KI-021) for the production deployment | DONE |
| P5-QA-5 | Verification: backend suite green (+6 tests: eviction host/guest, live-seat guard, cleanup regression, health warnings ×2); frontend 101/101 + tsc + build; live probe 5/5 (TTS delivery to the opposite participant only 110,592 B PCM, zero self-audio, solo fallback, ghost reclaim after 13 s, live-seat room_full, fatal close); `/health` v0.5.1 shows `tts.languages=[en,es,ur]` | DONE |
| P5-QA-6 | Docs: ADR-0015; protocol spec (eviction + `seat_taken` + heartbeat + retry semantics); DECISIONS D-017; KNOWN_ISSUES KI-019/020/021; CHANGELOG 0.5.1; CURRENT_STATE; APP_VERSION 0.5.1; PHASE-5.md QA-round section (phase-5 branch) | DONE |

## Phase 6 — Mobile/browser compatibility — COMPLETE (pending approval)

Approval: user message "start phase 6 and complete it" (2026-11-06) —
explicit approval per master prompt §23 (it also closes the Phase 5
approval record).

| ID | Task | Status |
| --- | --- | --- |
| P6-01 | Honest capture lifecycle (ADR-0016): `visibilitychange → hidden` finalizes the recording exactly like a connection loss (capture stopped, buffer flushed, playback + PlaybackGuard cleared) with a plain-language banner; `pagehide` finalizes quietly; `startMicCapture` gains a `statechange` watchdog (`onStateLost`) for iOS `interrupted`/suspension, disarmed on stop; return-to-tab re-arms playback via `SpeechPlayer.resumeIfCreated()` (never creates an AudioContext) | DONE |
| P6-02 | Screen Wake Lock (`services/wakeLock.ts`, injectable + unit-tested): acquire on recording start, release on finalize, OS-dropped sentinels forgotten, denials (low-power mode) degrade silently | DONE |
| P6-03 | Coarse platform telemetry: protocol v1 additive optional `hello.config.client.platform` (lowercase tag ≤ 24 chars, never a raw UA); `Session.platform` + `/health sessions.by_platform` aggregation; 7 params validation tests + 2 aggregation tests; older clients unaffected | DONE |
| P6-04 | Mobile comfort: PWA manifest + generated 192/512 icons + apple metas; `overscroll-behavior-y: none` (no pull-to-refresh reload mid-recording); 16 px form controls (no iOS focus zoom); sticky capture panel on small screens; ≥ 44 px touch targets; stale phase copy in index.html/footer refreshed; frontend version 0.6.0 | DONE |
| P6-05 | Verification: backend **177 passed + 1 xfailed** (+7); frontend **123/123** (+9: platform mapping, WakeLock incl. OS-drop/deny, resumeIfCreated) + tsc + ESLint + build; `smoke_phase6.mjs` under FULL iPhone emulation **8/8** (incl. platform tag end-to-end + background-tab guard); regressions smoke_phase2 **10/10**, smoke_phase5 **12/12**; live E2E translation **3/3**, two-participant **4/4**, silence guard **PASS** | DONE |
| P6-06 | Human real-device matrix written into `docs/testing.md §13b` (Android Chrome ×7, iPhone Safari ×7 incl. call/Siri interruption, lock screen, silent switch, low-power mode; two-phone room ×4) — the sandbox has no hardware; no claims made until it runs (KI-004/015/018) | DONE (matrix ready; hardware run is human) |
| P6-07 | Docs: ADR-0016; protocol spec (hello.client + mobile lifecycle obligations §6); testing.md §13; README (status/limitations/roadmap); prompt.md → Phase 6 record; DECISIONS D-018; KNOWN_ISSUES KI-004/015/018 updated; CHANGELOG 0.6.0; CURRENT_STATE; APP_VERSION 0.6.0 | DONE |

## Phase 7+ — see `prompt.md` section 17

Zoom (P7), extension (P8), more apps (P9), production (P10). All `TODO`
and **blocked until the respective phase is approved**.
