#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# start.sh - single-port production start for the AI Voice Translator.
#
# The FastAPI backend serves:
#   * the built React frontend (frontend/dist) at "/"
#   * the WebSocket audio API at "/ws/audio"
#   * the health endpoint at "/health"
# ...all on ONE port (default 3000 - the only port exposed by the platform
# gateway). This avoids CORS entirely and keeps deployment simple.
#
# Development mode (two processes) is documented in README.md.
# ---------------------------------------------------------------------------
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

PORT="${PORT:-3000}"
mkdir -p .zscripts

# Stop a previous instance so a restart never fights for the port.
if [ -f .zscripts/dev.pid ]; then
  OLD_PID="$(cat .zscripts/dev.pid 2>/dev/null || true)"
  if [ -n "${OLD_PID}" ] && kill -0 "${OLD_PID}" 2>/dev/null; then
    echo "[start.sh] stopping previous app instance (pid ${OLD_PID})"
    kill "${OLD_PID}" 2>/dev/null || true
    sleep 1
  fi
fi

# Backend dependencies (uv-managed virtualenv)
if [ ! -x backend/.venv/bin/python ]; then
  echo "[start.sh] backend/.venv missing -> running 'uv sync'"
  (cd backend && uv sync)
fi

# Frontend build (static assets served by FastAPI)
if [ ! -f frontend/dist/index.html ]; then
  echo "[start.sh] frontend/dist missing -> installing deps and building"
  (
    cd frontend \
      && { [ -d node_modules ] || npm install --no-audit --no-fund; } \
      && npm run build
  )
fi

# Keep the HuggingFace cache inside the project (never write to $HOME).
export HF_HOME="${HF_HOME:-${PROJECT_DIR}/models/huggingface}"
mkdir -p "${HF_HOME}"

echo "[start.sh] starting app on :${PORT} (HF_HOME=${HF_HOME})"
cd backend
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" &
APP_PID=$!
cd ..
echo "${APP_PID}" > .zscripts/dev.pid

term() {
  echo "[start.sh] stopping app (pid ${APP_PID})"
  kill "${APP_PID}" 2>/dev/null || true
}
trap term INT TERM

# Wait until the app answers /health (max 90s), then keep supervising it.
READY=0
for _ in $(seq 1 90); do
  if curl -fsS "http://localhost:${PORT}/health" >/dev/null 2>&1; then
    echo "[start.sh] app healthy on :${PORT} (pid ${APP_PID})"
    READY=1
    break
  fi
  if ! kill -0 "${APP_PID}" 2>/dev/null; then
    echo "[start.sh] ERROR: app process exited during startup - see log above" >&2
    exit 1
  fi
  sleep 1
done

if [ "${READY}" -ne 1 ]; then
  echo "[start.sh] ERROR: app did not become healthy within 90s" >&2
  kill "${APP_PID}" 2>/dev/null || true
  exit 1
fi

wait "${APP_PID}" || true
exit 0
