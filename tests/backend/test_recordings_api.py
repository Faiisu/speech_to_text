import io
import wave

import numpy as np
from fastapi.testclient import TestClient

from speech_to_text.backend.app import create_app
from speech_to_text.backend.dependencies import BackendRuntime
from speech_to_text.features.model_deployment import AudioInputError
from speech_to_text.workflows.audio_recording import RecordingService


class FakeSource:
    sample_rate = 16000
    channels = 1

    def __init__(self, *, device, on_audio, on_error):
        self.on_audio = on_audio

    def start(self):
        self.on_audio(np.zeros((160, 1), dtype=np.float32))

    def stop(self):
        pass


class FailingSource(FakeSource):
    def start(self):
        raise AudioInputError("rtsp://user:password@camera.local/private")


class DisconnectingSource(FakeSource):
    def __init__(self, *, device, on_audio, on_error):
        self.on_audio = on_audio
        self.on_error = on_error

    def start(self):
        self.on_audio(np.full(800, 0.25, dtype=np.float32))
        self.on_error(AudioInputError("RTSP audio stream ended"))


class FakeStore:
    def list_destinations(self):
        return []

    def close(self):
        pass


class FakeWorkflowService:
    def close(self):
        pass


def _wav_bytes(*, rate=16000, channels=1, frames=1600, width=2):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(b"\0" * frames * channels * width)
    return output.getvalue()


def _runtime(tmp_path, *, max_seconds=120):
    service = RecordingService(output_directory=tmp_path, audio_source_factory=FakeSource, max_seconds=max_seconds)
    runtime = BackendRuntime(
        workflow_service=FakeWorkflowService(),
        profile_store=FakeStore(),
        destination_registry=object(),
        recording_service=service,
    )
    return service, runtime


def test_recording_routes_start_stop_list_download_and_delete(tmp_path):
    service = RecordingService(output_directory=tmp_path, audio_source_factory=FakeSource)
    runtime = BackendRuntime(
        workflow_service=FakeWorkflowService(),
        profile_store=FakeStore(),
        destination_registry=object(),
        recording_service=service,
    )
    with TestClient(create_app(runtime=runtime)) as client:
        started = client.post("/api/v1/recordings", json={"device": "Studio Mic"})
        assert started.status_code == 201
        recording_id = started.json()["recording_id"]
        assert client.post("/api/v1/recordings", json={}).status_code == 409
        assert client.get(f"/api/v1/recordings/{recording_id}/audio").status_code == 409

        stopped = client.post(f"/api/v1/recordings/{recording_id}/stop")
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "completed"
        audio = client.get(f"/api/v1/recordings/{recording_id}/audio")
        assert audio.status_code == 200
        assert audio.headers["content-type"] == "audio/wav"
        assert audio.content[:4] == b"RIFF"
        assert client.get("/api/v1/recordings").json()["recordings"][0]["recording_id"] == recording_id
        assert client.delete(f"/api/v1/recordings/{recording_id}").status_code == 204
        assert client.get(f"/api/v1/recordings/{recording_id}").status_code == 404


def test_recording_routes_reject_paths_and_unknown_ids(tmp_path):
    service = RecordingService(output_directory=tmp_path, audio_source_factory=FakeSource)
    runtime = BackendRuntime(
        workflow_service=FakeWorkflowService(),
        profile_store=FakeStore(),
        destination_registry=object(),
        recording_service=service,
    )
    with TestClient(create_app(runtime=runtime)) as client:
        assert client.post("/api/v1/recordings", json={"path": "/etc/passwd"}).status_code == 422
        assert client.get("/api/v1/recordings/../../etc/passwd").status_code == 404


def test_rtsp_recording_validates_source_and_persists_only_non_secret_definition(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEECH_TO_TEXT_RTSP_SECRET_REFS", "CAMERA_URL")
    monkeypatch.setenv("CAMERA_URL", "rtsp://user:password@camera.local/audio?token=secret")
    service, runtime = _runtime(tmp_path)
    with TestClient(create_app(runtime=runtime)) as client:
        invalid = (
            {"source_type": "mystery"},
            {"source_type": "rtsp", "device": "Studio Mic", "rtsp_url": "rtsp://camera/audio"},
            {"source_type": "rtsp"},
            {"source_type": "rtsp", "rtsp_url": "rtsp://camera/audio", "rtsp_url_secret_ref": "CAMERA_URL"},
            {"source_type": "rtsp", "rtsp_url": "rtsp://user:password@camera/audio"},
            {"source_type": "microphone", "rtsp_url": None},
            {"source_type": "rtsp", "rtsp_url_secret_ref": "UNAPPROVED"},
            {"source_type": None},
            {"source_type": []},
            {"source_type": True},
            {"source_type": "rtsp", "rtsp_url": 123},
        )
        for payload in invalid:
            response = client.post("/api/v1/recordings", json=payload)
            assert response.status_code == 422, response.text
        assert service.list() == []
        assert list(tmp_path.iterdir()) == []

        started = client.post("/api/v1/recordings", json={
            "source_type": "rtsp", "rtsp_url_secret_ref": "CAMERA_URL"
        })
        assert started.status_code == 201, started.text
        snapshot = started.json()
        assert snapshot["source"] == "rtsp"
        assert snapshot["device"] is None
        assert snapshot["rtsp_url"] is None
        assert snapshot["rtsp_url_secret_ref"] == "CAMERA_URL"
        assert client.post("/api/v1/recordings", json={}).status_code == 409
        stopped = client.post(f"/api/v1/recordings/{snapshot['recording_id']}/stop").json()
        assert stopped["status"] == "completed"
        assert client.get(f"/api/v1/recordings/{snapshot['recording_id']}/audio").content[:4] == b"RIFF"
        assert "password" not in (tmp_path / f"{snapshot['recording_id']}.json").read_text()
        assert "token=secret" not in (tmp_path / f"{snapshot['recording_id']}.json").read_text()

    restored = RecordingService(output_directory=tmp_path)
    assert restored.get(snapshot["recording_id"])["source"] == "rtsp"
    assert restored.get(snapshot["recording_id"])["rtsp_url_secret_ref"] == "CAMERA_URL"


def test_rtsp_capture_startup_failure_cleans_files_and_hides_error(tmp_path):
    service = RecordingService(output_directory=tmp_path, audio_source_factory=FailingSource)
    runtime = BackendRuntime(
        workflow_service=FakeWorkflowService(), profile_store=FakeStore(),
        destination_registry=object(), recording_service=service,
    )
    with TestClient(create_app(runtime=runtime)) as client:
        response = client.post("/api/v1/recordings", json={
            "source_type": "rtsp", "rtsp_url": "rtsp://camera.local/audio"
        })
        assert response.status_code == 503
        assert "password" not in response.text
    assert service.list() == []
    assert list(tmp_path.iterdir()) == []


def test_rtsp_uses_feature_factory_without_opening_default_microphone(tmp_path, monkeypatch):
    from speech_to_text.workflows.audio_recording import service as recording_module
    from speech_to_text.features.model_deployment import recording as capture_module

    constructed = []

    def rtsp_factory(url):
        constructed.append(url)
        return FakeSource

    class NoMicrophone:
        def __init__(self, **_):
            raise AssertionError("RTSP selection must not open the host microphone")

    monkeypatch.setattr(recording_module, "create_rtsp_audio_source_factory", rtsp_factory)
    monkeypatch.setattr(capture_module, "SoundDeviceSource", NoMicrophone)
    service = RecordingService(output_directory=tmp_path)
    started = service.start(source_type="rtsp", rtsp_url="rtsp://camera.local/audio")
    assert constructed == ["rtsp://camera.local/audio"]
    assert started["source"] == "rtsp"
    assert service.stop(started["recording_id"])["status"] == "completed"


def test_rtsp_disconnect_keeps_partial_wav_and_failed_metadata_after_restart(tmp_path, monkeypatch):
    import time
    from speech_to_text.workflows.audio_recording import service as recording_module

    monkeypatch.setattr(recording_module, "create_rtsp_audio_source_factory", lambda _url: DisconnectingSource)
    service = RecordingService(output_directory=tmp_path)
    started = service.start(source_type="rtsp", rtsp_url="rtsp://camera.local/audio")
    deadline = time.monotonic() + 2
    while service.get(started["recording_id"])["status"] == "recording" and time.monotonic() < deadline:
        time.sleep(0.01)
    failed = service.get(started["recording_id"])
    assert failed["status"] == "failed"
    assert failed["duration_seconds"] > 0
    assert failed["error"] == "RTSP audio stream ended"
    with wave.open(str(service.audio_path(started["recording_id"])), "rb") as wav:
        assert wav.getnframes() == 800

    restored = RecordingService(output_directory=tmp_path)
    recovered = restored.get(started["recording_id"])
    assert recovered["status"] == "failed"
    assert recovered["source"] == "rtsp"
    assert recovered["rtsp_url"] == "rtsp://camera.local/audio"
    assert recovered["duration_seconds"] > 0


def test_recording_start_reports_unavailable_storage_as_503(tmp_path):
    non_directory = tmp_path / "file"
    non_directory.write_text("occupied")
    service = RecordingService(
        output_directory=non_directory / "recordings", audio_source_factory=FakeSource
    )
    runtime = BackendRuntime(
        workflow_service=FakeWorkflowService(),
        profile_store=FakeStore(),
        destination_registry=object(),
        recording_service=service,
    )
    with TestClient(create_app(runtime=runtime)) as client:
        response = client.post("/api/v1/recordings", json={})
        assert response.status_code == 503


def test_upload_wav_persists_original_bytes_and_restores_metadata(tmp_path):
    content = _wav_bytes(channels=2, frames=1600)
    service, runtime = _runtime(tmp_path)
    with TestClient(create_app(runtime=runtime)) as client:
        response = client.post(
            "/api/v1/recordings/upload",
            files={"file": ("../../my\\voice.wav", content, "audio/wav")},
        )
        assert response.status_code == 201
        snapshot = response.json()
        recording_id = snapshot["recording_id"]
        assert snapshot["source"] == "upload"
        assert snapshot["filename"] == "voice.wav"
        assert snapshot["duration_seconds"] == 0.1
        assert client.get(f"/api/v1/recordings/{recording_id}/audio").content == content
        assert client.get("/api/v1/recordings").json()["recordings"][0] == snapshot

    restored = RecordingService(output_directory=tmp_path)
    assert restored.get(recording_id)["source"] == "upload"
    assert restored.get(recording_id)["filename"] == "voice.wav"
    assert restored.delete(recording_id)


def _compressed_bytes(extension):
    import av

    output = io.BytesIO()
    container_format, codec = ("mp3", "libmp3lame") if extension == "mp3" else ("mp4", "aac")
    with av.open(output, "w", format=container_format) as container:
        stream = container.add_stream(codec, rate=44100)
        stream.layout = "stereo"
        frame = av.AudioFrame.from_ndarray(
            np.full((2, 44100), 0.1, dtype=np.float32), format="fltp", layout="stereo"
        )
        frame.sample_rate = 44100
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return output.getvalue()


def test_upload_accepts_mp3_and_m4a_and_stores_normalized_wav(tmp_path):
    service, runtime = _runtime(tmp_path)
    with TestClient(create_app(runtime=runtime)) as client:
        for extension in ("mp3", "m4a"):
            response = client.post(
                "/api/v1/recordings/upload",
                files={"file": (f"sample.{extension}", _compressed_bytes(extension))},
            )
            assert response.status_code == 201, response.text
            snapshot = response.json()
            assert snapshot["source"] == "upload"
            assert snapshot["filename"] == f"sample.{extension}"
            saved = client.get(f"/api/v1/recordings/{snapshot['recording_id']}/audio")
            assert saved.status_code == 200
            with wave.open(io.BytesIO(saved.content), "rb") as wav:
                assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
                assert 15000 <= wav.getnframes() <= 18000


def test_compressed_upload_limits_and_invalid_media_leave_no_files(tmp_path):
    _, runtime = _runtime(tmp_path, max_seconds=0.05)
    with TestClient(create_app(runtime=runtime)) as client:
        for extension in ("mp3", "m4a"):
            response = client.post(
                "/api/v1/recordings/upload",
                files={"file": (f"long.{extension}", _compressed_bytes(extension))},
            )
            assert response.status_code == 422, response.text
        for name, data in (
            ("renamed.mp3", _wav_bytes()),
            ("broken.m4a", b"not m4a"),
            ("playlist.mp3", b"#EXTM3U\n#EXTINF:1,\nhttp://127.0.0.1:1/audio.mp3\n"),
        ):
            response = client.post("/api/v1/recordings/upload", files={"file": (name, data)})
            assert response.status_code == 422, response.text
    assert list(tmp_path.iterdir()) == []


def test_upload_rejects_invalid_truncated_oversized_and_too_long_files(tmp_path):
    _, runtime = _runtime(tmp_path, max_seconds=0.05)
    too_long = _wav_bytes(frames=1600)
    truncated = _wav_bytes()[:-8]
    oversized = b"x" * (20 * 1024 * 1024 + 1)
    with TestClient(create_app(runtime=runtime)) as client:
        for filename, content, expected in (
            ("empty.wav", b"", 422),
            ("bad.wav", b"not a wav", 422),
            ("truncated.wav", truncated, 422),
            ("long.wav", too_long, 422),
            ("large.wav", oversized, 413),
            ("unsupported.ogg", _wav_bytes(), 422),
        ):
            response = client.post(
                "/api/v1/recordings/upload", files={"file": (filename, content)}
            )
            assert response.status_code == expected, response.text
    assert sorted(path.name for path in tmp_path.iterdir()) == []


def test_upload_works_while_microphone_capture_remains_active(tmp_path, monkeypatch):
    service, runtime = _runtime(tmp_path)
    with TestClient(create_app(runtime=runtime)) as client:
        started = client.post("/api/v1/recordings", json={}).json()
        upload = client.post(
            "/api/v1/recordings/upload", files={"file": ("desk.wav", _wav_bytes())}
        )
        assert upload.status_code == 201
        assert upload.json()["recording_id"] != started["recording_id"]
        assert service.snapshot(started["recording_id"])["status"] == "recording"
        assert client.post(f"/api/v1/recordings/{started['recording_id']}/stop").json()["status"] == "completed"


def test_upload_storage_failure_leaves_no_recording_or_files(tmp_path, monkeypatch):
    service, runtime = _runtime(tmp_path)

    def fail_manifest(_record):
        raise OSError("disk full")

    monkeypatch.setattr(service, "_save_manifest", fail_manifest)
    with TestClient(create_app(runtime=runtime)) as client:
        response = client.post(
            "/api/v1/recordings/upload", files={"file": ("desk.wav", _wav_bytes())}
        )
        assert response.status_code == 503
    assert service.list() == []
    assert list(tmp_path.iterdir()) == []


def test_slow_upload_does_not_block_stopping_microphone(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from speech_to_text.workflows.audio_recording import service as recording_module

    service, runtime = _runtime(tmp_path)
    gate = threading.Event()
    reached = threading.Event()
    validate = recording_module.validate_wav

    def slow_validate(*args, **kwargs):
        reached.set()
        assert gate.wait(5)
        return validate(*args, **kwargs)

    monkeypatch.setattr(recording_module, "validate_wav", slow_validate)
    with ThreadPoolExecutor(max_workers=2) as executor:
        with TestClient(create_app(runtime=runtime)) as client:
            recording_id = client.post("/api/v1/recordings", json={}).json()["recording_id"]
            upload = executor.submit(service.import_audio, io.BytesIO(_wav_bytes()), "slow.wav")
            try:
                assert reached.wait(2)
                stopped = executor.submit(service.stop, recording_id).result(timeout=2)
                assert stopped["status"] == "completed"
            finally:
                gate.set()
            assert upload.result(timeout=5)["source"] == "upload"


def test_compressed_upload_storage_errors_and_missing_codec_report_503(tmp_path, monkeypatch):
    import sys
    from speech_to_text.features.model_deployment import audio

    payload = _compressed_bytes("mp3")
    _, runtime = _runtime(tmp_path)
    with TestClient(create_app(runtime=runtime)) as client:
        with monkeypatch.context() as patch:
            patch.setitem(sys.modules, "av", None)
            response = client.post("/api/v1/recordings/upload", files={"file": ("sample.mp3", payload)})
            assert response.status_code == 503
        original_open = audio.wave.open

        def fail_write(path, mode):
            if mode == "wb":
                raise OSError("disk full")
            return original_open(path, mode)

        monkeypatch.setattr(audio.wave, "open", fail_write)
        response = client.post("/api/v1/recordings/upload", files={"file": ("sample.mp3", payload)})
        assert response.status_code == 503
    assert list(tmp_path.iterdir()) == []
