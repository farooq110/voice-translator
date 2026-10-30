"""Application factory — FastAPI app with health, WebSocket API, static SPA.

Single-port mode: when `frontend/dist` exists it is mounted at "/" so the
built React app, the WebSocket API and /health all share one origin/one port
(default 3000 — the only port exposed by the platform gateway).
"""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.health import router as health_router
from .config import Settings, get_settings
from .logging_config import configure_logging
from .sessions.manager import SessionManager
from .stt import build_stt_provider
from .stt.base import STTProvider
from .ws.audio_ws import router as audio_router

APP_VERSION = "0.1.0"

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, stt: STTProvider | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    sessions = SessionManager(max_sessions=settings.max_sessions)
    provider = stt if stt is not None else build_stt_provider(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        executor = ThreadPoolExecutor(
            max_workers=max(1, settings.stt_worker_threads), thread_name_prefix="stt"
        )
        app.state.stt_executor = executor
        app.state.stt = provider
        app.state.sessions = sessions
        app.state.started_at = time.time()
        app.state.version = APP_VERSION
        # Warm the model in the background so /health answers immediately;
        # inference itself is always dispatched to the bounded executor.
        warmup = asyncio.create_task(asyncio.to_thread(provider.ensure_loaded))
        app.state.stt_warmup = warmup
        logger.info(
            "app started version=%s env=%s stt=%s model=%s device=%s",
            APP_VERSION, settings.app_env, provider.name, settings.stt_model, settings.stt_device,
        )
        try:
            yield
        finally:
            warmup.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            logger.info("app stopped")

    app = FastAPI(
        title="AI Voice Translator",
        version=APP_VERSION,
        summary="Real-time voice translation — Phase 1: browser STT MVP",
        lifespan=lifespan,
    )

    if settings.cors_origins.strip():
        origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"] if origins == ["*"] else origins,
            allow_methods=["GET", "POST"],
            allow_headers=[],
            allow_credentials=False,
        )

    app.include_router(health_router)
    app.include_router(audio_router)

    dist_dir = _PROJECT_ROOT / "frontend" / "dist"
    if dist_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="frontend")
    else:
        logger.warning("frontend/dist not found — serving API only (dev mode)")

    return app


app = create_app()
