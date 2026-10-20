import numpy as np
import pytest

from speech_to_text.features.noise_filtering import filter_audio


def rms(samples):
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def test_bandpass_preserves_passband_and_attenuates_outside():
    rate = 16000
    time = np.arange(rate, dtype=np.float64) / rate
    filtered = filter_audio(
        np.sin(2 * np.pi * 1000 * time)
        + np.sin(2 * np.pi * 50 * time)
        + np.sin(2 * np.pi * 7500 * time),
        rate,
    )
    center = filtered[1000:-1000]
    expected = np.sin(2 * np.pi * 1000 * time)[1000:-1000]
    assert np.corrcoef(center, expected)[0, 1] > 0.99
    assert rms(center - expected) < 0.04


def test_filter_audio_rejects_invalid_configuration():
    with pytest.raises(ValueError, match="Unknown"):
        filter_audio(np.ones(1600), 16000, {"cutoff": 500})
    with pytest.raises(ValueError, match="Nyquist"):
        filter_audio(np.ones(1600), 16000, {"high_hz": 8000})
    with pytest.raises(ValueError, match="mapping"):
        filter_audio(np.ones(1600), 16000, "bad-config")
