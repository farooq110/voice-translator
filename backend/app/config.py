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
    # Model "small": accuracy tier for low-resource languages (Urdu). Measured
    # on the ur fixture (docs/testing.md): base garbles the greeting and drifts
    # mid-sentence; small produces the correct text at RTF ≈ 0.32 on 2 vCPU.
    stt_provider: str = "faster-whisper"
    stt_model: str = "small"
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"
    stt_worker_threads: int = 1
    stt_timeout_seconds: float = 30.0
    low_confidence_threshold: float = 0.60
    # Accuracy decoding (ADR-0007): beam search recovers mid-sentence
    # derailments that greedy decoding cannot; the temperature ladder only
    # engages on quality-gate failures so typical latency is unaffected.
    stt_beam_size: int = 5
    stt_vad_filter: bool = True
    stt_vad_min_silence_ms: int = 500
    stt_vad_speech_pad_ms: int = 250

    # --- Server-side VAD gate (Phase 2, ADR-0009) ---
    # Silero VAD (bundled with faster-whisper) runs BEFORE the STT model:
    # silence/noise is rejected without decoding, utterances are edge-trimmed
    # to their speech region, and over-long buffers are split at natural
    # pauses. `vad_gate_enabled=False` restores the Phase 1 pass-through.
    vad_gate_enabled: bool = True
    vad_threshold: float = 0.5
    # Speech shorter than this is treated as noise (clicks, breaths).
    vad_min_speech_ms: int = 120
    # Real audio kept around the detected speech region when trimming.
    vad_trim_pad_ms: int = 200
    # A pause this long may split an utterance into separate decodes.
    vad_split_silence_ms: int = 700
    # Speech span that always forces a split (bounds a single decode call).
    vad_split_max_seconds: float = 24.0

    # --- STT admission / backpressure (Phase 2) ---
    # Global cap on utterances admitted for decoding (the executor queue is
    # unbounded by itself). Overflow answers `server_busy` — backpressure.
    stt_max_queue_depth: int = 16
    # Per-session cap on simultaneously decoding utterances.
    stt_max_pending_per_session: int = 4

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
