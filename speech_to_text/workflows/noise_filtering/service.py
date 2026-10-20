"""Filter a bounded WAV into a new WAV file."""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from speech_to_text.features.model_deployment import validate_wav
from speech_to_text.features.noise_filtering import filter_audio


@dataclass(frozen=True)
class FilteredAudio:
    output_path: Path
    sample_rate: int
    channels: int
    frames: int
    duration_seconds: float
    low_hz: float
    high_hz: float
    order: int


def filter_wav(input_path, output_path, *, config=None):
    """Write a filtered copy of a complete PCM16 WAV without modifying input."""
    source = Path(input_path)
    destination = Path(output_path)
    if source.resolve() == destination.resolve():
        raise ValueError("input_path and output_path must refer to different files")
    duration = validate_wav(source, max_seconds=120)
    with wave.open(str(source), "rb") as wav:
        channels, sample_rate, frame_count = (
            wav.getnchannels(), wav.getframerate(), wav.getnframes()
        )
        raw = wav.readframes(frame_count)
    samples = np.frombuffer(raw, dtype="<i2")
    if channels == 2:
        samples = samples.reshape(-1, channels)
    filtered = filter_audio(samples.astype(np.float32) / np.float32(32768), sample_rate, config)
    pcm = np.clip(np.rint(filtered * 32768), -32768, 32767).astype("<i2")
    config = {} if config is None else dict(config)
    low_hz = config.get("low_hz", 100.0)
    high_hz = config.get("high_hz", 7000.0)
    order = config.get("order", 4)
    created = False
    try:
        with destination.open("xb") as output_file:
            created = True
            with wave.open(output_file, "wb") as output_wav:
                output_wav.setnchannels(channels)
                output_wav.setsampwidth(2)
                output_wav.setframerate(sample_rate)
                output_wav.writeframes(pcm.tobytes())
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise
    return FilteredAudio(
        output_path=destination,
        sample_rate=sample_rate,
        channels=channels,
        frames=frame_count,
        duration_seconds=duration,
        low_hz=float(low_hz),
        high_hz=float(high_hz),
        order=order,
    )
