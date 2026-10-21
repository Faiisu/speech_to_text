"""Process-local Silero VAD loading and speech decisions."""

from __future__ import annotations

import threading
import time

import numpy as np

from .audio import TARGET_RATE
from .errors import VADUnavailableError

_load_lock = threading.Lock()
_inference_lock = threading.Lock()
_timestamps = None
_model = None


def _load_silero():
    global _timestamps, _model
    if _model is None:
        with _load_lock:
            if _model is None:
                try:
                    from silero_vad import get_speech_timestamps, load_silero_vad

                    model = load_silero_vad()
                    _timestamps = get_speech_timestamps
                    _model = model
                except Exception as exc:
                    raise VADUnavailableError(
                        f"Silero VAD could not be loaded: {exc}"
                    ) from exc
    return _model, _timestamps


def detect_speech(samples, options, *, detector=None, clock=None):
    """Return `(has_speech, elapsed_seconds)` for one complete 16 kHz chunk.

    An optional detector is an internal seam for deterministic feature tests.
    Model state is reset before and after each chunk while the cached model is
    protected from concurrent live sources in this process.
    """
    now = clock or time.perf_counter
    started = now()
    if detector is not None:
        found = bool(detector(samples.copy(), options))
        return found, max(0.0, now() - started)

    try:
        import torch

        model, timestamps = _load_silero()
        with _inference_lock:
            reset = getattr(model, "reset_states", None)
            if callable(reset):
                reset()
            try:
                spans = timestamps(
                    torch.from_numpy(np.ascontiguousarray(samples, dtype=np.float32)),
                    model,
                    sampling_rate=TARGET_RATE,
                    threshold=options["threshold"],
                    min_speech_duration_ms=options["min_speech_duration_ms"],
                    speech_pad_ms=0,
                )
            finally:
                if callable(reset):
                    reset()
    except VADUnavailableError:
        raise
    except Exception as exc:
        raise VADUnavailableError(f"Silero VAD failed: {exc}") from exc
    return bool(spans), max(0.0, now() - started)
