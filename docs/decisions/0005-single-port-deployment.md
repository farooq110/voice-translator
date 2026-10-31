# ADR-0005 — Single-port serving: FastAPI hosts the built SPA

- **Status:** Accepted (2026-10-31)
- **Context:** The sandbox exposes exactly one port (3000) behind a gateway;
  split frontend/backend origins would need CORS + a WS proxy hop.

- **Decision:** `frontend/dist` is mounted at `/` by the FastAPI app when it
  exists (StaticFiles, html=True). `/health` and `/ws/audio` are registered
  before the mount, so API routes always win. In dev, Vite proxies `/health`
  and `/ws` to the API server — same-origin from the browser in both modes.

- **Reasons:**
  - zero CORS surface; cookies/origins stay trivial for later auth (Phase 10);
  - one process, one port, one health check for the platform preview;
  - deployment simplifies to "run start.sh".

- **Consequences:** frontend changes require a rebuild for the single-port
  mode (dev mode keeps HMR via Vite); StaticFiles 404s apply to unknown SPA
  paths (acceptable — the app has no client-side routing yet).
