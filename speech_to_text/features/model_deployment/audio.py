"""WAV decoding and bounded float32 mono resampling."""

from __future__ import annotations

from pathlib import Path
import wave

import numpy as np

from .errors import AudioInputError

TARGET_RATE = 16000


def normalize_audio(samples, sample_rate, channels=None):
    try:
        audio = np.asarray(samples, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise AudioInputError(f"Audio samples must be numeric: {exc}") from exc
    if channels is None and audio.ndim == 2:
        audio = audio.mean(axis=1, dtype=np.float32)
    elif channels and channels > 1:
        if audio.ndim != 2 or audio.shape[1] != channels:
            raise AudioInputError("Captured channel layout does not match the source configuration")
        audio = audio.mean(axis=1, dtype=np.float32)
    elif audio.ndim != 1:
        raise AudioInputError("Audio must be mono or frame-by-channel PCM")
    if not isinstance(sample_rate, (int, np.integer)) or not 8000 <= sample_rate <= 48000:
        raise AudioInputError("Audio sample rate must be between 8 kHz and 48 kHz")
    if not np.all(np.isfinite(audio)):
        raise AudioInputError("Audio contains NaN or infinite samples")
    if sample_rate == TARGET_RATE:
        return np.ascontiguousarray(audio, dtype=np.float32)
    if audio.size == 0:
        return np.empty(0, dtype=np.float32)
    output_size = int(round(audio.size * TARGET_RATE / sample_rate))
    old_positions = np.arange(audio.size, dtype=np.float64)
    new_positions = np.arange(output_size, dtype=np.float64) * sample_rate / TARGET_RATE
    return np.interp(new_positions, old_positions, audio).astype(np.float32)


def read_clip(clip):
    try:
        if isinstance(clip, (bytes, bytearray, memoryview)):
            import io
            source = io.BytesIO(bytes(clip))
        elif isinstance(clip, (str, Path)):
            source = str(clip)
        else:
            raise AudioInputError("clip must be a WAV path or WAV bytes")
        with wave.open(source, "rb") as wav:
            channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
            frames = wav.readframes(wav.getnframes())
        if width != 2 or channels not in (1, 2):
            raise AudioInputError("WAV input must be PCM signed 16-bit mono or stereo")
        if not 8000 <= rate <= 48000:
            raise AudioInputError("WAV sample rate must be between 8 kHz and 48 kHz")
        audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / np.float32(32768.0)
        if channels == 2:
            audio = audio.reshape(-1, 2).mean(axis=1, dtype=np.float32)
        return normalize_audio(audio, rate)
    except AudioInputError:
        raise
    except (OSError, EOFError, wave.Error, ValueError) as exc:
        raise AudioInputError(f"Unable to decode PCM16 WAV input: {exc}") from exc


class StreamingResampler:
    """Linear streaming resampler retaining only the interpolation boundary."""

    def __init__(self, sample_rate):
        if not isinstance(sample_rate, (int, np.integer)) or not 8000 <= sample_rate <= 48000:
            raise AudioInputError("Audio sample rate must be between 8 kHz and 48 kHz")
        self.sample_rate = int(sample_rate)
        self.step = self.sample_rate / TARGET_RATE
        self._buffer = np.empty(0, dtype=np.float32)
        self._position = 0.0

    def feed(self, frames):
        samples = np.asarray(frames, dtype=np.float32)
        if samples.ndim == 2:
            samples = samples.mean(axis=1, dtype=np.float32)
        if samples.ndim != 1:
            raise AudioInputError("Captured audio must be mono or frame-by-channel")
        if not np.all(np.isfinite(samples)):
            raise AudioInputError("Captured audio contains NaN or infinite samples")
        if self.sample_rate == TARGET_RATE:
            return np.ascontiguousarray(samples)
        self._buffer = np.concatenate((self._buffer, samples))
        if self._buffer.size < 2:
            return np.empty(0, dtype=np.float32)
        positions = []
        while self._position + 1 < self._buffer.size:
            positions.append(self._position)
            self._position += self.step
        if not positions:
            return np.empty(0, dtype=np.float32)
        indexes = np.asarray(positions, dtype=np.float64)
        left = indexes.astype(np.int64)
        fraction = (indexes - left).astype(np.float32)
        out = self._buffer[left] * (1 - fraction) + self._buffer[left + 1] * fraction
        # Keep the right interpolation sample even when the next output sample
        # lies several source frames beyond this callback (for downsampling).
        consumed = min(max(0, int(self._position)), self._buffer.size - 1)
        if consumed:
            self._buffer = self._buffer[consumed:]
            self._position -= consumed
        return np.asarray(out, dtype=np.float32)

    def flush(self):
        if self.sample_rate == TARGET_RATE:
            result = self._buffer.copy()
        elif self._buffer.size and self._position < self._buffer.size:
            positions = []
            while self._position < self._buffer.size:
                positions.append(self._position)
                self._position += self.step
            indexes = np.asarray(positions, dtype=np.float64)
            left = np.minimum(indexes.astype(np.int64), self._buffer.size - 1)
            right = np.minimum(left + 1, self._buffer.size - 1)
            fraction = (indexes - left).astype(np.float32)
            result = self._buffer[left] * (1 - fraction) + self._buffer[right] * fraction
            result = np.asarray(result, dtype=np.float32)
        else:
            result = np.empty(0, dtype=np.float32)
        self._buffer = np.empty(0, dtype=np.float32)
        return result
