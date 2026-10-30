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
| `STT_VAD_FILTER` | `true` | Silero VAD trims non-speech before decode |
| `STT_WORKER_THREADS` | `1` | inference executor size |
| `STT_TIMEOUT_SECONDS` | `30` | per-utterance inference cap |
| `MAX_CHUNK_BYTES` | `262144` | per-frame cap |
| `MAX_UTTERANCE_SECONDS` | `30` | buffer cap → auto-flush |
| `WS_IDLE_TIMEOUT_SECONDS` | `180` | zombie-connection reap |
| `MAX_SESSIONS` | `50` | capacity guard |
| `CORS_ORIGINS` | *(empty)* | empty = same-origin only (recommended) |
| `HF_HOME` | *(set by start.sh)* | keeps model cache inside the project |
| `HF_ENDPOINT` | *(unset)* | mirror for filtered networks |

## 6. Security posture (Phase 1 status — honest)

Implemented: same-origin-only serving, no secrets in the frontend, audio
format/payload validation, bounded buffers/queues, session caps + cleanup,
idle timeouts, privacy-conscious logging (no transcript text above DEBUG,
audio never persisted).

Not yet implemented (deliberately later phases): authentication/authorization
(Phase 10), per-session rate limiting beyond size caps (Phase 10), TLS
termination (handled by the platform gateway in the sandbox; use a reverse
proxy in self-hosted production), provider data-flow documentation beyond
"audio/text never leaves the box in Phase 1" (true today — STT is local;
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
