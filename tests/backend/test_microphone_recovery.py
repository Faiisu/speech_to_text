"""Microphone recovery keeps the workflow run and its forwarding snapshot."""

from __future__ import annotations

import json
import queue
import threading
import time

from fastapi.testclient import TestClient
import pytest

from speech_to_text.backend.app import create_app
from speech_to_text.backend.dependencies import BackendRuntime
from speech_to_text.backend.profile_store import SQLiteProfileStore
from speech_to_text.workflows.transcribe_match_forward import TranscriptionService
from speech_to_text.workflows.transcribe_match_forward import api as workflow_api
from speech_to_text.workflows.transcribe_match_forward import mqtt_output
from speech_to_text.workflows.transcribe_match_forward import service as service_module


class _PublishMessage:
    rc = 0

    def wait_for_publish(self, timeout):
        pass

    def is_published(self):
        return True


class _CaptureMqttClient:
    def __init__(self, records):
        self.records = records

    def connect(self, *_args, **_kwargs):
        self.on_connect(self, None, None, 0, None)

    def loop_start(self):
        pass

    def publish(self, topic, payload, qos, retain):
        self.records.append((topic, payload, qos, retain))
        return _PublishMessage()

    def disconnect(self):
        pass

    def loop_stop(self):
        pass


class _Model:
    def close(self):
        pass


class _Session:
    def __init__(self, source_id, *, failed=False):
        self.source_id = source_id
        self.result_queue = queue.Queue()
        self.stopped = threading.Event()
        if failed:
            self.result_queue.put(
                {
                    "type": "transcript",
                    "source_id": source_id,
                    "sequence": 1,
                    "text": "สวัสดี",
                }
            )
            self.result_queue.put(
                {
                    "type": "workflow_error",
                    "source_id": source_id,
                    "error": "USB capture disconnected",
                }
            )
            self.result_queue.put(
                {"type": "completed", "source_id": source_id, "status": "failed"}
            )
        else:
            self.result_queue.put(
                {
                    "type": "transcript",
                    "source_id": source_id,
                    "sequence": 1,
                    "text": "สวัสดี",
                }
            )

    def stop(self, timeout=30):
        if not self.stopped.is_set():
            self.stopped.set()
            self.result_queue.put(
                {"type": "completed", "source_id": self.source_id, "status": "stopped"}
            )


class _RecoveryWorkflow:
    def __init__(
        self,
        source_id,
        *,
        active=False,
        initial_termination="failed",
        stop_error=None,
        stop_completion_status="stopped",
        wait_always_timeout=False,
        measurement_rtf=None,
    ):
        self.output_queue = queue.Queue()
        self.stopped = threading.Event()
        self.stop_calls = 0
        self.stop_error = stop_error
        self.stop_completion_status = stop_completion_status
        self.wait_always_timeout = wait_always_timeout
        if active:
            self.active = True
        else:
            self.active = False
            if measurement_rtf is not None:
                self.output_queue.put({"type": "measurement", "rtf": measurement_rtf})
            if initial_termination == "failed":
                self.output_queue.put(
                    {
                        "type": "error",
                        "source_id": source_id,
                        "code": "CAPTURE_FAILED",
                        "message": "USB capture disconnected",
                        "fatal": True,
                    }
                )
            if initial_termination != "missing":
                self.output_queue.put(
                    {"type": "completed", "source_id": source_id, "status": initial_termination}
                )
        if active and measurement_rtf is not None:
            self.output_queue.put({"type": "measurement", "rtf": measurement_rtf})

    def wait(self, timeout=0):
        if self.active and (self.wait_always_timeout or not self.stopped.is_set()):
            raise TimeoutError

    def stop(self, timeout=30):
        self.stop_calls += 1
        if self.stop_error is not None:
            raise RuntimeError(self.stop_error)
        if not self.stopped.is_set():
            self.stopped.set()
            self.output_queue.put(
                {"type": "completed", "status": self.stop_completion_status}
            )


class _RecoveryService:
    default_model = "turbo"
    default_runtime = "openvino-cpu"

    def __init__(
        self,
        *,
        failed_restarts=0,
        block_restart=False,
        initial_termination="failed",
        initial_active=False,
        initial_stop_error=None,
        initial_wait_always_timeout=False,
        restart_stop_error=None,
        restart_stop_status="stopped",
        restart_wait_always_timeout=False,
        measurement_values=None,
    ):
        self.failed_restarts = failed_restarts
        self.block_restart = block_restart
        self.initial_termination = initial_termination
        self.initial_active = initial_active
        self.initial_stop_error = initial_stop_error
        self.initial_wait_always_timeout = initial_wait_always_timeout
        self.restart_stop_error = restart_stop_error
        self.restart_stop_status = restart_stop_status
        self.restart_wait_always_timeout = restart_wait_always_timeout
        self.measurement_values = measurement_values or ()
        self.restart_entered = threading.Event()
        self.release_restart = threading.Event()
        self.starts = []
        self.options = []
        self.workflows = []
        self.closed = False

    def validate_keywords(self, keywords):
        return tuple(keywords)

    def validate_model_runtime(self, model, runtime):
        if model != self.default_model or runtime != self.default_runtime:
            raise ValueError("unexpected model configuration")

    def start_microphone(self, device, keywords, flow_config, **_options):
        self.starts.append(dict(flow_config))
        self.options.append(dict(_options))
        if len(self.starts) == 1:
            workflow = _RecoveryWorkflow(
                flow_config["source_id"],
                active=self.initial_active,
                initial_termination=self.initial_termination,
                stop_error=self.initial_stop_error,
                wait_always_timeout=self.initial_wait_always_timeout,
                measurement_rtf=self.measurement_values[0] if self.measurement_values else None,
            )
            self.workflows.append(workflow)
            return workflow
        self.restart_entered.set()
        if self.block_restart:
            self.release_restart.wait(timeout=5)
        if len(self.starts) <= self.failed_restarts + 1:
            raise RuntimeError(f"restart failure {len(self.starts) - 1}")
        workflow = _RecoveryWorkflow(
            flow_config["source_id"],
            active=True,
            stop_error=self.restart_stop_error,
            stop_completion_status=self.restart_stop_status,
            wait_always_timeout=self.restart_wait_always_timeout,
            measurement_rtf=self.measurement_values[len(self.starts) - 1]
            if len(self.measurement_values) >= len(self.starts)
            else None,
        )
        self.workflows.append(workflow)
        return workflow

    def close(self):
        self.closed = True


def _run_api(service, *, initial_delay=0.01, max_delay=0.04, profile_store=None):
    runtime = BackendRuntime(
        workflow_service=service,
        profile_store=profile_store,
        recovery_initial_delay=initial_delay,
        recovery_max_delay=max_delay,
    )
    return runtime, TestClient(create_app(runtime=runtime))


def _wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("condition did not become true before timeout")


def _api_events(client, workflow_id):
    return client.get(
        f"/api/v1/transcriptions/{workflow_id}/events?after=0"
    ).json()["events"]


def _wait_for_status(client, workflow_id, status):
    return _wait_until(
        lambda: (
            value
            if (value := client.get(f"/api/v1/transcriptions/{workflow_id}").json())["status"]
            == status
            else None
        )
    )


@pytest.mark.parametrize("execution_mode", ["shared", "per_workflow_process"])
def test_recovery_retries_with_capped_backoff_and_clears_stale_error(execution_mode):
    service = _RecoveryService(failed_restarts=2)
    runtime, client = _run_api(service, initial_delay=0.01, max_delay=0.04)
    with client:
        started = client.post(
            "/api/v1/transcriptions/microphones",
            json={"keywords": ["ไทย"], "execution_mode": execution_mode, "language": "auto"},
        )
        workflow_id = started.json()["workflow_id"]
        _wait_until(lambda: any(event["type"] == "recovered" for event in _api_events(client, workflow_id)))
        events = _api_events(client, workflow_id)
        recovering = [event for event in events if event["type"] == "recovering"]
        failures = [event for event in events if event["type"] == "recovery_failed"]
        assert [event["attempt"] for event in recovering] == [1, 2, 3]
        assert [event["delay_seconds"] for event in recovering] == [0.01, 0.02, 0.04]
        assert [event["attempt"] for event in failures] == [1, 2]
        assert [event["delay_seconds"] for event in failures] == [0.02, 0.04]
        assert all(option["execution_mode"] == execution_mode for option in service.options)
        assert all(option["runtime"] == "openvino-cpu" for option in service.options)
        assert all(start["language"] == "auto" for start in service.starts)
        status = client.get(f"/api/v1/transcriptions/{workflow_id}").json()
        assert status["status"] == "recording"
        assert status["language"] == "auto"
        assert status["error"] is None
        stop = client.post(f"/api/v1/transcriptions/{workflow_id}/stop")
        assert stop.status_code == 202
        assert _wait_for_status(client, workflow_id, "stopped")["status"] == "stopped"
    assert runtime.shutdown_event.is_set()


@pytest.mark.parametrize("termination", ["stopped", "completed", "missing"])
def test_unexpected_natural_termination_recovers_without_terminal_event(termination):
    service = _RecoveryService(initial_termination=termination)
    _runtime, client = _run_api(service, initial_delay=0.01, max_delay=0.01)
    with client:
        started = client.post(
            "/api/v1/transcriptions/microphones", json={"keywords": ["ไทย"]}
        )
        workflow_id = started.json()["workflow_id"]
        _wait_until(lambda: any(event["type"] == "recovered" for event in _api_events(client, workflow_id)))
        assert client.get(f"/api/v1/transcriptions/{workflow_id}").json()["status"] == "recording"
        assert not any(event["type"] == "completed" for event in _api_events(client, workflow_id))
        assert client.post(f"/api/v1/transcriptions/{workflow_id}/stop").status_code == 202
        assert _wait_for_status(client, workflow_id, "stopped")["status"] == "stopped"


def test_rtf_aggregate_continues_across_live_source_recovery():
    service = _RecoveryService(measurement_values=(0.2, 0.8))
    _runtime, client = _run_api(service, initial_delay=0.01, max_delay=0.01)
    with client:
        workflow_id = client.post(
            "/api/v1/transcriptions/microphones", json={"keywords": ["ไทย"]}
        ).json()["workflow_id"]
        _wait_until(lambda: any(event["type"] == "recovered" for event in _api_events(client, workflow_id)))
        snapshot = client.get(f"/api/v1/transcriptions/{workflow_id}").json()
        assert snapshot["rtf_min"] == 0.2
        assert snapshot["rtf_max"] == 0.8
        assert snapshot["rtf_avg"] == pytest.approx(0.5)
        assert snapshot["rtf_count"] == 2
        client.post(f"/api/v1/transcriptions/{workflow_id}/stop")
        final = _wait_for_status(client, workflow_id, "stopped")
        assert final["rtf_count"] == 2


def test_stop_during_backoff_is_idempotent_and_prevents_restart():
    service = _RecoveryService()
    _runtime, client = _run_api(service, initial_delay=0.5, max_delay=0.5)
    with client:
        started = client.post(
            "/api/v1/transcriptions/microphones", json={"keywords": ["ไทย"]}
        )
        workflow_id = started.json()["workflow_id"]
        _wait_until(lambda: any(event["type"] == "recovering" for event in _api_events(client, workflow_id)))
        assert client.post(f"/api/v1/transcriptions/{workflow_id}/stop").status_code == 202
        assert client.post(f"/api/v1/transcriptions/{workflow_id}/stop").status_code == 202
        assert _wait_for_status(client, workflow_id, "stopped")["status"] == "stopped"
        time.sleep(0.55)
        assert len(service.starts) == 1


@pytest.mark.parametrize("execution_mode", ["shared", "per_workflow_process"])
def test_profile_run_remains_deduplicated_during_recovery(tmp_path, execution_mode):
    service = _RecoveryService()
    store = SQLiteProfileStore(tmp_path / "profiles.sqlite3")
    _runtime, client = _run_api(
        service,
        initial_delay=0.3,
        max_delay=0.3,
        profile_store=store,
    )
    with client:
        profile = client.post(
            "/api/v1/profiles",
            json={
                "name": "Studio",
                "device": "Studio Mic",
                "execution_mode": execution_mode,
                "model": "turbo",
                "runtime": "openvino-cpu",
                "keywords": ["ไทย"],
                "silence_threshold": 0,
                "decoding_options": {"beam_size": 2},
                "vad": {"enabled": True, "threshold": 0.31},
            },
        )
        assert profile.status_code == 201, profile.text
        run_url = f"/api/v1/profiles/{profile.json()['profile_id']}/runs"
        first = client.post(run_url)
        workflow_id = first.json()["workflow_id"]
        _wait_until(lambda: any(event["type"] == "recovering" for event in _api_events(client, workflow_id)))
        edited = client.put(
            f"/api/v1/profiles/{profile.json()['profile_id']}",
            json={
                "name": "Studio",
                "device": "Studio Mic",
                "execution_mode": "per_workflow_process" if execution_mode == "shared" else "shared",
                "model": "turbo",
                "runtime": "openvino-cpu",
                "keywords": ["ไทย"],
                "silence_threshold": 0,
                "decoding_options": {"beam_size": 8},
                "vad": {"enabled": False, "threshold": 0.77},
            },
        )
        assert edited.status_code == 200
        _wait_until(lambda: len(service.starts) == 2)
        assert service.starts[0]["decoding_options"]["beam_size"] == 2
        assert service.starts[1]["decoding_options"]["beam_size"] == 2
        assert service.starts[0]["vad"] == {
            "enabled": True, "threshold": 0.31, "min_speech_duration_ms": 250
        }
        assert service.starts[1]["vad"] == service.starts[0]["vad"]
        assert service.options[0]["execution_mode"] == execution_mode
        assert service.options[1]["execution_mode"] == execution_mode
        assert client.get(f"/api/v1/transcriptions/{workflow_id}").json()[
            "decoding_options"
        ]["beam_size"] == 2
        assert client.get(f"/api/v1/transcriptions/{workflow_id}").json()["vad"] == service.starts[0]["vad"]
        repeated = client.post(run_url)
        assert repeated.json()["workflow_id"] == workflow_id
        assert len(service.starts) == 2
        assert client.post(f"/api/v1/transcriptions/{workflow_id}/stop").status_code == 202
        assert _wait_for_status(client, workflow_id, "stopped")["status"] == "stopped"


def test_stop_during_restart_stops_the_newly_created_workflow():
    service = _RecoveryService(block_restart=True)
    _runtime, client = _run_api(service, initial_delay=0.01, max_delay=0.01)
    with client:
        started = client.post(
            "/api/v1/transcriptions/microphones", json={"keywords": ["ไทย"]}
        )
        workflow_id = started.json()["workflow_id"]
        assert service.restart_entered.wait(timeout=2)
        assert client.post(f"/api/v1/transcriptions/{workflow_id}/stop").status_code == 202
        service.release_restart.set()
        assert _wait_for_status(client, workflow_id, "stopped")["status"] == "stopped"
        assert service.workflows[-1].stop_calls == 1
        assert len(service.starts) == 2


def test_failed_stop_of_replacement_finishes_controller_and_preserves_failure():
    service = _RecoveryService(
        block_restart=True,
        restart_stop_error="replacement refused to stop",
        restart_wait_always_timeout=True,
    )
    runtime, client = _run_api(service, initial_delay=0.01, max_delay=0.01)
    with client:
        started = client.post(
            "/api/v1/transcriptions/microphones", json={"keywords": ["ไทย"]}
        )
        workflow_id = started.json()["workflow_id"]
        assert service.restart_entered.wait(timeout=2)
        assert client.post(f"/api/v1/transcriptions/{workflow_id}/stop").status_code == 202
        service.release_restart.set()
        failed = _wait_for_status(client, workflow_id, "failed")
        assert failed["error"] == "replacement refused to stop"
        events = _api_events(client, workflow_id)
        assert any(
            event.get("error") == "replacement refused to stop"
            for event in events
            if event["type"] == "workflow_error"
        )
        assert any(
            event["type"] == "completed" and event["status"] == "failed"
            for event in events
        )
        runtime.shutdown()
        assert service.closed
        assert len(service.starts) == 2


def test_failed_completion_during_stop_is_not_reported_as_stopped():
    service = _RecoveryService(block_restart=True, restart_stop_status="failed")
    _runtime, client = _run_api(service, initial_delay=0.01, max_delay=0.01)
    with client:
        started = client.post(
            "/api/v1/transcriptions/microphones", json={"keywords": ["ไทย"]}
        )
        workflow_id = started.json()["workflow_id"]
        assert service.restart_entered.wait(timeout=2)
        assert client.post(f"/api/v1/transcriptions/{workflow_id}/stop").status_code == 202
        service.release_restart.set()
        assert _wait_for_status(client, workflow_id, "failed")["status"] == "failed"


def test_shutdown_cancels_a_pending_recovery_before_service_close():
    service = _RecoveryService()
    runtime, client = _run_api(service, initial_delay=5, max_delay=5)
    with client:
        started = client.post(
            "/api/v1/transcriptions/microphones", json={"keywords": ["ไทย"]}
        )
        workflow_id = started.json()["workflow_id"]
        _wait_until(lambda: any(event["type"] == "recovering" for event in _api_events(client, workflow_id)))
        runtime.shutdown()
        assert len(service.starts) == 1
        assert service.closed


def test_shutdown_stop_failure_terminates_controller_without_waiting_forever():
    service = _RecoveryService(
        initial_active=True,
        initial_stop_error="capture process refused shutdown",
        initial_wait_always_timeout=True,
    )
    runtime, client = _run_api(service, initial_delay=5, max_delay=5)
    with client:
        started = client.post(
            "/api/v1/transcriptions/microphones", json={"keywords": ["ไทย"]}
        )
        workflow_id = started.json()["workflow_id"]
        _wait_for_status(client, workflow_id, "recording")
        runtime.shutdown()
        failed = client.get(f"/api/v1/transcriptions/{workflow_id}").json()
        assert failed["status"] == "failed"
        assert failed["error"] == "capture process refused shutdown"
        assert service.closed


def test_recovery_reuses_mqtt_snapshot_and_unique_chunk_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEECH_TO_TEXT_DESTINATION_ALLOWED_NETWORKS", "127.0.0.0/8")
    published = []
    monkeypatch.setattr(mqtt_output, "_make_client", lambda *_args: _CaptureMqttClient(published))

    sessions = []
    starts = []

    def start_with_test_capture(_device, _model, forwarder, matching, flow_config, *, on_finished=None):
        starts.append(dict(flow_config))
        session = _Session(
            flow_config["source_id"], failed=len(sessions) == 0
        )
        sessions.append(session)
        return workflow_api.forward_session(
            session, forwarder, matching, on_finished=on_finished
        )

    monkeypatch.setattr(service_module, "load_model", lambda _config: _Model())
    monkeypatch.setattr(service_module, "start_microphone_and_forward", start_with_test_capture)
    service = TranscriptionService(model_config={"model": "turbo", "runtime": "openvino-cpu"})
    runtime = BackendRuntime(
        workflow_service=service,
        profile_store=SQLiteProfileStore(tmp_path / "profiles.sqlite3"),
        recovery_initial_delay=0.01,
        recovery_max_delay=0.04,
    )

    with TestClient(create_app(runtime=runtime), base_url="http://127.0.0.1:8000", client=("127.0.0.1", 51000)) as client:
        headers = {"Origin": "http://127.0.0.1:5173"}
        created = client.post(
            "/api/v1/destinations",
            headers=headers,
            json={
                "name": "Original MQTT",
                "protocol": "mqtt",
                "endpoint_url": "mqtt://127.0.0.1:1883",
                "mqtt_topic": "speech/original",
                "mqtt_qos": 1,
                "mqtt_retain": False,
            },
        )
        assert created.status_code == 201, created.text
        destination_id = created.json()["destination_id"]
        started = client.post(
            "/api/v1/transcriptions/microphones",
            json={"keywords": ["สวัสดี"], "device": "Studio Mic", "destination_id": destination_id, "language": "auto"},
        )
        assert started.status_code == 202, started.text
        workflow_id = started.json()["workflow_id"]

        def events():
            return client.get(f"/api/v1/transcriptions/{workflow_id}/events?after=0").json()["events"]

        _wait_until(lambda: any(e["type"] == "recovering" for e in events()))
        initial_events = events()
        assert client.get(f"/api/v1/transcriptions/{workflow_id}").json()["status"] == "recording"
        assert not any(event["type"] == "completed" for event in initial_events)
        assert any(
            event.get("error") == "USB capture disconnected"
            for event in initial_events
            if event["type"] == "workflow_error"
        )

        changed = client.put(
            f"/api/v1/destinations/{destination_id}",
            headers=headers,
            json={
                "name": "Updated MQTT",
                "protocol": "mqtt",
                "endpoint_url": "mqtt://127.0.0.1:1883",
                "mqtt_topic": "speech/updated",
                "mqtt_qos": 1,
                "mqtt_retain": False,
            },
        )
        assert changed.status_code == 200, changed.text

        chunks = _wait_until(
            lambda: (items if len(items := [item for item in published if item[1]]) >= 2 else None)
        )
        assert len(starts) == 2
        assert starts[1]["source_id"] == f"{workflow_id}:restart:1"
        assert starts[1]["overload_policy"] == "drop"
        assert starts[0]["language"] == starts[1]["language"] == "auto"
        assert client.get(f"/api/v1/transcriptions/{workflow_id}").json()["language"] == "auto"
        assert {item[0] for item in chunks} == {"speech/original"}
        messages = [json.loads(item[1]) for item in chunks]
        assert {message["source_id"] for message in messages} == {
            workflow_id,
            f"{workflow_id}:restart:1",
        }
        event_ids = {message["event_id"] for message in messages}
        assert event_ids == {
            f"{workflow_id}:chunk:1",
            f"{workflow_id}:restart:1:chunk:1",
        }
        assert any(event["type"] == "recovered" for event in events())
        transcript_events = [event for event in events() if event["type"] == "transcript"]
        assert [(event["source_id"], event["sequence"], event["text"]) for event in transcript_events] == [
            (workflow_id, 1, "สวัสดี"),
            (f"{workflow_id}:restart:1", 1, "สวัสดี"),
        ]
        recovered = client.get(f"/api/v1/transcriptions/{workflow_id}").json()
        assert recovered["status"] == "recording"
        assert recovered["error"] is None

        stop = client.post(f"/api/v1/transcriptions/{workflow_id}/stop")
        assert stop.status_code == 202
        final = _wait_until(
            lambda: (
                status
                if (status := client.get(f"/api/v1/transcriptions/{workflow_id}").json())["status"]
                in {"stopped", "failed"}
                else None
            )
        )
        assert final["status"] == "stopped"
    assert len([item for item in published if item[1]]) == 2

