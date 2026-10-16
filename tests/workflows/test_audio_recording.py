import wave

import numpy as np
import pytest

from speech_to_text.features.model_deployment import AudioInputError, start_audio_recording
from speech_to_text.workflows.audio_recording import RecordingService


class FakeSource:
    sample_rate = 16000
    channels = 1

    def __init__(self, *, device, on_audio, on_error):
        self.on_audio = on_audio
        self.on_error = on_error
        self.stopped = False

    def start(self):
        self.on_audio(np.zeros((800, 1), dtype=np.float32))

    def stop(self):
        self.stopped = True


def test_capture_writes_silent_mono_pcm16_wav_and_retains_after_restart(tmp_path):
    service = RecordingService(output_directory=tmp_path, audio_source_factory=FakeSource)
    started = service.start("USB microphone")
    stopped = service.stop(started["recording_id"])

    assert stopped["status"] == "completed"
    wav_path = service.audio_path(started["recording_id"])
    with wave.open(str(wav_path), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16000
        assert wav.getnframes() == 800
        assert set(wav.readframes(800)) == {0}

    restored = RecordingService(output_directory=tmp_path)
    assert restored.get(started["recording_id"])["status"] == "completed"
    assert restored.audio_path(started["recording_id"])


def test_service_shutdown_stops_active_capture(tmp_path):
    service = RecordingService(output_directory=tmp_path, audio_source_factory=FakeSource)
    started = service.start()
    service.close()
    assert service.get(started["recording_id"])["status"] == "completed"


def test_capture_fails_on_source_error_and_keeps_partial_audio(tmp_path):
    class FailingSource(FakeSource):
        def start(self):
            self.on_audio(np.ones((400, 1), dtype=np.float32) * 0.25)
            self.on_error(AudioInputError("device unplugged"))

    session = start_audio_recording("mic", tmp_path / "partial.wav", audio_source_factory=FailingSource)
    final = session.wait()

    assert final["status"] == "failed"
    assert "device unplugged" in final["error"]
    with wave.open(str(tmp_path / "partial.wav"), "rb") as wav:
        assert wav.getnframes() == 400


def test_stereo_capture_continues_after_input_overflow_and_downmixes(tmp_path):
    from speech_to_text.features.model_deployment.capture import AudioInputOverflow

    class StereoSource(FakeSource):
        channels = 2

        def start(self):
            self.on_error(AudioInputOverflow("input overflow"))
            self.on_audio(np.tile([0.0, 0.5], (160, 1)).astype(np.float32))

    session = start_audio_recording(
        "mic", tmp_path / "stereo.wav", audio_source_factory=StereoSource
    )
    final = session.stop()
    assert final["status"] == "completed"
    assert final["warnings"] == ["input overflow"]
    with wave.open(str(tmp_path / "stereo.wav"), "rb") as wav:
        assert wav.getnchannels() == 1
        samples = np.frombuffer(wav.readframes(160), dtype="<i2")
    assert samples.shape == (160,)
    assert np.all(samples == 8191)


def test_capture_stops_at_maximum_and_rejects_invalid_duration(tmp_path):
    class ContinuousSource(FakeSource):
        def start(self):
            self.on_audio(np.ones((400, 1), dtype=np.float32))

    session = start_audio_recording("mic", tmp_path / "bounded.wav", max_seconds=0.01, audio_source_factory=ContinuousSource)
    final = session.wait()
    assert final["status"] == "completed"
    assert final["duration_seconds"] <= 0.01

    with pytest.raises(ValueError):
        start_audio_recording("mic", tmp_path / "bad.wav", max_seconds=float("inf"), audio_source_factory=FakeSource)


def test_startup_failure_closes_source_and_no_audio_is_stopped_by_wall_limit(tmp_path):
    failed_sources = []

    class FailingStart(FakeSource):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            failed_sources.append(self)

        def start(self):
            raise RuntimeError("device could not start")

    with pytest.raises(RuntimeError, match="could not start"):
        start_audio_recording("mic", tmp_path / "failed.wav", audio_source_factory=FailingStart)
    assert failed_sources[0].stopped

    silent_sources = []

    class SilentSource(FakeSource):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            silent_sources.append(self)

        def start(self):
            pass

    session = start_audio_recording("mic", tmp_path / "silent.wav", max_seconds=0.01, audio_source_factory=SilentSource)
    assert session.wait()["status"] == "completed"
    assert session.snapshot()["duration_seconds"] == 0
    assert silent_sources[0].stopped


def test_writer_failure_closes_microphone(monkeypatch, tmp_path):
    import speech_to_text.features.model_deployment.recording as recording

    class BrokenWave:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def setnchannels(self, value):
            pass

        def setsampwidth(self, value):
            pass

        def setframerate(self, value):
            pass

        def writeframesraw(self, value):
            raise OSError("disk full")

    monkeypatch.setattr(recording.wave, "open", lambda *args, **kwargs: BrokenWave())
    sources = []

    class TrackingSource(FakeSource):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            sources.append(self)

    session = start_audio_recording("mic", tmp_path / "broken.wav", audio_source_factory=TrackingSource)
    final = session.wait()
    assert final["status"] == "failed"
    assert "disk full" in final["error"]
    assert sources[0].stopped


def test_recording_ids_do_not_allow_arbitrary_paths_and_active_capture_is_unique(tmp_path):
    service = RecordingService(output_directory=tmp_path, audio_source_factory=FakeSource)
    started = service.start()
    service.stop(started["recording_id"])
    second = service.start()
    assert service.get(started["recording_id"])["status"] == "completed"
    with pytest.raises(RuntimeError):
        service.start()
    assert service.get("../../outside") is None
    assert service.list()[0]["recording_id"] == second["recording_id"]
    with pytest.raises(RuntimeError):
        service.delete(second["recording_id"])
    service.stop(second["recording_id"])
    assert service.delete(started["recording_id"])
    assert service.delete(second["recording_id"])
    assert not list(tmp_path.glob("*.wav"))
