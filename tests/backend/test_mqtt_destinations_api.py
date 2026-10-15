"""Destination API and chunk/terminal workflows publish through a real test broker."""

import json
import queue
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from speech_to_text.backend.app import create_app
from speech_to_text.backend.dependencies import BackendRuntime
from speech_to_text.backend.profile_store import SQLiteProfileStore
from speech_to_text.workflows.transcribe_match_forward import TranscriptionService
from speech_to_text.workflows.transcribe_match_forward import api as workflow_api
from speech_to_text.workflows.transcribe_match_forward import service as service_module
from tests.workflows.test_mqtt_output import broker_subscription  # noqa: F401


@pytest.mark.parametrize("kind", ["clip", "shared", "per_workflow_process"])
def test_mqtt_destination_test_and_terminal_workflows(tmp_path, monkeypatch, broker_subscription, kind):
    broker_url, topic, received = broker_subscription
    monkeypatch.setenv("SPEECH_TO_TEXT_DESTINATION_ALLOWED_NETWORKS", "127.0.0.1/32")
    closed = []
    monkeypatch.setattr(service_module, "load_model", lambda _: SimpleNamespace(close=lambda: closed.append("model")))
    monkeypatch.setattr(workflow_api, "transcribe_clip", lambda *_args, **_kwargs: "สวัสดีครับ")
    sessions = {}

    class Session:
        def __init__(self, source_id):
            self.source_id = source_id
            self.result_queue = queue.Queue()
            self.result_queue.put({"type": "measurement", "source_id": source_id, "sequence": 0, "rtf": 0.1})
            self.result_queue.put({"type": "transcript", "source_id": source_id, "sequence": 0, "text": "สวัสดีครับ"})
            self.stopped = False

        def stop(self, timeout=30):
            if not self.stopped:
                self.stopped = True
                self.result_queue.put({"type": "completed", "source_id": self.source_id, "status": "stopped"})

    monkeypatch.setattr(workflow_api, "start_microphone_flow",
                        lambda _device, _model, config: sessions.setdefault(config["source_id"], Session(config["source_id"])))
    monkeypatch.setattr(service_module, "start_multiprocess_microphone_flows",
                        lambda **kwargs: SimpleNamespace(sessions=[sessions.setdefault(kwargs["flow_configs"][0]["source_id"], Session(kwargs["flow_configs"][0]["source_id"]))],
                                    stop=lambda: closed.append("group"), abort=lambda: closed.append("aborted")))
    store = SQLiteProfileStore(tmp_path / "profiles.sqlite3")
    runtime = BackendRuntime(workflow_service=TranscriptionService(), profile_store=store)
    with TestClient(create_app(runtime=runtime), base_url="http://127.0.0.1:8000", client=("127.0.0.1", 51000)) as client:
        headers = {"Origin": "http://127.0.0.1:5173"}
        created = client.post("/api/v1/destinations", headers=headers, json={
            "name": "MQTT results", "protocol": "mqtt", "endpoint_url": broker_url,
            "mqtt_topic": topic, "mqtt_qos": 1, "mqtt_retain": False,
        })
        assert created.status_code == 201, created.text
        destination_id = created.json()["destination_id"]
        tested = client.post(f"/api/v1/destinations/{destination_id}/test", headers=headers)
        assert tested.status_code == 200, tested.text
        assert tested.json()["status"] == "delivered"
        assert tested.json()["transport"] == "mqtt" and tested.json()["http_status"] is None
        test_payload = json.loads(received.get(timeout=5).payload)
        assert test_payload["event_id"] == tested.json()["event_id"]
        assert test_payload["rtf"] is None
        if kind == "clip":
            started = client.post("/api/v1/transcriptions/clips", data={"keywords": "สวัสดี", "destination_id": destination_id},
                                  files={"file": ("sample.wav", b"audio boundary", "audio/wav")})
        else:
            profile = store.create_profile({"profile_id": "profile", "name": "MQTT profile", "device": None,
                        "execution_mode": kind, "keywords": ["สวัสดี"], "silence_threshold": 0,
                        "model": "turbo", "runtime": "openvino-gpu", "destination_id": destination_id})
            started = client.post(f"/api/v1/profiles/{profile['profile_id']}/runs")
        assert started.status_code == 202, started.text
        workflow_id = started.json()["workflow_id"]
        if kind != "clip":
            first_chunk = json.loads(received.get(timeout=5).payload)
            assert first_chunk["event_id"] == f"{workflow_id}:chunk:1"
            assert first_chunk["source_id"] == workflow_id
            assert first_chunk["matches"] == [{"keyword": "สวัสดี", "count": 1}]
            assert first_chunk["rtf"] == 0.1
            sessions[workflow_id].result_queue.put(
                {"type": "measurement", "source_id": workflow_id, "sequence": 1, "rtf": 0.2}
            )
            sessions[workflow_id].result_queue.put(
                {"type": "transcript", "source_id": workflow_id, "sequence": 1, "text": " สวัสดีครับ"}
            )
            second_chunk = json.loads(received.get(timeout=5).payload)
            assert second_chunk["event_id"] == f"{workflow_id}:chunk:2"
            assert second_chunk["matches"] == [{"keyword": "สวัสดี", "count": 2}]
            assert second_chunk["rtf"] == 0.2
            deadline = time.monotonic() + 5
            snapshot = client.get(f"/api/v1/transcriptions/{workflow_id}").json()
            while snapshot["matches"][0]["count"] != 2 and time.monotonic() < deadline:
                time.sleep(.01)
                snapshot = client.get(f"/api/v1/transcriptions/{workflow_id}").json()
            assert snapshot["matches"] == [{"keyword": "สวัสดี", "count": 2}]
            assert client.post(f"/api/v1/transcriptions/{workflow_id}/stop").status_code == 202
        record = json.loads(received.get(timeout=5).payload)
        assert record["event_id"] == record["source_id"] == workflow_id
        assert record["transcript"] == ("สวัสดีครับ" if kind == "clip" else "สวัสดีครับ  สวัสดีครับ")
        assert record["matches"] == [{"keyword": "สวัสดี", "count": 2 if kind != "clip" else 1}]
        assert record["rtf"] == (None if kind == "clip" else 0.2)
        cleanup_marker = "group" if kind == "per_workflow_process" else "model"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            snapshot = client.get(f"/api/v1/transcriptions/{workflow_id}").json()
            if (
                snapshot["status"] in {"completed", "stopped", "failed"}
                and cleanup_marker in closed
            ):
                break
            time.sleep(.01)
        assert snapshot["delivery"]["status"] == "delivered"
        assert snapshot["delivery"]["transport"] == "mqtt" and snapshot["delivery"]["mqtt_topic"] == topic
        assert snapshot["delivery"]["http_status"] is None
        assert snapshot["status"] in {"completed", "stopped"}
        assert cleanup_marker in closed
        assert client.get("/api/v1/destinations").json()["destinations"][0]["protocol"] == "mqtt"


def test_old_http_definitions_load_without_migration(tmp_path):
    store = SQLiteProfileStore(tmp_path / "old.sqlite3")
    store.save_destination("legacy", "Existing HTTP", {"endpoint_url": "https://receiver.example.com/results"})
    runtime = BackendRuntime(profile_store=store)
    assert runtime.destination_registry.get_config("legacy").protocol == "http"
    runtime.shutdown()
