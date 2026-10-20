"""Filter a bounded WAV into a new WAV file."""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from speech_to_text.features.model_deployment import validate_wav
from speech_to_text.features.noise_filtering import (
    NoiseProfile,
    analyze_noise,
    combine_noise_profiles,
    denoise_audio,
    filter_audio,
)


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
    denoise_enabled: bool
    noise_profile: NoiseProfile | None


def _read_pcm16(path):
    source = Path(path)
    duration = validate_wav(source, max_seconds=120)
    with wave.open(str(source), "rb") as wav:
        channels, sample_rate, frame_count = wav.getnchannels(), wav.getframerate(), wav.getnframes()
        raw = wav.readframes(frame_count)
    samples = np.frombuffer(raw, dtype="<i2")
    if channels == 2:
        samples = samples.reshape(-1, channels)
    return samples.astype(np.float32) / np.float32(32768), sample_rate, channels, frame_count, duration


def analyze_noise_wav(path):
    """Analyze a complete PCM16 noise-only WAV and return its stationary profile."""
    samples, sample_rate, _channels, _frames, _duration = _read_pcm16(path)
    return analyze_noise(samples, sample_rate)


def analyze_noise_wavs(paths):
    """Analyze 1–8 WAV files one at a time and combine their stationary profiles."""
    if isinstance(paths, (str, bytes, Path)):
        raise ValueError("paths must be a collection of 1 to 8 noise WAV paths")
    paths = tuple(Path(path) for path in paths)
    if not 1 <= len(paths) <= 8:
        raise ValueError("Provide between 1 and 8 noise WAV files")
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("Noise WAV paths must be unique")
    return combine_noise_profiles(analyze_noise_wav(path) for path in paths)


def filter_wav(input_path, output_path, *, config=None, denoise_enabled=False, noise_wav_path=None, noise_wav_paths=None):
    """Write a filtered copy of a complete PCM16 WAV without modifying input."""
    source = Path(input_path)
    destination = Path(output_path)
    if source.resolve() == destination.resolve():
        raise ValueError("input_path and output_path must refer to different files")
    samples, sample_rate, channels, frame_count, duration = _read_pcm16(source)
    if not isinstance(denoise_enabled, bool):
        raise ValueError("denoise_enabled must be a boolean")
    if noise_wav_path is not None and noise_wav_paths is not None:
        raise ValueError("Specify noise_wav_path or noise_wav_paths, not both")
    selected_noise_paths = (noise_wav_path,) if noise_wav_path is not None else noise_wav_paths
    if denoise_enabled and selected_noise_paths is None:
        raise ValueError("A noise WAV path is required when denoising is enabled")
    if not denoise_enabled and selected_noise_paths is not None:
        raise ValueError("Noise WAV paths require denoising to be enabled")
    profile = analyze_noise_wavs(selected_noise_paths) if denoise_enabled else None
    if profile is not None and profile.sample_rate != sample_rate:
        raise ValueError("Noise profile sample rate must match the target audio sample rate")
    processed = denoise_audio(samples, sample_rate, profile) if profile is not None else samples
    filtered = filter_audio(processed, sample_rate, config)
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
        denoise_enabled=denoise_enabled,
        noise_profile=profile,
    )
