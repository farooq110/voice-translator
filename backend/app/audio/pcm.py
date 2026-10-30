"""PCM conversion helpers (bytes → model-ready float32 samples)."""

from __future__ import annotations

import numpy as np

TARGET_SAMPLE_RATE = 16_000


def linear_resample(
    samples: np.ndarray, *, orig_rate: int, target_rate: int
) -> np.ndarray:
    """Linear-interpolation resampler (fallback path only; the browser is
    expected to deliver 16 kHz already)."""
    if len(samples) == 0:
        return samples
    target_len = max(1, int(round(len(samples) * target_rate / orig_rate)))
    x_orig = np.arange(len(samples), dtype=np.float64)
    x_target = np.linspace(0.0, len(samples) - 1, num=target_len, dtype=np.float64)
    return np.interp(x_target, x_orig, samples).astype(np.float32)


def pcm16_bytes_to_float32(
    data: bytes,
    *,
    sample_rate: int,
    target_rate: int = TARGET_SAMPLE_RATE,
    channels: int = 1,
) -> np.ndarray:
    """Convert PCM16 little-endian bytes to float32 mono samples in [-1, 1]."""
    if channels != 1:
        raise ValueError("only mono (1 channel) audio is supported in protocol v1")
    samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32_768.0
    if sample_rate != target_rate:
        samples = linear_resample(samples, orig_rate=sample_rate, target_rate=target_rate)
    return samples
