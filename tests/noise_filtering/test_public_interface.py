import numpy as np
import pytest

from speech_to_text.features.noise_filtering import NoiseProfile, analyze_noise, combine_noise_profiles, denoise_audio, filter_audio


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


def test_stationary_noise_profile_reduces_matching_noise_and_preserves_stereo_layout():
    rate = 16000
    time = np.arange(rate * 2, dtype=np.float32) / rate
    noise = .12 * np.sin(2 * np.pi * 600 * time)
    noise_stereo = np.column_stack((noise, -noise))
    profile = analyze_noise(noise_stereo, rate)
    target = np.column_stack((noise + .3 * np.sin(2 * np.pi * 1100 * time), -noise - .3 * np.sin(2 * np.pi * 1100 * time)))

    denoised = denoise_audio(target, rate, profile)

    assert len(profile.noise_power) == 513
    assert (profile.fft_size, profile.overlap, profile.window) == (1024, 768, "hann")
    assert all(np.isfinite(profile.noise_power))
    assert denoised.shape == target.shape
    assert np.isfinite(denoised).all()
    assert rms(denoised[2000:-2000, 0] + denoised[2000:-2000, 1]) < .001
    assert rms(denoised[2000:-2000, 0] - .3 * np.sin(2 * np.pi * 1100 * time[2000:-2000])) < .03
    assert rms(denoised[2000:-2000, 0]) < rms(target[2000:-2000, 0])


def test_spectral_denoising_handles_silence_short_targets_and_rejects_bad_profiles():
    rate = 16000
    profile = analyze_noise(np.zeros((1024, 2), dtype=np.float32), rate)
    short = np.array([.1, -.2, .3], dtype=np.float32)
    assert np.array_equal(denoise_audio(short, rate, profile), short)

    tone_profile = analyze_noise(np.sin(2 * np.pi * 500 * np.arange(1024) / rate), rate)
    denoised = denoise_audio(short, rate, tone_profile)
    assert denoised.shape == short.shape
    assert np.isfinite(denoised).all()
    with pytest.raises(ValueError, match="sample rate"):
        denoise_audio(short, 8000, tone_profile)
    with pytest.raises(ValueError, match="at least 1024"):
        analyze_noise(np.zeros(100), rate)
    with pytest.raises(ValueError, match="Unknown denoising"):
        denoise_audio(short, rate, tone_profile, config={"gain": .5})


def test_noise_profiles_combine_by_per_bin_max_independent_of_order_and_single_is_unchanged():
    left = NoiseProfile(16000, 1, 1024, 768, "hann", tuple([1.0] * 513))
    right = NoiseProfile(16000, 2, 1024, 768, "hann", tuple([2.0] * 513))
    combined = combine_noise_profiles([left, right])
    assert combined.noise_power == right.noise_power
    assert combined.duration_seconds == 3
    assert combine_noise_profiles([right, left]) == combined
    assert combine_noise_profiles([left]) is left
    with pytest.raises(ValueError, match="same sample rate"):
        combine_noise_profiles([left, NoiseProfile(8000, 1, 1024, 768, "hann", left.noise_power)])
    with pytest.raises(ValueError, match="finite"):
        combine_noise_profiles([NoiseProfile(16000, 1, 1024, 768, "hann", tuple([float("nan")] * 513))])
