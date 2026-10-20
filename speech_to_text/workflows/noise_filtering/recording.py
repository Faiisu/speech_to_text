"""Band-pass saved recordings and return bounded plot data."""

from __future__ import annotations

import math
import tempfile
import wave
from pathlib import Path

import numpy as np
from scipy.signal import spectrogram

from .service import filter_wav


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


def filter_recording(recording_service, recording_id, *, low_hz, high_hz, order=4):
    """Create and save a filtered copy plus comparable, bounded spectrograms.

    ``recording_service`` supplies the existing recording-library snapshot and
    import boundaries. The source recording is never changed.
    """
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in (low_hz, high_hz)
    ):
        raise ValueError("Filter cutoffs must be finite numbers")
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
        output_path = Path(directory) / "filtered.wav"
        filtered = filter_wav(
            input_path,
            output_path,
            config={"low_hz": low_hz, "high_hz": high_hz, "order": order},
        )
        before = _spectrogram(input_path)
        after = _spectrogram(output_path)
        base_name = Path(source.get("filename") or "recording.wav").stem or "recording"
        output_name = f"{base_name}-bandpass-{filtered.low_hz:g}-{filtered.high_hz:g}.wav"
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
            },
            "before": before,
            "after": after,
        }
