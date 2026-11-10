# Deployment

## 1. Prerequisites

- Python ≥ 3.11,<3.13 with [uv](https://docs.astral.sh/uv/)
- Node ≥ 20 (LTS) with npm
- ~2 GB disk (deps + Whisper `small` + Phase 3 offline MT models), no GPU required

## 2. Single-port production mode (recommended; also the platform default)

```bash
bash start.sh            # serves everything on :3000 (override with PORT=…)
```

`start.sh` idempotently: stops a previous instance → `uv sync` if the venv is
missing → builds the frontend if `frontend/dist` is missing → exports
`HF_HOME=<project>/models/huggingface` → **provisions the offline MT
translation models if `models/translation/` is empty (Phase 3, BUG-003 — see
§4)** → starts uvicorn → waits for `/health` → supervises the process. Logs
land in `.zscripts/dev.log` when started via the platform shim.

What you get on one origin/port:

| Path | Content |
| --- | --- |
| `/` | built React SPA (`frontend/dist`) |
| `/ws/audio` | audio WebSocket (protocol v1) |
| `/health` | JSON health/readiness |

**Deploy checklist (QA round v0.6.2):** after every redeploy,
assert the running build matches the branch tip —
`curl -fsS localhost:3000/health | grep -o '"version":"…"'` must equal the
`APP_VERSION` in `backend/app/main.py` (0.6.2 as of this round). A version
mismatch means the deployment is running a stale/partially synced tree;
rebuild (`cd frontend && npm run build`) and restart `start.sh` before any
QA conclusions are drawn from that deployment.

## 3. Development mode (two processes)

```bash
cd backend && uv sync && uv run uvicorn app.main:app --port 8000 --reload
cd frontend && npm install && npm run dev   # Vite :5173, proxies /health + /ws → :8000
```

## 4. Model management

```bash
cd backend
HF_HOME=../models/huggingface uv run python scripts/download_model.py --model small
# Behind a filtered network:
HF_ENDPOINT=https://hf-mirror.com HF_HOME=../models/huggingface \
  uv run python scripts/download_model.py --model small
```

The model also auto-downloads on first use (lazily, in the background).
`STT_MODEL` selects the size (`tiny`|`base`|`small`). Default `small` —
accuracy tier for low-resource languages like Urdu (ADR-0007).

### 4.1 Translation models (Phase 3 — required for the local provider)

The `local` translation provider (default) discovers its language pairs by
scanning `models/translation/<src>-<tgt>/` at startup. **This directory is a
git-ignored runtime artifact — a fresh checkout has zero pairs**, and without
provisioning every translation decision honestly degrades to pass-through
(`unsupported_pair`). Provision all four directions (~600 MB total):

```bash
cd backend
uv run python scripts/download_translation_models.py
# subset: uv run python scripts/download_translation_models.py --only ur-en,en-ur
# Behind a filtered network: HF_ENDPOINT=https://hf-mirror.com uv run …
```

| Pair | Source repo | Footprint |
| --- | --- | --- |
| `ur-en` / `en-ur` | gaudi/opus-mt-*-ctranslate2 | ~100 MB each |
| `es-en` / `en-es` | michaelfeil/ct2fast-opus-mt-*-es-en | ~100 MB each |

The script is **idempotent** (present, non-empty files are skipped) and is
run automatically by `start.sh` on first boot when `models/translation/` is
empty (`TRANSLATION_PROVIDER=local`, the default). If that automatic
provisioning fails (e.g. no network), `start.sh` logs a loud ERROR and the
app still starts — STT/VAD keep working, translation passes through, and
`/health` makes the gap visible: `translation.pairs: []` (BUG-003
observability fix). Re-run the script and restart to recover full
translation.

### 4.2 TTS voices (Phase 4 — required for spoken translations)

The `piper` TTS provider (default) discovers its voices by scanning
`models/tts/<language>/` at startup. **This directory is a git-ignored
runtime artifact — a fresh checkout has zero voices**, and without
provisioning every translated sentence stays text-only with a visible
`tts_unsupported_language` chip. Provision all three (~190 MB total):

```bash
cd backend
uv run python scripts/download_tts_voices.py
# subset: uv run python scripts/download_tts_voices.py --only ur,en
```

| Language | Voice (rhasspy/piper-voices) | Footprint |
| --- | --- | --- |
| `ur` | ur_PK-fasih-medium | ~65 MB |
| `en` | en_US-lessac-medium | ~63 MB |
| `es` | es_MX-ald-medium | ~63 MB |

The script is **idempotent** and is run automatically by `start.sh` on first
boot when `models/tts/` is empty (`TTS_PROVIDER=piper`, the default). If
that automatic provisioning fails (e.g. no network), `start.sh` logs a loud
ERROR and the app still starts — STT/translation keep working, and `/health`
makes the gap visible: `tts.languages: []`. Re-run the script and restart to
recover spoken output. Voices are loaded lazily per language: the FIRST
translation into a language pays ~1.5 s of voice load (measured), later ones
synthesize at RTF ≈ 0.06–0.08.

## 5. Environment variables (backend)

All in `backend/.env.example`; highlights:

| Var | Default | Purpose |
| --- | --- | --- |
| `STT_MODEL` | `small` | Whisper size |
| `STT_DEVICE` / `STT_COMPUTE_TYPE` | `cpu` / `int8` | CTranslate2 runtime |
| `STT_BEAM_SIZE` | `5` | beam width (ADR-0007 accuracy) |
| `STT_VAD_FILTER` | `true` | Silero VAD trims non-speech inside decode |
| `STT_WORKER_THREADS` | `1` | inference executor size |
| `STT_TIMEOUT_SECONDS` | `30` | per-utterance inference cap |
| `VAD_GATE_ENABLED` | `true` | **Phase 2 (ADR-0009)** server-side Silero gate before STT |
| `VAD_THRESHOLD` | `0.5` | Silero speech probability threshold |
| `VAD_MIN_SPEECH_MS` | `120` | shorter speech = noise → gated |
| `VAD_TRIM_PAD_MS` | `200` | audio kept around the speech region when trimming |
| `VAD_SPLIT_SILENCE_MS` | `700` | pause length that may split an utterance |
| `VAD_SPLIT_MAX_SECONDS` | `24` | speech span that forces a split (bounds a decode call) |
| `STT_MAX_QUEUE_DEPTH` | `16` | global decode admission cap → `server_busy` backpressure |
| `STT_MAX_PENDING_PER_SESSION` | `4` | per-session decode cap → `server_busy` backpressure |
| `MAX_CHUNK_BYTES` | `262144` | per-frame cap |
| `MAX_UTTERANCE_SECONDS` | `30` | buffer cap → auto-flush |
| `WS_IDLE_TIMEOUT_SECONDS` | `180` | zombie-connection reap |
| `MAX_SESSIONS` | `50` | capacity guard |
| `CORS_ORIGINS` | *(empty)* | empty = same-origin only (recommended) |
| `TRANSLATION_PROVIDER` | `local` | `local` / `fake` / `libre` / `none` (Phase 3) |
| `TRANSLATION_DETECT_MODE` | `auto` | `auto` or `pinned` (ADR-0012) |
| `TRANSLATION_MODELS_DIR` | `models/translation` | offline MT pair scan root |
| `TTS_PROVIDER` | `piper` | `piper` / `fake` / `none` (Phase 4, ADR-0013) |
| `TTS_VOICES_DIR` | `models/tts` | offline voice scan root (`<lang>/*.onnx`) |
| `TTS_TIMEOUT_S` | `10` | synthesis hard cap |
| `TTS_RATE_LIMIT_PER_MINUTE` | `30` | sliding-window synthesis cap |
| `TTS_MAX_CONCURRENT` | `1` | synthesis is CPU-bound |
| `TTS_MAX_TEXT_CHARS` | `800` | longer texts are not spoken (text kept) |
| `ROOM_LINGER_SECONDS` | `120` | Phase 5: how long an empty room survives for reconnection (ADR-0014) |
| `TTS_WORKER_THREADS` | `1` | synthesis executor size |
| `HF_HOME` | *(set by start.sh)* | keeps model cache inside the project |
| `HF_ENDPOINT` | *(unset)* | mirror for filtered networks |
| `ZOOM_ENABLED` | `false` | **Phase 7 (ADR-0019)** master switch for the Zoom RTMS bridge |
| `ZOOM_CLIENT_ID` / `ZOOM_CLIENT_SECRET` | *(empty)* | Zoom General App OAuth credentials — sign the RTMS handshake; bridge stays OFF without them |
| `ZOOM_SECRET_TOKEN` | *(empty)* | webhook validation token — answers `endpoint.url_validation` and verifies `x-zm-signature`; empty = verification skipped (dev only) |
| `ZOOM_NATIVE_LANGUAGE` / `ZOOM_PREFERRED_LANGUAGE` | `ur` / `en` | the meeting rooms' language pair (the bridge is the room's host) |
| `ZOOM_WS_URL` | `ws://127.0.0.1:3000/ws/audio` | where the bridge's internal protocol-v1 client connects (override in dev mode :8000) |
| `ZOOM_PAUSE_MS` / `ZOOM_SOFT_CAP_S` / `ZOOM_RMS_THRESHOLD` | `600` / `6.0` / `300` | meeting-audio endpointing (KI-030) |

Frontend build-time flag: `VITE_FEATURE_HANDS_FREE` (default `true`) — set
`false` to remove the hands-free toggle from the built SPA (ADR-0009).

## 6. Security posture (Phase 2 status — honest)

Implemented: same-origin-only serving, no secrets in the frontend, audio
format/payload validation, bounded buffers/queues (per-session and global
decode admission caps with `server_busy` backpressure — ADR-0009), server-side
VAD gate (silence/noise never decoded), session caps + cleanup, idle
timeouts, privacy-conscious logging (no transcript text above DEBUG, audio
never persisted).

Not yet implemented (deliberately later phases): authentication/authorization
(Phase 10), per-session rate limiting beyond the admission caps (Phase 10),
TLS termination (handled by the platform gateway in the sandbox; use a reverse
proxy in self-hosted production).

Data flow (honest, per phase): STT (Whisper), VAD and the Phase 3 `local`
translation provider are **fully on-box** — audio and transcript text never
leave the machine. The optional `libre` cloud provider (off by default)
sends **translated text only**, never audio, to the LibreTranslate instance
you configure. TTS arrives in Phase 4 and will be documented when
implemented.

**Phase 1 is intended for private/LAN or sandbox deployment, not the public
internet.**

## 7. Troubleshooting

| Symptom | Check |
| --- | --- |
| `/health` shows `stt.loaded: false` | first inference still loading/downloading; watch backend logs |
| UI shows "pass-through · pair unavailable" on every utterance | `/health` → `translation.pairs` is `[]` or missing the pair — provision MT models (§4.1) and restart |
| `translation.pairs: []` after restart | provisioning failed (check start.sh output); run `backend/scripts/download_translation_models.py` manually |
| Model download fails | set `HF_ENDPOINT=https://hf-mirror.com`; verify disk space |
| Port 3000 busy | `start.sh` auto-kills its previous PID (`.zscripts/dev.pid`); check for foreign processes |
| Browser can't connect WS | must be same-origin or proxied (`/ws`); check `CORS_ORIGINS` if cross-origin |
| Urdu shows as "hi" in transcript | known Whisper ur/hi confusion — see KI-002 and docs/testing.md §4 |

## 6a. Zoom integration (Phase 7, ADR-0019) — real-app runbook

The bridge ships verified against a fake of Zoom's edge (KI-029). Turning
it on against REAL Zoom is a human checklist — every step needs a Zoom
account, which the sandbox does not have:

1. **Create the app** — marketplace.zoom.us → Develop → Build App →
   **General App**, user-managed. Copy the Client ID and Client Secret.
2. **Enable RTMS** — request RTMS access for the app if your account
   requires it (Zoom gates RTMS behind a request).
3. **Event subscription** — add a webhook endpoint
   `https://<your-deployment>/zoom/webhook` and subscribe to
   `meeting.rtms_started` and `meeting.rtms_stopped`. Copy the **Secret
   Token**.
4. **Scopes** — add `meeting:read:meeting_audio` (+ transcript scope if
   you later enable Zoom's own transcript stream).
5. **Configure the backend** — set `ZOOM_ENABLED=true`,
   `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`, `ZOOM_SECRET_TOKEN`; verify
   `/health` shows `"zoom": {"enabled": true, ...}`.
6. **Zoom validates the webhook** — the app answers the
   `endpoint.url_validation` challenge automatically. If a proxy rewrites
   the public URL, set `ZOOM_WEBHOOK_URL_OVERRIDE` so the v0 signature
   still matches.
7. **First live meeting** — start a meeting as the app-installing host,
   start the RTMS stream from the Zoom client, then:
   `curl -s https://<deployment>/zoom/meetings` → open the invite link
   (`…/?room=CODE`) on your device. Speech should appear in the room's
   transcript within a few seconds (segmenter soft cap 6 s).
8. **Record the results here** — handshake field names observed
   (`content.media_url` vs alternatives), audio frame encoding
   (base64-in-JSON vs binary), keep-alive cadence — and flip KI-029 to
   RESOLVED. Stop the stream and confirm
   `curl -s https://<deployment>/health | jq .zoom.active_meetings`
   returns to 0.
