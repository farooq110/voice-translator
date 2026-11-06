# ADR-0015: QA hardening — stale-seat eviction, TTS provisioning integrity, fatal-close defense (Phase 5 QA round)

Date: 2026-11-05 · Status: accepted · Phase: 5 (QA round, v0.5.1)

## Context

The independent QA verification of Phase 5 (Journal, 2026-11-05, against
`phase-5` @ 65942d6 / deployment v0.5.0) accepted the source and the normal
conversation path but blocked acceptance with three defects:

- **BUG-001 (blocker, deployment):** the deployed server had NO usable Piper
  voices — `/health` reported `tts.languages=[]`, and every translated
  utterance answered `tts_unsupported_language`. Root cause chain: HuggingFace
  answers **HTTP 429** for the small `.onnx.json` voice config right after a
  ~63 MB `.onnx` download; the boot-time provisioning run therefore
  "finished" with `.onnx`-only directories, and `start.sh`'s
  directory-is-non-empty check silently accepted the incomplete state.
- **BUG-002 (high):** after an UNCLEAN client drop (tab crash, killed mobile
  browser) the departed participant kept its seat for the full idle timeout
  (~180 s), because the platform preview proxy does not propagate the
  client-side WebSocket close to the backend — `leave_room()` never runs —
  so a returning participant re-helloing with the same code received a
  FATAL `room_full` and the app stopped reconnecting.
- **BUG-003 (low):** server-initiated close frames after fatal protocol
  errors never reach the client through the same proxy (the server-side
  handler DOES close — verified locally), leaving clients on a half-open
  socket until their own heartbeat notices.

## Decision

1. **Provisioning integrity (BUG-001).** `download_tts_voices.py` downloads
   each file with bounded retry/backoff (5 s → 60 s, 5 attempts) — the
   measured 429 bursts recover within one or two retries — and gains a
   `--verify` mode that exits 0 only when EVERY voice has BOTH files
   (`.onnx` + `.onnx.json`, >1 kB). `start.sh` now runs `--verify` BEFORE
   boot and (re-)provisions on any incomplete state; it no longer trusts a
   non-empty directory. Observability (QA suggestion): the app logs a loud
   `TTS PROVISIONING WARNING` at startup when TTS is enabled but no voices
   loaded, and `/health.tts` carries an additive `warning` field for the
   same condition — a voiceless deployment is visible immediately instead
   of being discovered as silent audio mid-call.

2. **Stale-seat eviction (BUG-002).** The server now tracks `last_seen`
   (monotonic) per connection — refreshed on EVERY received frame in the
   endpoint loop. When a hello (host rejoin or guest join) finds its own
   seat occupied, the seat is RECLAIMED if the occupant has sent nothing
   for `room_stale_seat_seconds` (new setting, 12 s default — comfortably
   above the client heartbeat, far below the 180 s idle timeout): the ghost
   receives a fatal `seat_taken` error, is closed server-side (errors
   swallowed), is detached from the room (`ghost.room = None`), and the
   remaining peer gets the normal `peer left` event; the join is then
   retried. A LIVE occupant (recent frames) is never evicted — a second
   tab or third device still answers fatal `room_full` (QA P8 unchanged).
   The reclaiming client's `leave_room()` gained an identity guard
   (`room.members.get(role) is self`): an evicted ghost's eventual cleanup
   can never eject the returning participant or fire a bogus `left`.

3. **Client-side rejoin discipline (BUG-002 mitigation, BUG-003).** The
   reference client heartbeats every **4 s** (was 20 s) so a live member's
   `last_seen` gap is always far under the eviction threshold, and the pong
   deadline is armed ONCE per unanswered ping (a new ping must not extend
   the deadline of a ping that was never answered — otherwise a 4 s
   heartbeat with a 10 s timeout could never detect a dead link). On
   `room_full` DURING a reconnect the client retries via its existing
   backoff for up to 90 s (`REJOIN_GRACE_MS`) with an honest
   "your seat is still being freed" banner instead of dying; a first-join
   `room_full` (genuine third participant) stays fatal immediately.
   `seat_taken` joins the always-fatal room codes.

4. **Fatal-close defense (BUG-003).** The server already closes and tears
   down on every fatal path (verified: close arrives on localhost); the
   remaining gap is purely the proxy's close-frame propagation, which the
   application cannot fix. The client-side hardening above closes its own
   socket on room-fatal errors and its pong timeout reaps a half-open
   socket within ≤14 s; the proxy gap is DOCUMENTED (docs/deployment.md,
   KNOWN_ISSUES KI-021) for the production deployment phase.

## Consequences

- A dropped participant returns within ~12–15 s worst case (eviction
  threshold + one backoff round) instead of ~180 s, without any
  false-positive eviction of live members (heartbeat 4 s ≪ threshold 12 s;
  unit tests cover both directions).
- A partially provisioned TTS directory can no longer reach production
  silently: verify-gated boot + startup log alarm + `/health.tts.warning`.
- The wire protocol stays v1: `seat_taken` is one additive error code;
  `room_full` changes semantics only for reconnecting clients (retry), and
  first-join behavior is unchanged.
- `/health` gains an optional `tts.warning` string (additive; consumers
  that ignore unknown fields are unaffected).
- The proxy close-propagation limitation remains open for real deployments
  (non-preview) — production reverse proxies forward close frames
  correctly; the mitigation layer makes the behavior correct either way.
