"""Reusable frequency filtering for mono or multichannel audio samples."""

from __future__ import annotations

import math

import numpy as np
from scipy.signal import butter, sosfiltfilt


def filter_audio(samples, sample_rate, config=None):
    """Apply a zero-phase Butterworth band-pass to frame-first audio samples.

    ``samples`` must be a finite mono array or a frame-by-channel array. The
    returned float32 array has the same shape and does not clip to PCM range.
    ``config`` accepts ``low_hz``, ``high_hz``, and ``order``.
    """
    if config is None:
        options = {}
    elif isinstance(config, dict) and all(isinstance(key, str) for key in config):
        options = dict(config)
    else:
        raise ValueError("config must be a mapping with string keys")
    unknown = options.keys() - {"low_hz", "high_hz", "order"}
    if unknown:
        raise ValueError(f"Unknown filter settings: {', '.join(sorted(unknown))}")
    low_hz = options.get("low_hz", 100.0)
    high_hz = options.get("high_hz", 7000.0)
    order = options.get("order", 4)
    if (
        isinstance(sample_rate, bool)
        or not isinstance(sample_rate, (int, np.integer))
        or sample_rate <= 0
    ):
        raise ValueError("sample_rate must be a positive integer")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in (low_hz, high_hz)
    ):
        raise ValueError("Filter cutoffs must be finite numbers")
    if low_hz <= 0 or high_hz <= low_hz or high_hz >= sample_rate / 2:
        raise ValueError("Filter cutoffs must satisfy 0 < low_hz < high_hz < Nyquist")
    if isinstance(order, bool) or not isinstance(order, int) or not 1 <= order <= 8:
        raise ValueError("Filter order must be an integer from 1 through 8")
    try:
        audio = np.asarray(samples, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Audio samples must be numeric: {exc}") from exc
    if audio.ndim not in (1, 2) or (audio.ndim == 2 and audio.shape[1] not in (1, 2)):
        raise ValueError("Audio must be mono or frame-by-channel mono/stereo")
    if audio.shape[0] == 0 or not np.all(np.isfinite(audio)):
        raise ValueError("Audio must contain finite samples")
    sos = butter(order, (low_hz, high_hz), btype="bandpass", fs=sample_rate, output="sos")
    try:
        return np.asarray(sosfiltfilt(sos, audio, axis=0), dtype=np.float32)
    except ValueError as exc:
        raise ValueError(f"Audio is too short for the configured filter: {exc}") from exc


__all__ = ["filter_audio"]
