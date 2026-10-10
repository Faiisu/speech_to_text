"""Public API behavior for asynchronous file-replay stress tests."""

from __future__ import annotations

import queue
import threading
import time

import pytest
from fastapi.testclient import TestClient

from speech_to_text.backend.app import create_app
from speech_to_text.backend.dependencies import BackendRuntime
from speech_to_text.workflows.transcribe_match_forward import (
    WorkflowConfigurationError,
)


class FakeWorkflowService:
    default_model = "model-default"
    default_runtime = "openvino-gpu"
    default_precision = "bf16"

    def __init__(self):
        self.resolved = []
        self.microphone_workflow = None

    def resolve_model_config(self, *, model=None, runtime=None):
        model = self.default_model if model is None else model
        runtime = self.default_runtime if runtime is None else runtime
        self.resolved.append((model, runtime))
        self.validate_model_runtime(model, runtime)
        precision = "int8" if runtime == "ctranslate2" else "bf16"
        return {"model": model, "runtime": runtime, "precision": precision}

    def validate_model_runtime(self, model, runtime):
        if model not in {"model-default", "model-alt"}:
            raise WorkflowConfigurationError("unknown model")
        if runtime not in {"openvino-gpu", "ctranslate2"}:
            raise WorkflowConfigurationError("unsupported runtime")
        if model == "model-alt" and runtime == "openvino-gpu":
            raise WorkflowConfigurationError("incompatible model/runtime pair")

    def validate_keywords(self, keywords):
        if not keywords or any(not word.strip() for word in keywords):
            raise WorkflowConfigurationError("keywords must be non-empty")
        return tuple(keywords)

    def start_microphone(
        self, device, keywords, flow_config, *, execution_mode="shared", model=None
    ):
        workflow = FakeMicrophoneWorkflow(flow_config["source_id"])
        self.microphone_workflow = workflow
        return workflow

    def close(self):
        pass


class FakeMicrophoneWorkflow:
    def __init__(self, source_id):
        self.source_id = source_id
        self.output_queue = queue.Queue()

    def stop(self, timeout=30):
        self.output_queue.put(
            {"type": "completed", "source_id": self.source_id, "status": "stopped"}
        )

    def wait(self, timeout=0):
        raise TimeoutError


class FakeProfileStore:
    def close(self):
        pass


@pytest.fixture
def stress_api():
    entered = threading.Event()
    release = threading.Event()
    calls = []
    service = FakeWorkflowService()
    report = {
        "shared": {"trials": [{"workflow_count": 1, "status": "completed"}]},
        "per_workflow_process": {
            "trials": [{"workflow_count": 1, "status": "unavailable"}]
        },
    }

    def stress_workflow(*, clip_path, model_config, progress_callback):
        calls.append((clip_path, model_config))
        progress_callback(
            {
                "event": "trial_started",
                "topology": "shared",
                "workflow_count": 1,
            }
        )
        entered.set()
        release.wait(timeout=3)
        progress_callback(
            {
                "event": "trial_completed",
                "topology": "shared",
                "workflow_count": 1,
                "status": "completed",
            }
        )
        progress_callback({"event": "matrix_completed"})
        return report

    runtime = BackendRuntime(
        workflow_service=service,
        profile_store=FakeProfileStore(),
        stress_workflow=stress_workflow,
    )
    app = create_app(runtime=runtime)
    with TestClient(app) as client:
        yield client, service, entered, release, calls, report
        release.set()


def wait_for_status(client, stress_test_id, expected, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/stress-tests/{stress_test_id}")
        if response.status_code == 200 and response.json()["status"] == expected:
            return response.json()
        time.sleep(0.01)
    pytest.fail(f"stress test {stress_test_id} did not reach {expected!r}")


def test_stress_request_defaults_resolve_overrides_and_reject_invalid_values(stress_api):
    client, service, entered, release, calls, _ = stress_api

    defaulted = client.post("/api/v1/stress-tests", json={})
    assert defaulted.status_code == 202
    default_id = defaulted.json()["stress_test_id"]
    default_status = client.get(f"/api/v1/stress-tests/{default_id}").json()
    assert (default_status["model"], default_status["runtime"], default_status["precision"]) == (
        "model-default",
        "openvino-gpu",
        "bf16",
    )
    assert service.resolved[:2] == [
        ("model-default", "openvino-gpu"),
        ("model-default", "openvino-gpu"),
    ]

    release.set()
    wait_for_status(client, default_id, "completed")
    release.clear()
    entered.clear()

    overridden = client.post(
        "/api/v1/stress-tests",
        json={"model": "model-alt", "runtime": "ctranslate2", "precision": "float32"},
    )
    assert overridden.status_code == 202
    override_id = overridden.json()["stress_test_id"]
    override_status = client.get(f"/api/v1/stress-tests/{override_id}").json()
    assert (override_status["model"], override_status["runtime"], override_status["precision"]) == (
        "model-alt",
        "ctranslate2",
        "float32",
    )
    assert entered.wait(timeout=1)
    assert calls[-1][1] == {
        "model": "model-alt",
        "runtime": "ctranslate2",
        "precision": "float32",
    }
    assert calls[-1][0].endswith("audio/test-audio.wav")

    invalid_bodies = [
        {"unknown": "field"},
        {"model": ""},
        {"model": "missing-model"},
        {"runtime": "missing-runtime"},
        {"model": "model-alt", "runtime": "openvino-gpu"},
        {"runtime": "ctranslate2", "precision": "bf16"},
        {"model": 7},
    ]
    release.set()
    wait_for_status(client, override_id, "completed")
    for body in invalid_bodies:
        response = client.post("/api/v1/stress-tests", json=body)
        assert response.status_code == 422, body


def test_stress_api_lifecycle_forwards_repeatable_cursor_events_and_report(stress_api):
    client, _, entered, release, calls, report = stress_api

    created = client.post("/api/v1/stress-tests")
    assert created.status_code == 202
    stress_id = created.json()["stress_test_id"]
    assert created.json() == {"stress_test_id": stress_id, "status": "queued"}
    assert entered.wait(timeout=1)

    running = wait_for_status(client, stress_id, "running")
    assert running["report"] is None
    first_page = client.get(f"/api/v1/stress-tests/{stress_id}/events?after=0")
    assert first_page.status_code == 200
    first = first_page.json()
    assert first["stress_test_id"] == stress_id
    assert [event["type"] for event in first["events"]] == [
        "queued",
        "started",
        "trial_started",
    ]
    assert first["events"][-1]["topology"] == "shared"
    assert first["events"][-1]["workflow_count"] == 1
    assert all(event["timestamp"].endswith("Z") for event in first["events"])

    repeated = client.get(f"/api/v1/stress-tests/{stress_id}/events?after=0")
    assert repeated.json() == first
    ahead = client.get(f"/api/v1/stress-tests/{stress_id}/events?after=999")
    assert ahead.status_code == 422
    unknown = client.get("/api/v1/stress-tests/missing/events")
    assert unknown.status_code == 404

    release.set()
    completed = wait_for_status(client, stress_id, "completed")
    assert completed["report"] == report
    all_events = client.get(
        f"/api/v1/stress-tests/{stress_id}/events?after={first['next_cursor']}"
    ).json()
    assert [event["type"] for event in all_events["events"]] == [
        "trial_completed",
        "matrix_completed",
        "completed",
    ]
    assert all_events["next_cursor"] == 6
    assert calls[0][0].endswith("audio/test-audio.wav")


def test_stress_and_microphone_starts_return_serialized_conflicts(stress_api):
    client, _, entered, release, _, _ = stress_api

    created = client.post("/api/v1/stress-tests")
    assert created.status_code == 202
    assert entered.wait(timeout=1)

    concurrent_stress = client.post("/api/v1/stress-tests")
    assert concurrent_stress.status_code == 409
    microphone_during_stress = client.post(
        "/api/v1/transcriptions/microphones", json={"keywords": ["ไทย"]}
    )
    assert microphone_during_stress.status_code == 409
    release.set()
    wait_for_status(client, created.json()["stress_test_id"], "completed")

    microphone = client.post(
        "/api/v1/transcriptions/microphones", json={"keywords": ["ไทย"]}
    )
    assert microphone.status_code == 202
    stress_during_microphone = client.post("/api/v1/stress-tests")
    assert stress_during_microphone.status_code == 409


def test_stress_event_cursor_expiration_reports_oldest_cursor():
    service = FakeWorkflowService()

    def overflowing_stress_workflow(*, clip_path, model_config, progress_callback):
        for index in range(4):
            progress_callback({"event": "trial_progress", "step": index})
        return {"ok": True}

    runtime = BackendRuntime(
        workflow_service=service,
        profile_store=FakeProfileStore(),
        event_limit=3,
        stress_workflow=overflowing_stress_workflow,
    )
    with TestClient(create_app(runtime=runtime)) as client:
        created = client.post("/api/v1/stress-tests")
        stress_id = created.json()["stress_test_id"]
        wait_for_status(client, stress_id, "completed")

        expired = client.get(f"/api/v1/stress-tests/{stress_id}/events?after=0")
        assert expired.status_code == 410
        assert expired.json()["detail"]["oldest_cursor"] == 5
        resumed = client.get(f"/api/v1/stress-tests/{stress_id}/events?after=4")
        assert resumed.status_code == 200
        assert [event["cursor"] for event in resumed.json()["events"]] == [5, 6, 7]
