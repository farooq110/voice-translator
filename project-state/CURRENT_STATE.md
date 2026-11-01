# CURRENT_STATE

_Last updated: 2026-11-01 — Phase 3 complete (translation decision engine, ADR-0012)_

## Phase

**Phase 3 — Translation Decision Engine and Bidirectional Translation** —
complete (2026-11-01, ADR-0012; user approval "After that start the phase 3
and complete it"). Phase 1 (+KI-006/007/008), Phase 2 (+KI-009/010/011, QA
round) remain complete. Work is on branch `phase-3` (created from `main`
after merging `phase-2` per the user's instruction); nothing pushed — the
SSH key from the GitHub round was wiped by a sandbox recycle, and pushing
waits for the user's explicit branch-named instruction (strict rule §2).

## Current task

None — Phase 3 finished. The decision engine implements the master prompt
Rules 1–4 for any configured pair; detection is "detect-first,
pin-the-decode" (standalone detect_language + ur/hi cluster + threshold,
then ONE pinned decode — the full auto-decode was measured unusable for
Urdu); translation runs offline by default via CTranslate2 OPUS-MT models
(ur↔en, es↔en) behind a provider abstraction with fake/libre/none
alternatives; protocol v1 gained the additive `decision` field, the
`translation` event and three utterance-scoped error codes; the UI shows a
direction badge or an honest pass-through chip per line with RTL-aware
translated text. All suites green: backend 128+1xfail, frontend 57,
live translation E2E 3/3 (real speech + real MT), continuous speech 5/5
≤ 4.0 s with the detect pass included, synthetic 3/3, silence guard PASS,
browser smoke 10/10. APP_VERSION 0.3.0.

## Completed work (Phase 3, 2026-11-01)

- **Decision engine (ADR-0012):** pure `DecisionEngine` — Rules 1–4 for any
  configured pair, provider-supports consulted only for would-be
  translations; honest pass-throughs (identical config, low confidence,
  undetected, unsupported pair, too-long text).
- **Detection "detect-first, pin-the-decode":** standalone
  `detect_language` (measured en 0.997 / es 0.989 / ur→hi 0.775) → ur/hi
  cluster + 0.60 threshold + native fallback → ONE decode pinned to the
  resolved language (full auto-decode measured unusable: `ar` p=0.97, mixed
  scripts, 3–4× slower); 8 s detection cap (the ur greeting's first 2.5 s
  detects `ar` p=0.93 — full-utterance context is required).
- **Providers:** `local` offline CT2 OPUS-MT (ur↔en + es↔en, ~900 MB via
  `scripts/download_translation_models.py`, measured EOS framing fix),
  `fake`, `libre` (MockTransport-tested), `none`; keyless web providers
  probed and rejected (documented).
- **Protocol v1 additive:** `transcription.decision`, `translation` event,
  `translation_failed/timeout/rate_limited` (utterance-scoped, non-fatal).
- **Bounded execution:** dedicated thread, 30/min rate limit, ≤2 concurrent,
  8 s timeout, stale-drop + cancellation, 2000-char cap.
- **Frontend:** translation flag, direction badges / pass-through chips,
  RTL-aware translated text, per-line error chips; 16 new tests.
- **Verification:** backend 128+1xfail; frontend 57 + tsc/lint/build clean;
  live translation E2E 3/3 (real speech, real MT, 144–172 ms);
  continuous speech 5/5 ≤ 4.0 s incl. detection; synthetic 3/3; silence
  guard PASS; browser smoke 10/10. APP_VERSION 0.3.0.

## Completed work (QA fix round, 2026-11-01)

- **BUG-001 (KI-010, ADR-0011):** protocol v1 gains optional
  `flush.utterance_id` (client-generated) echoed on `transcription.id` and
  every flush-scoped `error.utterance_id`; client `FlushTracker` settles
  per-flush by id and the Transcribing… indicator is derived from the map —
  the audit's two stuck-indicator modes are structurally impossible.
- **BUG-002 (KI-011, ADR-0011):** true push-to-talk when the toggle is OFF —
  pause/soft-cap auto-flushes suppressed, flush on stop (tail capture kept,
  20 s safety cap); honest toggle/status wording.
- **OBS-005 (partial):** `server_busy` = dropped-by-design + queue-depth
  hint; protocol client obligation updated (no re-flush).
- **OBS-003:** `.env` untracked (gitignored already).
- **OBS-004:** README image link → tracked `docs/screenshots/phase2/`;
  `frontend/scripts/smoke_phase2.mjs` re-provisioned (playwright, fake mic)
  and re-run **10/10** (zero console errors); desktop + mobile screenshots
  committed to the repo.
- Docs: ADR-0011; audio-protocol.md (utterance_id, backpressure semantics,
  client obligations); testing.md §2/§3/§7/§9; KI-010/011 RESOLVED;
  CHANGELOG 0.2.2; TASKS QA round; DECISIONS D-013; APP_VERSION 0.2.2.

## Completed work (KI-009 round, 2026-11-01)

- **Continuous-speech latency (ADR-0010):** `useTranslatorSession.ts`
  endpointing v2 — soft cap 4.5 s closes the segment at the next ≥ 300 ms
  micro-pause; hard cap 20 s → 9 s (worst-case blob ≈ 3 s decode). The 800 ms
  sentence-pause rule is unchanged for normal speech.
- **Transcript UX:** newest sentence renders at the TOP of the panel
  (chronological state unchanged); scroll-to-bottom removed — the user's
  second request this round. The per-line ✕ delete button from ADR-0008 is
  unaffected.
- **Transcribing… indicator:** pending-decode counter so overlapping flushes
  (the new normal during continuous speech) never clear it early.
- **`scripts/e2e_continuous_speech_check.py`:** live old-vs-new endpointing
  benchmark on 22.5 s of compressed-silence Urdu speech with a fixture-sanity
  gate; measured first text 22.2 s (old) → **6.6 s (new)**, 5/5 utterances
  correct, flush→text 1.9–2.2 s (docs/testing.md §5b).
- **Backup:** recreated the lost `download/Phase-One.zip` (the sandbox wiped
  `download/` between sessions) from git — Phase-1-final tree @ `3c1b5c2`
  (tag `phase-one-final`) +
  full git-history bundle + MANIFEST; integrity verified.
- Docs: ADR-0010; testing.md §1/§2/§3/§5b/§6/§7/§9; KI-009 RESOLVED;
  CHANGELOG 0.2.1; TASKS round table; DECISIONS D-012; APP_VERSION 0.2.1.

### GitHub setup round (2026-11-01)

- Repo-local `user.name` / `user.email` set to the user's Repository
  Details (`m Farooq` / khanbhi88@gmail.com) and verified before any
  commit or push (user rule §3).
- Pre-publish history alignment: all 11 commits' author/committer set
  to `m Farooq <khanbhi88@gmail.com>` (the repo had never been pushed;
  `filter-branch`; tag `phase-one-final` re-pointed to `3c1b5c2`).
- 14 working-tree files carried accidental +x mode bits (content
  unchanged); normalized back to 644.
- `origin` → `https://github.com/farooq110/voice-translator.git`;
  branch `phase-2` created from `main` @ `c03ac21`.
- `download/Phase-One.zip` recreated again after the sandbox wiped
  `download/`: Phase-1-final tree (tag `phase-one-final`) + git-history
  bundle + MANIFEST, integrity verified.

## Verified work (evidence)

- `backend`: `uv run pytest` → **69 passed, 1 xfailed** (xfail = documented
  KI-002 auto-detect behavior; +4 ADR-0011 regression tests).
- `frontend`: `tsc --noEmit` ✓ · `eslint --max-warnings 0` ✓ · `vitest run` →
  **41 passed** (+8: flushTracker ×5, protocol/wsClient utterance-id ×3) ·
  `vite build` ✓ (244.9 kB JS / 77 kB gzip).
- Live server v0.2.2 (restarted): `/health` 200, `vad.enabled=true`,
  `stt_queue {queued:0,max:16}`.
- `scripts/e2e_synthetic_check.py` → **3/3 PASS**; `silence_guard_check.py` →
  **PASS**; `e2e_continuous_speech_check.py` → **PASS** (5/5 utterances,
  first text 6.28 s, flush→text 1.84–1.98 s, all correct Urdu — §5b).
- Browser smoke `frontend/scripts/smoke_phase2.mjs` → **10/10**, zero
  console/page errors; screenshots committed (`docs/screenshots/phase2/`).
- Git round: `git config user.email` / `user.name` verified against the
  user's Repository Details; `git log` shows all commits authored
  `m Farooq <khanbhi88@gmail.com>`; tag `phase-one-final` → `3c1b5c2`.
- Push: SSH auth via the user-provided key (fingerprint `SHA256:2lsCE9…`)
  with a paramiko shim (`core.sshCommand`); GitHub host-key fingerprints
  pinned (ED25519/ECDSA/RSA); `git ls-remote origin` shows `refs/heads/main`
  and `refs/heads/phase-2`; the web-UI bootstrap README commit superseded.
  The QA-fix-round commit is LOCAL (strict rule: push only on the user's
  explicit command).

## Actual test results

See `docs/testing.md` §2–§9 (updated 2026-11-01 after ADR-0011).

## Known issues

`project-state/KNOWN_ISSUES.md`: KI-001/005/006/007/008/009/010/011
RESOLVED; KI-002 mitigated (auto-detect only; Phase 3 design input); KI-003
accepted; KI-004 OPEN (real devices — Phase 6). OBS-005 remainder (WS origin
allow-listing, authentication) deferred to Phase 10 per the audit.

## Blockers

None.

## Next task

**Phase 3 — Translation Decision Engine and Bidirectional Translation** —
NOT started; requires the user's explicit approval of the Phase 2 completion
report.

## Phase approval required?

**YES** — per master prompt §23 the Phase 2 completion report ends with the
question _"The current phase has been completed and reviewed. Do you approve
starting the next phase?"_; no Phase 3 work may begin until the user answers
affirmatively.
