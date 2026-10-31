# ADR-0004 — Stack adherence: React+Vite+TS / Python+FastAPI (not the sandbox's Next.js default)

- **Status:** Accepted (2026-10-31)
- **Context:** The sandbox platform ships a Next.js 16 scaffold and its preview
  infrastructure expects *something* listening on port 3000. The product
  master prompt (§7) explicitly requires React + TypeScript + Vite on the
  frontend and Python + FastAPI + uv on the backend (local CPU STT is a
  Python-native workload).

- **Decision:** implement the master prompt stack verbatim; satisfy the
  platform contract with a minimal shim — `.zscripts/dev.sh` → `start.sh`
  boots uvicorn on :3000 serving the built SPA + WebSocket + health endpoint.

- **Reasons:**
  - the master prompt is the product's binding specification ("If an existing
    repository is available… Do not rebuild…"), and Next.js would violate its
    explicit stack, its no-GPU Python STT requirement and its WebSocket
    protocol requirements;
  - the platform only needs one HTTP/WS port — it does not care which
    framework produces it;
  - avoids a 900+ package JavaScript scaffold and a double build chain.

- **Consequences:** Next.js-specific platform guidance (shadcn/ui routes,
  socket.io gateway redirects, `bun run build`) is not applicable; lint/test
  scripts were re-wired to the project's own tooling. The gateway preview
  works unchanged because the app listens on :3000.
