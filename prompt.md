# prompt.md — Phase 7 Scope & Delivery Record

**Phase:** 7 of 10 — Zoom integration (first meeting app)
**Status:** implemented — full verification round complete — **pending user approval** (§23 workflow)
**Version:** v0.7.0
**ADR:** [docs/decisions/0019-zoom-rtms-bridge.md](docs/decisions/0019-zoom-rtms-bridge.md)

> This repository documents the **current phase only** in this file. The
> complete master prompt (25 sections, 10-phase roadmap, approval workflow)
> is maintained by the product owner outside the repository. The overall
> delivery roadmap and phase statuses live in
> [README.md § Roadmap](README.md#roadmap-master-prompt-17); durable agent
> rules live in [AGENT.md](AGENT.md). Branch-local deep dives: `PHASE-3.md`
> (phase-3 branch), `PHASE-4.md` (phase-4 branch), `PHASE-5.md` (phase-5
> branch), `PHASE-6.md` (phase-6 branch), `PHASE-7.md` (phase-7 branch) —
> each documents only its own phase. (Previous phases' records live on in
> their branch docs and the CHANGELOG; this file was the Phase 6 record
> until 2026-11-10.)

---

## 1. Phase 7 scope (master prompt §17)

"Zoom integration (first meeting app)" — connect the verified translation
pipeline to Zoom meetings. The roadmap's meeting phase, built on top of the
complete browser MVP (phases 1–5) and the mobile-honest capture (phase 6).

## 2. Design (ADR-0019)

- **The bridge orchestrates; the pipeline stays the product.** Zoom's
  Real-Time Media Streams (RTMS — the documented WebSocket media plane for
  AI notetakers/translation, no bot participant) delivers the meeting's
  mixed audio; the bridge feeds it into the app's OWN `/ws/audio` endpoint
  as an ordinary protocol-v1 client — one code path, every Phase 1–6
  behavior (Silero gating, segmentation, decision engine, pass-through
  relay, Piper voice, rooms) applies unchanged. AGENT.md §4's rule (engine
  independent of meeting adapters) is preserved by construction.
- **Webhook-driven lifecycle.** `POST /zoom/webhook` answers
  `endpoint.url_validation` with the exact HMAC challenge response,
  verifies `x-zm-signature` (v0) on every delivery when
  `zoom_secret_token` is configured, acknowledges `meeting.rtms_started`
  200-immediately (Zoom's documented duplicate-connection gotcha) and
  spawns one `ZoomMeetingWorker` per meeting; `meeting.rtms_stopped`
  (uuid-only payloads tolerated) stops it with the graceful
  `STREAM_CLOSE_REQ`.
- **Raw L16 audio — no codec dependency.** The media handshake requests
  uncompressed PCM16 at 16 kHz mono (20 ms send rate) — byte-for-byte the
  pipeline's native format; the spec's G.711/G.722/Opus paths are simply
  not requested. Keep-alives (12→13) are answered on both sockets; a
  bounded drop-on-full queue decouples the RTMS loop from a slow consumer.
- **The human is the room's guest.** The bridge hosts a normal
  two-participant room (pair from `zoom_native_language`/
  `zoom_preferred_language`, platform tag `zoom-bridge`); the meeting's
  invite is published at `GET /zoom/meetings`. The translator user opens
  `…/?room=CODE` and gets the full Phase 5 experience for the meeting:
  speaker-tagged transcript, translations, and the translated voice on
  their own device — routed only to the listener. Nothing is injected
  back INTO the meeting in v1 (RTMS has no per-listener addressing;
  KI-031).
- **OFF unless configured.** Routes mount only when `zoom_enabled` AND
  client credentials exist; disabled → 404/SPA-catch-all and
  `/health.zoom.enabled=false`. Secrets live only in backend env vars;
  meeting UUIDs are masked in logs.

## 3. Delivery record (what was built)

Backend only (frontend untouched): `app/zoom/signing.py` (pure crypto),
`app/zoom/rtms.py` (`RtmsClient`), `app/zoom/pipeline_client.py`
(`PipelineClient`), `app/zoom/bridge.py` (`UtteranceSegmenter`,
`ZoomMeetingWorker`, `ZoomBridge`), `app/zoom/router.py`; config block +
`/health` zoom section; lifespan shutdown closes workers. Tests: 30 new
(signing vectors, webhook enforcement, segmenter, RTMS protocol edges,
bridge integration over the fake RTMS + fake pipeline server, HTTP
surface). Tooling: `app/zoom/fake_rtms.py` (signature-verifying fake of
Zoom's edge) + `scripts/e2e_zoom_check.py` (live E2E).

## 4. Verification (measured 2026-11-10)

| Check | Result |
| --- | --- |
| Backend suite (+30 zoom tests) | **220 passed + 1 xfailed** |
| Frontend suite (untouched) | **139/139**, tsc/ESLint/build clean (264.01 kB) |
| `scripts/e2e_zoom_check.py` (live :3000, fake Zoom edge) | **5/5 PASS (17.9 s)** — url_validation; worker live + invite published; RTMS handshake (signature verified, L16/16 kHz/mono); 6.6 s ur fixture → 3 utterances → guest transcription(speaker=host) + real ur→en translation + 111,618 B routed PCM; graceful stop + STREAM_CLOSE_REQ + `/health` zoom back to zero |
| Regression: e2e translation / two-participant / TTS | **3/3 / 4/4 / 3/3** |
| Regression: smokes phase2 / phase5 / phase6 | **10/10 / 12/12 / 9/9** |
| Silence guard (hallucination) | **PASS** |
| `/health.version` | **0.7.0** |

## 5. Honest boundaries

- **Zoom's production edge is NOT verified** — the sandbox has no Zoom
  account or Marketplace app. The fake implements the documented protocol;
  the client is defensive about the fields Zoom does not publish in full
  (media-URL key, frame encoding). The real-Zoom checklist is
  `docs/deployment.md §6`; tracked as KI-029.
- Meeting-audio endpointing is coarser than the browser's (RMS pause +
  6 s soft cap; the server VAD still gates/trims/splits) — KI-030.
- No captions or voice are sent INTO the meeting in v1 (per-listener
  addressing does not exist; the product's routing rule wins) — KI-031.
- Room model unchanged: the meeting bridge occupies the host seat; a
  server restart ends rooms and the bridge re-hellos into a fresh one
  (the old invite dies with the room — same as browser rooms, Phase 5).
