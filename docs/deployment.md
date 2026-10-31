# Deployment

## 1. Prerequisites

- Python ≥ 3.11,<3.13 with [uv](https://docs.astral.sh/uv/)
- Node ≥ 20 (LTS) with npm
- ~1.2 GB disk (deps + Whisper `small` model), no GPU required

## 2. Single-port production mode (recommended; also the platform default)

```bash
bash start.sh            # serves everything on :3000 (override with PORT=…)
```

`start.sh` idempotently: stops a previous instance → `uv sync` if the venv is
missing → builds the frontend if `frontend/dist` is missing → exports
`HF_HOME=<project>/models/huggingface` → starts uvicorn → waits for
`/health` → supervises the process. Logs land in `.zscripts/dev.log` when
started via the platform shim.

What you get on one origin/port:

| Path | Content |
| --- | --- |
| `/` | built React SPA (`frontend/dist`) |
| `/ws/audio` | audio WebSocket (protocol v1) |
| `/health` | JSON health/readiness |

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
| `HF_HOME` | *(set by start.sh)* | keeps model cache inside the project |
| `HF_ENDPOINT` | *(unset)* | mirror for filtered networks |

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
proxy in self-hosted production), provider data-flow documentation beyond
"audio/text never leaves the box in Phase 1/2" (true today — STT is local;
translation/TTS providers arrive in Phases 3/4 and will be documented then).

**Phase 1 is intended for private/LAN or sandbox deployment, not the public
internet.**

## 7. Troubleshooting

| Symptom | Check |
| --- | --- |
| `/health` shows `stt.loaded: false` | first inference still loading/downloading; watch backend logs |
| Model download fails | set `HF_ENDPOINT=https://hf-mirror.com`; verify disk space |
| Port 3000 busy | `start.sh` auto-kills its previous PID (`.zscripts/dev.pid`); check for foreign processes |
| Browser can't connect WS | must be same-origin or proxied (`/ws`); check `CORS_ORIGINS` if cross-origin |
| Urdu shows as "hi" in transcript | known Whisper ur/hi confusion — see KI-002 and docs/testing.md §4 |
