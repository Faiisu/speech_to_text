"""HTTP and lifecycle contracts for IPCAM RTSP audio runs."""

from __future__ import annotations

import queue
import threading
import time

import pytest
from fastapi.testclient import TestClient

from speech_to_text.backend.app import create_app
from speech_to_text.backend.dependencies import BackendRuntime
from speech_to_text.backend.profile_store import SQLiteProfileStore
from speech_to_text.features.model_deployment import (
    RTSPCaptureUnavailableError,
    create_rtsp_audio_source_factory,
    create_rtsp_secret_source_factory,
)
from speech_to_text.workflows.transcribe_match_forward import (
    TranscriptionService,
    WorkflowUnavailableError,
)
from speech_to_text.workflows.transcribe_match_forward import service as service_module


class _Workflow:
    def __init__(self):
        self.output_queue = queue.Queue()
        self.output_queue.put({"type": "completed", "status": "completed"})

    def wait(self, timeout=0):
        return None

    def stop(self, timeout=30):
        pass


class _Service:
    default_model = "turbo"
    default_runtime = "openvino-gpu"

    def __init__(self):
        self.started = []

    def validate_keywords(self, keywords):
        return tuple(keywords)

    def validate_model_runtime(self, _model, _runtime):
        return None

    def start_microphone(
        self,
        device,
        keywords,
        flow_config=None,
        *,
        execution_mode="shared",
        model=None,
        runtime=None,
        forwarder=None,
        audio_source_factory=None,
    ):
        self.started.append(
            {
                "device": device,
                "keywords": tuple(keywords),
                "execution_mode": execution_mode,
                "model": model,
                "runtime": runtime,
                "audio_source_factory": audio_source_factory,
            }
        )
        return _Workflow()

    def close(self):
        pass


class _FailingWorkflow:
    def __init__(self):
        self.output_queue = queue.Queue()
        self.output_queue.put({"type": "workflow_error", "error": "camera stream ended"})
        self.output_queue.put({"type": "completed", "status": "failed"})

    def wait(self, timeout=0):
        return None

    def stop(self, timeout=30):
        pass


class _ActiveWorkflow:
    def __init__(self):
        self.output_queue = queue.Queue()
        self.stopped = threading.Event()

    def wait(self, timeout=0):
        if not self.stopped.is_set():
            raise TimeoutError

    def stop(self, timeout=30):
        self.stopped.set()


def _client(tmp_path):
    service = _Service()
    runtime = BackendRuntime(
        workflow_service=service,
        profile_store=SQLiteProfileStore(tmp_path / "profiles.sqlite3"),
    )
    client = TestClient(create_app(runtime=runtime))
    return client, service


def test_rtsp_profile_round_trip_and_start_keep_secret_value_private(tmp_path, monkeypatch):
    secret_url = "rtsps://operator:password@camera.example/live?token=private"
    monkeypatch.setenv("SPEECH_TO_TEXT_RTSP_SECRET_REFS", "CAMERA_AUDIO_URL")
    monkeypatch.setenv("CAMERA_AUDIO_URL", secret_url)
    client, service = _client(tmp_path)
    with client:
        body = {
            "name": "Lobby camera",
            "source_type": "rtsp",
            "device": None,
            "rtsp_url_secret_ref": "CAMERA_AUDIO_URL",
            "keywords": ["สวัสดี"],
        }
        created = client.post("/api/v1/profiles", json=body)
        assert created.status_code == 201
        profile = created.json()
        assert profile["source_type"] == "rtsp"
        assert profile["device"] is None
        assert profile["rtsp_url"] is None
        assert profile["rtsp_url_secret_ref"] == "CAMERA_AUDIO_URL"
        assert secret_url not in created.text
        assert client.get("/api/v1/profiles").json()["profiles"] == [profile]

        started = client.post(f"/api/v1/profiles/{profile['profile_id']}/runs")
        assert started.status_code == 202
        factory = service.started[0]["audio_source_factory"]
        assert factory is not None
        assert secret_url not in repr(factory)
        run = client.get(
            f"/api/v1/transcriptions/{started.json()['workflow_id']}"
        ).json()
        assert run["kind"] == "microphone"
        assert run["source_type"] == "rtsp"
        assert run["device"] is None
        assert run["rtsp_url_secret_ref"] == "CAMERA_AUDIO_URL"
        assert secret_url not in str(run)


def test_direct_rtsp_endpoint_validates_source_and_redacts_resolved_value(
    tmp_path, monkeypatch
):
    secret_url = "rtsp://operator:password@camera.example/live?token=private"
    monkeypatch.setenv("SPEECH_TO_TEXT_RTSP_SECRET_REFS", "CAMERA_AUDIO_URL")
    monkeypatch.setenv("CAMERA_AUDIO_URL", secret_url)
    client, service = _client(tmp_path)
    with client:
        invalid = client.post(
            "/api/v1/transcriptions/rtsp",
            json={"keywords": ["สวัสดี"], "rtsp_url": "rtsp://user:pass@camera:554/live", "rtsp_username": "another"},
        )
        assert invalid.status_code == 422
        assert "user:pass" not in invalid.text

        authenticated = client.post(
            "/api/v1/transcriptions/rtsp",
            json={"keywords": ["สวัสดี"], "rtsp_url": "rtsp://camera:554/live", "rtsp_username": "operator", "rtsp_password": "p@ss:/%#"},
        )
        assert authenticated.status_code == 202
        auth_run = client.get(f"/api/v1/transcriptions/{authenticated.json()['workflow_id']}").json()
        factory = service.started[-1]["audio_source_factory"]
        assert factory._url == "rtsp://operator:p%40ss%3A%2F%25%23@camera:554/live"
        assert "p@ss" not in repr(factory) + authenticated.text + str(auth_run)

        started = client.post(
            "/api/v1/transcriptions/rtsp",
            json={"keywords": ["สวัสดี"], "rtsp_url_secret_ref": "CAMERA_AUDIO_URL"},
        )
        assert started.status_code == 202
        assert service.started[0]["device"] is None
        assert service.started[0]["audio_source_factory"] is not None
        run = client.get(
            f"/api/v1/transcriptions/{started.json()['workflow_id']}"
        ).json()
        assert run["source_type"] == "rtsp"
        assert secret_url not in started.text + str(run)

        rejected = client.post(
            "/api/v1/transcriptions/rtsp",
            json={"keywords": ["สวัสดี"], "rtsp_url_secret_ref": "UNAPPROVED"},
        )
        assert rejected.status_code == 422
        assert secret_url not in rejected.text


def test_credential_request_model_representations_hide_password_and_inline_url():
    from speech_to_text.backend.api.schemas.transcription import (
        MicrophoneProfileDefinition,
        RTSPTranscriptionRequest,
    )

    password = "request-secret"
    url = f"rtsp://user:{password}@camera/live"
    request = RTSPTranscriptionRequest(keywords=["word"], rtsp_url=url, rtsp_password=password)
    profile = MicrophoneProfileDefinition(
        name="camera", source_type="rtsp", device=None, rtsp_url=url,
        rtsp_password=password, keywords=["word"],
    )
    assert password not in repr(request) + repr(profile)
    assert url not in repr(request) + repr(profile)


def test_rtsp_secret_ref_must_be_allowlisted_and_configured(monkeypatch):
    monkeypatch.setenv("SPEECH_TO_TEXT_RTSP_SECRET_REFS", "CAMERA_URL")
    monkeypatch.delenv("CAMERA_URL", raising=False)
    from speech_to_text.features.model_deployment import ConfigurationError

    with pytest.raises(ConfigurationError, match="not configured"):
        create_rtsp_secret_source_factory("CAMERA_URL")
    with pytest.raises(ConfigurationError, match="not deployment-approved"):
        create_rtsp_secret_source_factory("OTHER_URL")


def test_rtsp_capture_support_is_checked_before_model_loading(monkeypatch):
    service = TranscriptionService()

    class SourceFactory:
        def preflight(self):
            raise RTSPCaptureUnavailableError("RTSP support is unavailable")

    monkeypatch.setattr(
        service,
        "_acquire_model_config",
        lambda _config: pytest.fail("model must not load before RTSP preflight"),
    )
    with pytest.raises(WorkflowUnavailableError, match="RTSP support"):
        service.start_microphone(
            None,
            ["สวัสดี"],
            {},
            audio_source_factory=SourceFactory(),
        )


@pytest.mark.parametrize("execution_mode", ["shared", "per_workflow_process"])
def test_rtsp_factory_reaches_both_service_execution_modes(
    monkeypatch, execution_mode
):
    service = TranscriptionService()
    factory = create_rtsp_audio_source_factory("rtsp://camera.example:554/live")
    calls = {}

    class Lease:
        handle = object()
        released = False

        def release(self):
            pass

    class Group:
        sessions = [object()]

        def stop(self):
            pass

        def abort(self):
            pass

    monkeypatch.setattr(service, "_acquire_model_config", lambda _config: Lease())

    def start_shared(*_args, **kwargs):
        calls["shared_factory"] = kwargs.get("audio_source_factory")
        return _Workflow()

    def start_group(**kwargs):
        calls["group_factory"] = kwargs["audio_source_factory"]
        return Group()

    def forward_session(*_args, **kwargs):
        calls["forward_factory"] = kwargs.get("audio_source_factory")
        return _Workflow()

    monkeypatch.setattr(service_module, "start_microphone_and_forward", start_shared)
    monkeypatch.setattr(service_module, "start_multiprocess_microphone_flows", start_group)
    monkeypatch.setattr(service_module, "forward_session", forward_session)
    service.start_microphone(
        None,
        ["สวัสดี"],
        {},
        execution_mode=execution_mode,
        audio_source_factory=factory,
    )

    if execution_mode == "shared":
        assert calls["shared_factory"] is factory
    else:
        assert calls["group_factory"] is factory


def test_rtsp_recovery_keeps_start_snapshot_and_stop_cancels_reconnect(
    tmp_path, monkeypatch
):
    first_url = "rtsp://first-user:first-pass@camera-one.example/live?token=first"
    second_url = "rtsp://second-user:second-pass@camera-two.example/live?token=second"
    monkeypatch.setenv(
        "SPEECH_TO_TEXT_RTSP_SECRET_REFS", "OLD_CAMERA_URL,NEW_CAMERA_URL"
    )
    monkeypatch.setenv("OLD_CAMERA_URL", first_url)
    monkeypatch.setenv("NEW_CAMERA_URL", second_url)

    class RecoveringService(_Service):
        def start_microphone(self, *args, **kwargs):
            super().start_microphone(*args, **kwargs)
            return _FailingWorkflow() if len(self.started) == 1 else _ActiveWorkflow()

    service = RecoveringService()
    runtime = BackendRuntime(
        workflow_service=service,
        profile_store=SQLiteProfileStore(tmp_path / "profiles.sqlite3"),
        recovery_initial_delay=0.2,
        recovery_max_delay=0.4,
    )
    client = TestClient(create_app(runtime=runtime))
    profile_body = {
        "name": "Camera input",
        "source_type": "rtsp",
        "rtsp_url_secret_ref": "OLD_CAMERA_URL",
        "device": None,
        "keywords": ["สวัสดี"],
    }

    with client:
        profile = client.post("/api/v1/profiles", json=profile_body).json()
        started = client.post(f"/api/v1/profiles/{profile['profile_id']}/runs")
        assert started.status_code == 202
        workflow_id = started.json()["workflow_id"]

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            events = client.get(
                f"/api/v1/transcriptions/{workflow_id}/events?after=0"
            ).json()["events"]
            if any(event["type"] == "recovering" for event in events):
                break
            time.sleep(0.01)
        assert any(event["type"] == "recovering" for event in events)

        monkeypatch.setenv("OLD_CAMERA_URL", second_url)
        replacement = client.put(
            f"/api/v1/profiles/{profile['profile_id']}",
            json={**profile_body, "name": "Updated camera", "rtsp_url_secret_ref": "NEW_CAMERA_URL"},
        )
        assert replacement.status_code == 200

        deadline = time.monotonic() + 3
        while len(service.started) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(service.started) == 2
        assert service.started[0]["audio_source_factory"] is service.started[1]["audio_source_factory"]
        assert service.started[1]["audio_source_factory"]._url == first_url
        status = client.get(f"/api/v1/transcriptions/{workflow_id}").json()
        assert status["profile_name"] == "Camera input"
        assert status["rtsp_url_secret_ref"] == "OLD_CAMERA_URL"
        assert first_url not in str(status)
        assert second_url not in str(status)

        stopped = client.post(f"/api/v1/transcriptions/{workflow_id}/stop")
        assert stopped.status_code == 202
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            status = client.get(f"/api/v1/transcriptions/{workflow_id}").json()
            if status["status"] in {"stopped", "failed"}:
                break
            time.sleep(0.01)
        assert status["status"] == "stopped"
        time.sleep(0.25)
        assert len(service.started) == 2
