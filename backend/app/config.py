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

    # --- Translation decision engine (Phase 3, ADR-0012) ---
    # `local`  — offline CTranslate2 OPUS-MT models under
    #            `translation_models_dir` (provision with
    #            backend/scripts/download_translation_models.py); no keys,
    #            no network, text never leaves the machine.
    # `fake`   — deterministic stub (`[fake:src→tgt] …`) for tests/demos.
    # `libre`  — LibreTranslate HTTP API (LIBRETRANSLATE_URL + optional key).
    # `none`   — translation disabled: no decisions, Phase 1/2 transcripts.
    translation_provider: str = "local"
    # `auto`  = Whisper detects the language per utterance; the decision engine
    #           normalizes it (script inspection + ur/hi cluster, KI-002) and
    #           gates low-confidence labels into pass-through (ADR-0012).
    # `pinned` = assume the configured native language (Phase 1/2 behavior —
    #           faster and deterministic, but Rule 2 can never fire).
    translation_detect_mode: str = "auto"
    translation_confidence_threshold: float = 0.60
    translation_timeout_s: float = 8.0
    translation_rate_limit_per_minute: int = 30
    translation_max_concurrent: int = 2
    translation_max_text_chars: int = 2000
    translation_worker_threads: int = 1
    translation_models_dir: str = "models/translation"
    libretranslate_url: str = ""
    libretranslate_api_key: str = ""

    # --- Text-to-speech (Phase 4, ADR-0013) ---
    # `piper` — offline Piper neural voices under `tts_voices_dir`
    #           (one directory per language, e.g. models/tts/ur/); provision
    #           with backend/scripts/download_tts_voices.py. No keys, no GPU,
    #           no network; audio never leaves the machine.
    # `fake`  — deterministic sine-tone stub for tests/demos.
    # `none`  — TTS disabled: translated text is displayed, never spoken.
    tts_provider: str = "piper"
    tts_timeout_s: float = 10.0
    tts_rate_limit_per_minute: int = 30
    tts_max_concurrent: int = 1
    # Synthesizing a novel-length text would produce minutes of audio; texts
    # above this bound keep their translated text but are not spoken.
    tts_max_text_chars: int = 800
    tts_worker_threads: int = 1
    tts_voices_dir: str = "models/tts"

    # --- Sessions / security ---
    max_sessions: int = 50
    cors_origins: str = ""

    # --- Two-participant rooms (Phase 5, ADR-0014) ---
    # How long an empty room survives after its last member leaves — the
    # reconnection window in which a dropped client can rejoin with the same
    # invite code. 0 disables lingering (rooms die with their last member).
    room_linger_seconds: float = 120.0
    # QA round (BUG-002): a proxy in front of the server can swallow a dead
    # client's disconnect, so a seat may stay occupied by a GHOST long after
    # the participant is gone (the app-level idle timeout only fires at
    # ws_idle_timeout_seconds=180). When a rejoin finds its own seat occupied
    # by a member that has sent NOTHING for longer than this, the seat is
    # reclaimed for the returning participant. Must stay well above the
    # client heartbeat (4 s ping interval) so a live-but-quiet member is
    # never evicted.
    room_stale_seat_seconds: float = 12.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
