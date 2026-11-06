# ADR-0017: QA hardening — hello-handshake recovery (Phase 6 QA round)

Date: 2026-11-07 · Status: accepted · Phase: 6 (QA round, v0.6.1)

## Context

The independent QA verification of Phase 6 (Journal, 2026-11-07, against
`phase-6` @ b5ddce4 / deployment v0.6.0) verified the compatibility work
(24/26 live checks, developer smoke 8/8 reproduced, frontend 123/123) but
blocked unconditional acceptance with one HIGH defect:

- **BUG-601 (HIGH):** the client could stick in "Reconnecting…" forever
  when the reconnect `hello` was lost. Reproduced twice with independent
  probes against the deployed app; deadlock window observed > 50 s with
  no recovery (only a manual page reload helped). Three contributing
  factors, each verified live:
  1. the client sent `hello` exactly once per socket, at `onopen` — a lost
     hello was never retried;
  2. the server answered `ping` with `pong` even BEFORE `hello` completed,
     so the client's pong-based dead-connection detector never fired and
     the half-alive socket was never recycled;
  3. the server kept hello-less sockets open until the 180 s idle timeout.
  
  Net effect after a mobile network transition (Wi-Fi↔LTE, dead zone): the
  reconnect socket's hello is dropped, pings and pongs flow, `ready` never
  comes, and no new attempt is ever scheduled. Exactly the scenario Phase 6
  targets.
- **BUG-602 (LOW, deployment):** the deployed preview reported
  `/health.version = 0.5.1` on a Phase 6 build (stale working tree). The
  source has carried the correct version since 5c5f2fa; the fix is
  redeployment from the branch tip plus a version assertion in the deploy
  checklist.

## Decision

The handshake must be able to save itself — on BOTH sides, so each side is
safe against a peer from the other era.

**Client (`frontend/src/services/wsClient.ts`):**
1. **Idempotent re-hello.** Until `ready` arrives, the hello is repeated on
   every heartbeat tick (4 s) before the ping. A hello lost in transit is
   therefore retried within one interval without changing the reconnect
   state machine. The server answers a repeated hello with a fresh `ready`
   (see below), so the retry is harmless in every state.
2. **Hello-ack watchdog.** If no `ready` arrives within the pong timeout
   (10 s) after `onopen`, the socket is closed and the normal backoff
   reconnect takes over. This bounds recovery even against an OLD server
   that still answers pings before the handshake: pongs alone must not be
   able to keep a handshake-less socket alive.

**Server (`backend/app/ws/audio_ws.py`):**
3. **No pong before hello.** `ping` frames received before the handshake
   completes are ignored (not answered). A pong now proves the HANDSHAKE,
   not just the socket — an old client whose hello was lost gets silence,
   its own pong detector fires, and it recycles the socket by itself.
4. **Idempotent hello re-ack.** A hello received on an already-hello'd
   connection no longer answers the non-fatal `hello_already_sent` error;
   it is re-acked with a fresh `ready` rebuilt from the stored session and
   room state (same session id, same room, same role). This is what lets
   the client repeat hello safely, and it also recovers a ready event lost
   in transit.
5. **Hello timeout.** A socket that never completes the handshake is closed
   after `ws_hello_timeout_seconds` (new setting, default 15 s) with a
   fatal `hello_timeout` error, instead of holding a session open until the
   180 s idle timeout.

## Consequences

- Every failure mode of the old design now has a bounded recovery path:
  lost hello (client retry ≤ 4 s), lost ready (server re-ack), silent
  pre-hello socket (client watchdog 10 s / server timeout 15 s), dead
  uplink (existing pong detector, unchanged).
- Recovery after the link returns is dominated by one heartbeat interval —
  measured end-to-end ≤ 5 s; the QA acceptance budget was 30 s.
- Old clients paired with the new server regain self-healing (silence
  before hello triggers their pong detector); new clients paired with an
  old server heal via re-hello + watchdog. Neither combination can wedge.
- `hello_already_sent` disappears from the protocol error table; the error
  code `hello_timeout` (fatal) is added. Both changes are documented in
  `docs/audio-protocol.md` (additive; no version bump — no existing
  message shape changed).
- `room_full` / stale-seat rejoin paths share the reconnect machinery and
  stay green (smoke_phase5 12/12, backend suite 190 passed + 1 xfailed).
- Regression coverage: frontend unit tests (re-hello until ready; watchdog
  recycles a pong-only socket), backend tests (duplicate hello re-acks the
  same session, solo AND in-room; ping before hello gets no pong; silent
  socket closed at the hello timeout), and `smoke_phase6.mjs` check M9 —
  a real uplink blackout held past the pong timeout and a reconnect
  attempt, asserting return to Connected.
- BUG-602 needs no code change (the source version is bumped to 0.6.1 by
  this round); the deploy checklist gains a `/health.version` assertion.
