import wave
from io import BytesIO

import numpy as np
import pytest

from speech_to_text.features.model_deployment import AudioInputError
from speech_to_text.workflows.audio_recording import RecordingService
from speech_to_text.workflows.noise_filtering import filter_recording, filter_wav


def write_wav(path, samples, sample_rate=16000):
    pcm = np.asarray(samples, dtype="<i2")
    channels = 1 if pcm.ndim == 1 else pcm.shape[1]
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def rms(samples):
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def test_filter_wav_preserves_wav_layout_and_source(tmp_path):
    rate = 16000
    time = np.arange(rate, dtype=np.float64) / rate
    samples = np.column_stack(
        (
            np.rint(10000 * np.sin(2 * np.pi * 1000 * time)),
            np.rint(10000 * np.sin(2 * np.pi * 50 * time)),
        )
    ).astype(np.int16)
    source = tmp_path / "input.wav"
    output = tmp_path / "filtered.wav"
    write_wav(source, samples, rate)
    original_bytes = source.read_bytes()

    result = filter_wav(source, output)

    assert source.read_bytes() == original_bytes
    assert result.output_path == output
    assert result.sample_rate == rate
    assert result.channels == 2
    assert result.frames == rate
    assert result.duration_seconds == 1.0
    with wave.open(str(output), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getnframes()) == (rate, 2, rate)
        filtered = np.frombuffer(wav.readframes(rate), dtype="<i2").reshape(-1, 2)
    assert rms(filtered[1000:-1000, 0]) > 6500
    assert rms(filtered[1000:-1000, 1]) < 1000


def test_filter_wav_rejects_output_overwrite_and_bad_nyquist(tmp_path):
    source = tmp_path / "input.wav"
    output = tmp_path / "filtered.wav"
    write_wav(source, np.zeros(8000, dtype=np.int16), 8000)
    output.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        filter_wav(source, output, config={"high_hz": 3000})
    assert output.read_bytes() == b"keep"
    with pytest.raises(ValueError, match="Nyquist"):
        filter_wav(source, tmp_path / "bad.wav")


def test_filter_wav_rejects_truncated_audio_and_keeps_silence(tmp_path):
    silence = tmp_path / "silence.wav"
    filtered = tmp_path / "silence-filtered.wav"
    write_wav(silence, np.zeros(16000, dtype=np.int16))
    filter_wav(silence, filtered)
    with wave.open(str(filtered), "rb") as wav:
        assert not np.any(np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2"))

    truncated = tmp_path / "truncated.wav"
    truncated.write_bytes(silence.read_bytes()[:-2])
    with pytest.raises(AudioInputError, match="truncated"):
        filter_wav(truncated, tmp_path / "unused.wav")
    assert not (tmp_path / "unused.wav").exists()


@pytest.mark.parametrize("sample_count", [0, 16000 * 120 + 1])
def test_filter_wav_rejects_empty_or_overlong_audio(tmp_path, sample_count):
    source = tmp_path / "input.wav"
    write_wav(source, np.zeros(sample_count, dtype=np.int16))
    with pytest.raises(AudioInputError):
        filter_wav(source, tmp_path / "filtered.wav")
    assert not (tmp_path / "filtered.wav").exists()


def test_filter_wav_rejects_same_input_and_output(tmp_path):
    source = tmp_path / "input.wav"
    write_wav(source, np.zeros(16000, dtype=np.int16))
    original_bytes = source.read_bytes()
    with pytest.raises(ValueError, match="different files"):
        filter_wav(source, source)
    assert source.read_bytes() == original_bytes


def test_filter_wav_removes_partial_output_after_write_failure(tmp_path, monkeypatch):
    source = tmp_path / "input.wav"
    output = tmp_path / "filtered.wav"
    write_wav(source, np.full(16000, 5000, dtype=np.int16))
    original_bytes = source.read_bytes()
    original_writeframesraw = wave.Wave_write.writeframesraw

    def fail_after_partial_write(writer, data):
        original_writeframesraw(writer, data[: len(data) // 2])
        raise OSError("simulated disk failure")

    monkeypatch.setattr(wave.Wave_write, "writeframesraw", fail_after_partial_write)
    with pytest.raises(OSError, match="simulated disk failure"):
        filter_wav(source, output)
    assert source.read_bytes() == original_bytes
    assert not output.exists()


def test_filter_recording_keeps_source_and_returns_comparable_stereo_spectrograms(tmp_path):
    rate = 16000
    time = np.arange(rate * 2) / rate
    tone = .3 * np.sin(2 * np.pi * 1000 * time) + .3 * np.sin(2 * np.pi * 50 * time)
    stereo = np.column_stack((tone, -tone))
    source_file = BytesIO()
    with wave.open(source_file, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(np.rint(stereo * 32767).astype("<i2").tobytes())
    source_bytes = source_file.getvalue()
    service = RecordingService(output_directory=tmp_path)
    source = service.import_audio(BytesIO(source_bytes), "phase-cancel.wav")
    original_path = service.audio_path(source["recording_id"])
    original = original_path.read_bytes()

    result = filter_recording(service, source["recording_id"], low_hz=300, high_hz=3000)

    assert original_path.read_bytes() == original == source_bytes
    assert result["source"]["sample_rate"] == rate
    assert result["recording"]["recording_id"] != source["recording_id"]
    assert result["recording"]["filename"].startswith("phase-cancel-bandpass-300-3000")
    assert result["settings"]["channels"] == 2
    assert result["settings"]["duration_seconds"] == 2
    before, after = result["before"], result["after"]
    assert before["times_seconds"] == after["times_seconds"]
    assert before["frequencies_hz"] == after["frequencies_hz"]
    assert len(before["times_seconds"]) >= 5
    assert len(before["times_seconds"]) <= 640
    assert len(before["frequencies_hz"]) <= 192
    assert max(max(row) for row in before["dbfs"]) > -25
    assert max(max(row) for row in after["dbfs"]) > -25
    low_bin = min(range(len(before["frequencies_hz"])), key=lambda i: abs(before["frequencies_hz"][i] - 50))
    low_bin_after = min(range(len(after["frequencies_hz"])), key=lambda i: abs(after["frequencies_hz"][i] - 50))
    assert np.mean(before["dbfs"][low_bin]) > np.mean(after["dbfs"][low_bin_after]) + 15


def test_filter_recording_uses_the_selected_file_nyquist(tmp_path):
    rate = 8000
    time = np.arange(rate) / rate
    source_file = BytesIO()
    with wave.open(source_file, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(np.rint(10000 * np.sin(2 * np.pi * 1000 * time)).astype("<i2").tobytes())
    service = RecordingService(output_directory=tmp_path)
    source = service.import_audio(BytesIO(source_file.getvalue()), "low-rate.wav")

    with pytest.raises(ValueError, match="Nyquist"):
        filter_recording(service, source["recording_id"], low_hz=100, high_hz=4000)
    result = filter_recording(service, source["recording_id"], low_hz=100, high_hz=3500)
    assert result["settings"]["nyquist_hz"] == 4000
