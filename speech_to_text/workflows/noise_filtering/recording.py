"""Band-pass saved recordings and return bounded plot data."""

from __future__ import annotations

import math
import tempfile
import wave
from pathlib import Path

import numpy as np
from scipy.signal import spectrogram

from speech_to_text.features.noise_filtering import combine_noise_profiles

from .service import analyze_noise_wav, filter_wav


def analyze_noise_recording(recording_service, recording_id):
    """Analyze a saved WAV recording as an explicitly noise-only source."""
    with tempfile.TemporaryDirectory(prefix="speech-noise-profile-") as directory:
        input_path = Path(directory) / "noise.wav"
        copied = recording_service.copy_audio_snapshot(recording_id, input_path)
        if copied is None:
            return None
        source, _duration = copied
        profile = analyze_noise_wav(input_path)
        return {
            "recording_id": recording_id,
            "filename": source.get("filename") or "noise.wav",
            "sample_rate": profile.sample_rate,
            "duration_seconds": profile.duration_seconds,
            "fft_size": profile.fft_size,
            "overlap": profile.overlap,
            "window": profile.window,
            "frequency_bins": len(profile.noise_power),
        }


def analyze_noise_recordings(recording_service, recording_ids):
    """Analyze up to eight saved noise WAVs, snapshotting each source in turn."""
    ids = _noise_ids(recording_ids)
    sources = []
    profiles = []
    with tempfile.TemporaryDirectory(prefix="speech-noise-profiles-") as directory:
        for index, recording_id in enumerate(ids):
            input_path = Path(directory) / f"noise-{index}.wav"
            copied = recording_service.copy_audio_snapshot(recording_id, input_path)
            if copied is None:
                return None
            source, _duration = copied
            sources.append({"recording_id": recording_id, "filename": source.get("filename") or "noise.wav"})
            profiles.append(analyze_noise_wav(input_path))
        profile = combine_noise_profiles(profiles)
    return {
        "recording_ids": list(ids),
        "sources": sources,
        "source_count": len(sources),
        "total_duration_seconds": profile.duration_seconds,
        "combination": "maximum",
        "sample_rate": profile.sample_rate,
        "duration_seconds": profile.duration_seconds,
        "fft_size": profile.fft_size,
        "overlap": profile.overlap,
        "window": profile.window,
        "frequency_bins": len(profile.noise_power),
    }


def _noise_ids(value):
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 8:
        raise ValueError("Provide between 1 and 8 noise recording IDs")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError("Noise recording IDs must be non-empty strings")
    ids = tuple(item.strip() for item in value)
    if len(set(ids)) != len(ids):
        raise ValueError("Noise recording IDs must be unique")
    return ids


def _spectrogram(path, *, max_times=640, max_frequencies=192):
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    if channels > 1:
        samples = samples.reshape(-1, channels)
    samples = samples.astype(np.float32) / 32768.0
    segment_length = min(1024, len(samples))
    frequencies, times, power = spectrogram(
        samples,
        fs=sample_rate,
        window="hann",
        nperseg=segment_length,
        noverlap=segment_length * 3 // 4,
        detrend=False,
        scaling="spectrum",
        mode="psd",
        axis=0,
    )
    if power.ndim == 3:
        power = power.mean(axis=1)
    frequency_edges = np.linspace(0, len(frequencies), min(max_frequencies, len(frequencies)) + 1, dtype=int)
    time_edges = np.linspace(0, len(times), min(max_times, len(times)) + 1, dtype=int)
    reduced = np.empty((len(frequency_edges) - 1, len(time_edges) - 1), dtype=np.float32)
    for row, (frequency_start, frequency_end) in enumerate(zip(frequency_edges[:-1], frequency_edges[1:])):
        for column, (time_start, time_end) in enumerate(zip(time_edges[:-1], time_edges[1:])):
            reduced[row, column] = power[frequency_start:frequency_end, time_start:time_end].mean()
    frequency_axis = [
        float(frequencies[start:end].mean())
        for start, end in zip(frequency_edges[:-1], frequency_edges[1:])
    ]
    time_axis = [
        float(times[start:end].mean())
        for start, end in zip(time_edges[:-1], time_edges[1:])
    ]
    decibels = np.maximum(10 * np.log10(np.maximum(reduced, 1e-10)), -100)
    return {"times_seconds": time_axis, "frequencies_hz": frequency_axis, "dbfs": decibels.tolist()}


def filter_recording(
    recording_service,
    recording_id,
    *,
    low_hz,
    high_hz,
    order=4,
    denoise_enabled=False,
    noise_recording_id=None,
    noise_recording_ids=None,
):
    """Create and save a filtered copy plus comparable, bounded spectrograms.

    ``recording_service`` supplies the existing recording-library snapshot and
    import boundaries. The source recording is never changed.
    """
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in (low_hz, high_hz)
    ):
        raise ValueError("Filter cutoffs must be finite numbers")
    if not isinstance(denoise_enabled, bool):
        raise ValueError("denoise_enabled must be a boolean")
    if noise_recording_id is not None and noise_recording_ids is not None:
        raise ValueError("Specify noise_recording_id or noise_recording_ids, not both")
    selected_noise_ids = (noise_recording_id,) if noise_recording_id is not None else noise_recording_ids
    if denoise_enabled and selected_noise_ids is None:
        raise ValueError("A noise recording is required when denoising is enabled")
    if not denoise_enabled and selected_noise_ids is not None:
        raise ValueError("Noise recording IDs require denoising to be enabled")
    if denoise_enabled:
        selected_noise_ids = _noise_ids(selected_noise_ids)
    with tempfile.TemporaryDirectory(prefix="speech-filter-") as directory:
        input_path = Path(directory) / "source.wav"
        copied = recording_service.copy_audio_snapshot(recording_id, input_path)
        if copied is None:
            return None
        source, _duration = copied
        with wave.open(str(input_path), "rb") as wav:
            sample_rate = wav.getframerate()
            source["sample_rate"] = sample_rate
            source["channels"] = wav.getnchannels()
        if high_hz >= sample_rate / 2:
            raise ValueError(f"High cutoff must be below this recording's Nyquist frequency ({sample_rate / 2:g} Hz)")
        noise_sources = []
        noise_paths = []
        if denoise_enabled:
            for index, noise_recording_id in enumerate(selected_noise_ids):
                noise_path = Path(directory) / f"noise-{index}.wav"
                noise_copy = recording_service.copy_audio_snapshot(noise_recording_id, noise_path)
                if noise_copy is None:
                    return None
                noise_source, _noise_duration = noise_copy
                noise_sources.append({"recording_id": noise_recording_id, "filename": noise_source.get("filename") or "noise.wav"})
                noise_paths.append(noise_path)
        output_path = Path(directory) / "filtered.wav"
        filtered = filter_wav(
            input_path,
            output_path,
            config={"low_hz": low_hz, "high_hz": high_hz, "order": order},
            denoise_enabled=denoise_enabled,
            noise_wav_paths=noise_paths if denoise_enabled else None,
        )
        before = _spectrogram(input_path)
        after = _spectrogram(output_path)
        base_name = Path(source.get("filename") or "recording.wav").stem or "recording"
        output_suffix = f"-denoised-bandpass-{filtered.low_hz:g}-{filtered.high_hz:g}" if denoise_enabled else f"-bandpass-{filtered.low_hz:g}-{filtered.high_hz:g}"
        output_name = f"{base_name}{output_suffix}.wav"
        with output_path.open("rb") as output_file:
            recording = recording_service.import_audio(output_file, output_name, max_bytes=24 * 1024 * 1024)
        return {
            "recording": recording,
            "source": source,
            "source_recording_id": recording_id,
            "settings": {
                "low_hz": filtered.low_hz,
                "high_hz": filtered.high_hz,
                "order": filtered.order,
                "sample_rate": filtered.sample_rate,
                "channels": filtered.channels,
                "duration_seconds": filtered.duration_seconds,
                "nyquist_hz": filtered.sample_rate / 2,
                "db_min": -100,
                "db_max": 0,
                "denoise_enabled": filtered.denoise_enabled,
                "noise_recording_id": selected_noise_ids[0] if denoise_enabled and len(selected_noise_ids) == 1 else None,
                "noise_filename": noise_sources[0]["filename"] if denoise_enabled and len(noise_sources) == 1 else None,
                "noise_recording_ids": list(selected_noise_ids) if denoise_enabled else [],
                "noise_sources": noise_sources,
                "noise_source_count": len(noise_sources),
                "noise_total_duration_seconds": filtered.noise_profile.duration_seconds if filtered.noise_profile else None,
                "noise_combination": "maximum" if filtered.noise_profile else None,
                "denoising_fft_size": filtered.noise_profile.fft_size if filtered.noise_profile else None,
                "denoising_overlap": filtered.noise_profile.overlap if filtered.noise_profile else None,
                "denoising_window": filtered.noise_profile.window if filtered.noise_profile else None,
                "denoising_subtraction_strength": 1.0 if filtered.denoise_enabled else None,
                "denoising_gain_floor": 0.1 if filtered.denoise_enabled else None,
                "denoising_smoothing_size": 3 if filtered.denoise_enabled else None,
                "denoising_max_attenuation_db": 20 if filtered.denoise_enabled else None,
            },
            "before": before,
            "after": after,
        }
