"""Bounded PCM input source for audio captured by a trusted host-side bridge."""

from __future__ import annotations

import numpy as np

from .errors import AudioInputError


class RemoteAudioSource:
    """Adapt little-endian float32 PCM batches to the microphone session source contract."""

    def __init__(self, *, device, on_audio, on_error, sample_rate):
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or not 8000 <= sample_rate <= 48000:
            raise AudioInputError("Remote microphone sample_rate must be an integer in [8000, 48000]")
        self.sample_rate = sample_rate
        self.channels = 1
        self._on_audio = on_audio
        self._on_error = on_error
        self._closed = False
        self.started = False

    def start(self):
        self.started = True

    def feed(self, pcm):
        if self._closed:
            raise AudioInputError("Remote microphone session is closed")
        if not pcm or len(pcm) > self.sample_rate * 4:
            raise AudioInputError("Remote microphone batch must contain at most one second of float32 PCM")
        if len(pcm) % 4:
            raise AudioInputError("Remote microphone batch is not aligned to float32 samples")
        frames = np.frombuffer(pcm, dtype="<f4")
        if not np.isfinite(frames).all():
            raise AudioInputError("Remote microphone batch contains non-finite samples")
        self._on_audio(frames.copy())

    def stop(self):
        self._closed = True
        self.started = False
