"""Validate an optional caller-owned preprocessing boundary before inference."""

import numpy as np

from .audio import TARGET_RATE
from .errors import AudioInputError, ConfigurationError


def validate_audio_preprocessor(value):
    if value is not None and not callable(value):
        raise ConfigurationError("audio_preprocessor must be callable or None")
    return value


def preprocess_chunk(samples, processor):
    """Apply one processor to a complete 16 kHz mono chunk, preserving frames."""
    if processor is None or not samples.size:
        return samples
    try:
        processed = np.asarray(processor(samples.copy(), TARGET_RATE), dtype=np.float32)
        if processed.shape != samples.shape or not np.all(np.isfinite(processed)):
            raise ValueError("Preprocessing must preserve mono frame count and return finite samples")
        return np.ascontiguousarray(processed)
    except Exception as exc:
        raise AudioInputError(f"Audio preprocessing failed: {exc}") from exc
