import numpy as np
from fastapi.testclient import TestClient

from speech_to_text.backend.app import create_app
from speech_to_text.backend.dependencies import BackendRuntime
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


class FakeStore:
    def list_destinations(self):
        return []

    def close(self):
        pass


class FakeWorkflowService:
    def close(self):
        pass


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
