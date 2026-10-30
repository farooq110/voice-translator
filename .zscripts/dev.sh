#!/bin/bash
# ---------------------------------------------------------------------------
# .zscripts/dev.sh - platform dev entry point.
#
# Platform contract (mirrors the standard fullstack scaffold):
#   * started automatically by the sandbox on session init
#   * must serve the app on localhost:3000 (the only externally exposed port,
#     fronted by the Caddy gateway)
#   * output is captured in .zscripts/dev.log, pid written to .zscripts/dev.pid
#
# This project intentionally does NOT use the Next.js scaffold: the product
# master prompt (prompt.md, section 7) mandates React + Vite + TypeScript on
# the frontend and Python + FastAPI on the backend. start.sh boots that stack
# in single-port mode.
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "[$(date '+%F %T')] [dev.sh] launching AI Voice Translator via start.sh"
exec bash "${PROJECT_DIR}/start.sh"
