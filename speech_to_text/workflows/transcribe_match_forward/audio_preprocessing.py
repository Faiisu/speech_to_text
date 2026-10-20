"""Prepare immutable noise filtering for 16 kHz speech inference chunks."""

from __future__ import annotations

import json
import math
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

from speech_to_text.features.model_deployment import validate_wav
from speech_to_text.features.noise_filtering import (
    NoiseProfile, analyze_noise, combine_noise_profiles, denoise_audio, filter_audio,
)

_DEFAULTS = {
    "bandpass_enabled": False,
    "low_hz": 100.0,
    "high_hz": 7000.0,
    "order": 4,
    "denoise_enabled": False,
    "noise_recording_ids": [],
}


def normalize_noise_filter(value=None):
    """Validate saved settings; cutoffs refer to STT's 16 kHz mono input."""
    if value is None:
        value = {}
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("noise_filter must be a mapping with string keys")
    unknown = value.keys() - _DEFAULTS.keys()
    if unknown:
        raise ValueError(f"Unknown noise filter settings: {', '.join(sorted(unknown))}")
    options = {**_DEFAULTS, **value}
    for key in ("bandpass_enabled", "denoise_enabled"):
        if not isinstance(options[key], bool):
            raise ValueError(f"{key} must be a boolean")
    for key in ("low_hz", "high_hz"):
        number = options[key]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            raise ValueError("Bandpass cutoffs must be finite numbers")
        options[key] = float(number)
    if not 0 < options["low_hz"] < options["high_hz"] < 8000:
        raise ValueError("Bandpass cutoffs must satisfy 0 < low_hz < high_hz < 8000 Hz")
    order = options["order"]
    if isinstance(order, bool) or not isinstance(order, int) or not 1 <= order <= 8:
        raise ValueError("Bandpass order must be an integer from 1 through 8")
    ids = options["noise_recording_ids"]
    if not isinstance(ids, list) or len(ids) > 8:
        raise ValueError("Select at most 8 noise recordings")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 128 for item in ids):
        raise ValueError("Noise recording IDs must be non-empty strings of at most 128 characters")
    ids = [item.strip() for item in ids]
    if len(set(ids)) != len(ids):
        raise ValueError("Noise recording IDs must be unique")
    if options["denoise_enabled"] and not ids:
        raise ValueError("Spectral denoising requires at least one noise-only recording")
    options["noise_recording_ids"] = ids
    return options


@dataclass(frozen=True)
class NoisePreprocessor:
    """Picklable run snapshot, independent of saved files after preparation."""

    _settings_json: str
    profile: NoiseProfile | None
    _sources: tuple[tuple[str, str], ...] = ()

    @property
    def settings(self):
        return json.loads(self._settings_json)

    @property
    def noise_sources(self):
        return [{"recording_id": key, "filename": name} for key, name in self._sources]

    def __call__(self, samples, sample_rate):
        if sample_rate != 16000:
            raise ValueError("Speech noise filtering requires 16 kHz input")
        audio = np.asarray(samples, dtype=np.float32)
        if audio.ndim != 1 or not np.all(np.isfinite(audio)):
            raise ValueError("Speech noise filtering requires finite mono audio")
        if not len(audio):
            return audio.copy()
        options = self.settings
        result = denoise_audio(audio, sample_rate, self.profile) if self.profile is not None else audio.copy()
        if options["bandpass_enabled"]:
            # Stop can flush fewer frames than filtfilt's padding requirement.
            padded = np.pad(result, (0, max(0, 64 - len(result))), mode="edge")
            result = filter_audio(padded, sample_rate, {key: options[key] for key in ("low_hz", "high_hz", "order")})[:len(audio)]
        return np.asarray(result, dtype=np.float32)


def prepare_audio_preprocessor(config=None, recording_service=None):
    """Snapshot noise-only WAVs once, resample to 16 kHz, then combine profiles.

    Bandpass defaults to 100–7000 Hz/order 4; both filtering stages default off.
    Raises ValueError for invalid settings, missing recordings, or short noise.
    Recording service snapshot errors propagate to the caller's input boundary.
    """
    options = normalize_noise_filter(config)
    if not options["bandpass_enabled"] and not options["denoise_enabled"]:
        return None
    profiles, sources = [], []
    if options["denoise_enabled"]:
        if recording_service is None:
            raise ValueError("A recording service is required to prepare noise samples")
        with tempfile.TemporaryDirectory(prefix="speech-stt-noise-") as directory:
            for index, key in enumerate(options["noise_recording_ids"]):
                path = Path(directory) / f"noise-{index}.wav"
                copied = recording_service.copy_audio_snapshot(key, path)
                if copied is None:
                    raise ValueError(f"Noise recording not found: {key}")
                metadata, _duration = copied
                validate_wav(path, max_seconds=120)
                with wave.open(str(path), "rb") as wav:
                    rate, channels = wav.getframerate(), wav.getnchannels()
                    audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768
                if channels > 1:
                    audio = audio.reshape(-1, channels).mean(axis=1)
                if rate != 16000:
                    divisor = math.gcd(rate, 16000)
                    audio = resample_poly(audio, 16000 // divisor, rate // divisor).astype(np.float32)
                profiles.append(analyze_noise(audio, 16000))
                sources.append((key, metadata.get("filename") or "noise.wav"))
    profile = combine_noise_profiles(profiles) if profiles else None
    return NoisePreprocessor(json.dumps(options), profile, tuple(sources))
