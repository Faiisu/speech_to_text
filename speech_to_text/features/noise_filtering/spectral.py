"""Stationary-noise spectral estimation and conservative spectral gating."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import istft, stft

FFT_SIZE = 1024
OVERLAP = 768
GAIN_FLOOR = 0.1


@dataclass(frozen=True)
class NoiseProfile:
    """Mean stationary noise power at each fixed STFT frequency bin."""

    sample_rate: int
    duration_seconds: float
    fft_size: int
    overlap: int
    window: str
    noise_power: tuple[float, ...]


def _audio(samples, sample_rate):
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, (int, np.integer)) or sample_rate <= 0:
        raise ValueError("sample_rate must be a positive integer")
    try:
        audio = np.asarray(samples, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Audio samples must be numeric: {exc}") from exc
    if audio.ndim == 1:
        audio = audio[:, None]
        mono = True
    elif audio.ndim == 2 and audio.shape[1] in (1, 2):
        mono = False
    else:
        raise ValueError("Audio must be mono or frame-by-channel mono/stereo")
    if not len(audio) or not np.all(np.isfinite(audio)):
        raise ValueError("Audio must contain finite samples")
    return audio, mono, int(sample_rate)


def _spectrum(audio):
    padded = np.pad(audio, ((0, max(0, FFT_SIZE - len(audio))), (0, 0)))
    _, _, transformed = stft(
        padded,
        window="hann",
        nperseg=FFT_SIZE,
        noverlap=OVERLAP,
        boundary="zeros",
        padded=True,
        axis=0,
    )
    if not np.all(np.isfinite(transformed)):
        raise ValueError("Unable to analyze non-finite audio spectrum")
    return transformed


def analyze_noise(samples, sample_rate):
    """Estimate stationary noise power from a noise-only mono or stereo clip."""
    audio, _mono, rate = _audio(samples, sample_rate)
    if len(audio) < FFT_SIZE:
        raise ValueError(f"Noise audio must contain at least {FFT_SIZE} frames")
    transformed = _spectrum(audio)
    power = np.mean(np.square(np.abs(transformed), dtype=np.float32), axis=(1, 2))
    if not np.all(np.isfinite(power)):
        raise ValueError("Unable to analyze non-finite noise power")
    return NoiseProfile(rate, len(audio) / rate, FFT_SIZE, OVERLAP, "hann", tuple(float(value) for value in power))


def combine_noise_profiles(profiles):
    """Combine compatible profiles with a per-frequency maximum envelope."""
    profiles = tuple(profiles)
    if not 1 <= len(profiles) <= 8 or any(not isinstance(profile, NoiseProfile) for profile in profiles):
        raise ValueError("Provide between 1 and 8 noise profiles")
    first = profiles[0]
    if any(
        profile.sample_rate != first.sample_rate
        or (profile.fft_size, profile.overlap, profile.window) != (FFT_SIZE, OVERLAP, "hann")
        or len(profile.noise_power) != FFT_SIZE // 2 + 1
        for profile in profiles
    ):
        raise ValueError("Noise profiles must use the same sample rate and STFT settings")
    powers = np.asarray([profile.noise_power for profile in profiles], dtype=np.float64)
    if not np.all(np.isfinite(powers)) or np.any(powers < 0):
        raise ValueError("Noise profile power must be finite and non-negative")
    if len(profiles) == 1:
        return first
    return NoiseProfile(
        first.sample_rate,
        sum(profile.duration_seconds for profile in profiles),
        FFT_SIZE,
        OVERLAP,
        "hann",
        tuple(float(value) for value in np.max(powers, axis=0)),
    )


def denoise_audio(samples, sample_rate, profile, *, config=None):
    """Apply bounded, smoothed power subtraction and return frame-aligned audio.

    ``config`` accepts ``subtraction_strength`` (default 1.0), ``gain_floor``
    (default 0.1), and ``smoothing_size`` (default 3 bins and frames).
    """
    audio, mono, rate = _audio(samples, sample_rate)
    if (
        not isinstance(profile, NoiseProfile)
        or profile.fft_size != FFT_SIZE
        or profile.overlap != OVERLAP
        or profile.window != "hann"
        or len(profile.noise_power) != FFT_SIZE // 2 + 1
    ):
        raise ValueError("profile must be a NoiseProfile produced by analyze_noise")
    if profile.sample_rate != rate:
        raise ValueError("Noise profile sample rate must match the target audio sample rate")
    if config is None:
        options = {}
    elif isinstance(config, dict) and all(isinstance(key, str) for key in config):
        options = dict(config)
    else:
        raise ValueError("config must be a mapping with string keys")
    unknown = options.keys() - {"subtraction_strength", "gain_floor", "smoothing_size"}
    if unknown:
        raise ValueError(f"Unknown denoising settings: {', '.join(sorted(unknown))}")
    strength = options.get("subtraction_strength", 1.0)
    gain_floor = options.get("gain_floor", GAIN_FLOOR)
    smoothing_size = options.get("smoothing_size", 3)
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in (strength, gain_floor)
    ) or strength < 0 or strength > 10 or gain_floor <= 0 or gain_floor > 1:
        raise ValueError("Denoising strength must be from 0 through 10; gain_floor must be in (0, 1]")
    if isinstance(smoothing_size, bool) or not isinstance(smoothing_size, int) or smoothing_size < 1:
        raise ValueError("smoothing_size must be a positive integer")
    noise_power = np.asarray(profile.noise_power, dtype=np.float64)[:, None, None]
    if not np.all(np.isfinite(noise_power)) or np.any(noise_power < 0):
        raise ValueError("Noise profile power must be finite and non-negative")
    if not strength or not np.any(noise_power):
        return np.asarray(samples, dtype=np.float32).copy()
    transformed = _spectrum(audio)
    target_power = np.square(np.abs(transformed), dtype=np.float32)
    ratio = np.divide(strength * noise_power, target_power, out=np.ones_like(target_power), where=target_power > 0)
    gain = np.sqrt(np.maximum(1.0 - ratio, gain_floor ** 2))
    gain = uniform_filter1d(gain, size=smoothing_size, axis=0, mode="nearest")
    gain = uniform_filter1d(gain, size=smoothing_size, axis=2, mode="nearest")
    np.clip(gain, gain_floor, 1.0, out=gain)
    _, denoised = istft(
        transformed * gain,
        window="hann",
        nperseg=FFT_SIZE,
        noverlap=OVERLAP,
        input_onesided=True,
        boundary=True,
        time_axis=2,
        freq_axis=0,
    )
    output = np.asarray(denoised.T[:len(audio)], dtype=np.float32)
    if not np.all(np.isfinite(output)):
        raise ValueError("Denoising produced non-finite audio")
    return output[:, 0] if mono else output


__all__ = ["NoiseProfile", "analyze_noise", "combine_noise_profiles", "denoise_audio"]
