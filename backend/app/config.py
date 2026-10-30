"""Centralized application configuration (12-factor: env vars + optional .env)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    app_env: str = "dev"
    log_level: str = "INFO"

    # --- STT (local CPU Whisper) ---
    stt_provider: str = "faster-whisper"
    stt_model: str = "base"
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"
    stt_worker_threads: int = 1
    stt_timeout_seconds: float = 30.0
    low_confidence_threshold: float = 0.60

    # --- Audio protocol limits ---
    audio_sample_rate: int = 16_000
    max_chunk_bytes: int = 262_144
    max_utterance_seconds: float = 30.0
    min_utterance_seconds: float = 0.2
    ws_idle_timeout_seconds: float = 180.0

    # --- Sessions / security ---
    max_sessions: int = 50
    cors_origins: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
