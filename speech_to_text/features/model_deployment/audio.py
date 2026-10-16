"""WAV decoding and bounded float32 mono resampling."""

from __future__ import annotations

import wave
import math
from pathlib import Path

import numpy as np

from .errors import AudioCodecUnavailableError, AudioInputError

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
            raise AudioInputError(
                "Captured channel layout does not match the source configuration"
            )
        audio = audio.mean(axis=1, dtype=np.float32)
    elif audio.ndim != 1:
        raise AudioInputError("Audio must be mono or frame-by-channel PCM")
    if (
        not isinstance(sample_rate, (int, np.integer))
        or not 8000 <= sample_rate <= 48000
    ):
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
            channels, width, rate = (
                wav.getnchannels(),
                wav.getsampwidth(),
                wav.getframerate(),
            )
            frames = wav.readframes(wav.getnframes())
        if width != 2 or channels not in (1, 2):
            raise AudioInputError("WAV input must be PCM signed 16-bit mono or stereo")
        if not 8000 <= rate <= 48000:
            raise AudioInputError("WAV sample rate must be between 8 kHz and 48 kHz")
        audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / np.float32(
            32768.0
        )
        if channels == 2:
            audio = audio.reshape(-1, 2).mean(axis=1, dtype=np.float32)
        return normalize_audio(audio, rate)
    except AudioInputError:
        raise
    except (OSError, EOFError, wave.Error, ValueError) as exc:
        raise AudioInputError(f"Unable to decode PCM16 WAV input: {exc}") from exc


def validate_wav(clip, *, max_seconds=120):
    """Validate a retained PCM16 WAV and return its duration in seconds.

    This header and frame check keeps upload validation bounded while checking
    that the data chunk contains every declared frame.
    """
    if isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float)) or not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError("max_seconds must be finite and positive")
    try:
        if not isinstance(clip, (str, Path)):
            raise AudioInputError("clip must be a WAV path")
        with wave.open(str(clip), "rb") as wav:
            channels, width, rate, frame_count = (
                wav.getnchannels(),
                wav.getsampwidth(),
                wav.getframerate(),
                wav.getnframes(),
            )
            if width != 2 or channels not in (1, 2):
                raise AudioInputError("WAV input must be PCM signed 16-bit mono or stereo")
            if not 8000 <= rate <= 48000:
                raise AudioInputError("WAV sample rate must be between 8 kHz and 48 kHz")
            if frame_count <= 0:
                raise AudioInputError("WAV input must contain audio frames")
            duration = frame_count / rate
            if duration > max_seconds:
                raise AudioInputError(f"WAV duration must not exceed {max_seconds} seconds")
            remaining = frame_count
            while remaining:
                count = min(remaining, 16384)
                if len(wav.readframes(count)) != count * channels * width:
                    raise AudioInputError("WAV input is truncated")
                remaining -= count
            return duration
    except AudioInputError:
        raise
    except (EOFError, wave.Error, ValueError) as exc:
        raise AudioInputError(f"Unable to decode PCM16 WAV input: {exc}") from exc


def convert_audio_to_wav(input_path, output_path, *, max_seconds=120):
    """Decode an audio-only MP3/M4A file into bounded mono PCM16 WAV."""
    try:
        import av
    except ImportError as exc:
        raise AudioCodecUnavailableError("Compressed audio decoding requires PyAV") from exc

    source = Path(input_path)
    destination = Path(output_path)
    if isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float)) or not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError("max_seconds must be finite and positive")
    demuxer = {".mp3": "mp3", ".m4a": "mov"}.get(source.suffix.lower())
    if demuxer is None:
        raise AudioInputError("Compressed audio input must be an MP3 or M4A file")
    try:
        # Decode only local media, never playlists or external references.
        with av.open(str(source), mode="r", format=demuxer, options={"protocol_whitelist": "file"}) as container:
            if len(container.streams) != 1 or container.streams.video:
                raise AudioInputError(
                    "Input must contain one audio stream and no video or data streams"
                )
            streams = list(container.streams.audio)
            if len(streams) != 1:
                raise AudioInputError("Input must contain exactly one audio stream")
            stream = streams[0]
            format_names = set(container.format.name.split(","))
            codec_name = stream.codec_context.name
            if source.suffix.lower() == ".mp3":
                if "mp3" not in format_names or codec_name not in {"mp3", "mp3float"}:
                    raise AudioInputError("File is not a supported MP3 audio stream")
            elif source.suffix.lower() == ".m4a":
                if not (format_names & {"mov", "mp4", "m4a"}) or codec_name not in {"aac", "alac"}:
                    raise AudioInputError("File is not a supported M4A audio stream")
            else:
                raise AudioInputError("Audio input must be an MP3, M4A, or WAV file")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            frame_limit = int(max_seconds * 16000)
            frame_count = 0
            with wave.open(str(destination), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                for frame in container.decode(stream):
                    for converted in resampler.resample(frame):
                        samples = converted.to_ndarray()
                        count = int(samples.size)
                        if count == 0:
                            continue
                        if frame_count + count > frame_limit:
                            raise AudioInputError(
                                f"Audio duration must not exceed {max_seconds} seconds"
                            )
                        wav.writeframesraw(samples.astype("<i2", copy=False).tobytes())
                        frame_count += count
                for converted in resampler.resample(None):
                    samples = converted.to_ndarray()
                    count = int(samples.size)
                    if count == 0:
                        continue
                    if frame_count + count > frame_limit:
                        raise AudioInputError(
                            f"Audio duration must not exceed {max_seconds} seconds"
                        )
                    wav.writeframesraw(samples.astype("<i2", copy=False).tobytes())
                    frame_count += count
                if frame_count == 0:
                    raise AudioInputError("Input audio stream contains no audio frames")
        return frame_count / 16000
    except AudioInputError:
        destination.unlink(missing_ok=True)
        raise
    except (av.error.FFmpegError, ValueError, EOFError) as exc:
        destination.unlink(missing_ok=True)
        raise AudioInputError(f"Unable to decode audio input: {exc}") from exc
    except OSError:
        destination.unlink(missing_ok=True)
        raise


class StreamingResampler:
    """Linear streaming resampler retaining only the interpolation boundary."""

    def __init__(self, sample_rate):
        if (
            not isinstance(sample_rate, (int, np.integer))
            or not 8000 <= sample_rate <= 48000
        ):
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
            result = (
                self._buffer[left] * (1 - fraction) + self._buffer[right] * fraction
            )
            result = np.asarray(result, dtype=np.float32)
        else:
            result = np.empty(0, dtype=np.float32)
        self._buffer = np.empty(0, dtype=np.float32)
        return result
