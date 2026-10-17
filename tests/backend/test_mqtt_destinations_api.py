"""Destination API and MQTT chunk workflows publish through a real test broker."""

import json
import queue
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from speech_to_text.backend.app import create_app
from speech_to_text.backend.dependencies import BackendRuntime
from speech_to_text.backend.profile_store import SQLiteProfileStore
from speech_to_text.workflows.transcribe_match_forward import TranscriptionService
from speech_to_text.workflows.transcribe_match_forward import MqttForwarder, MqttForwarderConfig
from speech_to_text.workflows.transcribe_match_forward import api as workflow_api
from speech_to_text.workflows.transcribe_match_forward import service as service_module
from speech_to_text.workflows.transcribe_match_forward import mqtt_output
from tests.workflows.test_mqtt_output import broker_subscription  # noqa: F401


class _PublishMessage:
    rc = 0

    def wait_for_publish(self, timeout):
        pass

    def is_published(self):
        return True


class _CaptureMqttClient:
    def __init__(self, records, *, connect_error=None):
        self.records = records
        self.connect_error = connect_error

    def connect(self, *_args, **_kwargs):
        if self.connect_error:
            raise self.connect_error
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


def test_destination_save_clears_only_saved_mqtt_topic_when_retain_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEECH_TO_TEXT_DESTINATION_ALLOWED_NETWORKS", "127.0.0.1/32")
    records = []
    monkeypatch.setattr(mqtt_output, "_make_client", lambda *_args: _CaptureMqttClient(records))
    store = SQLiteProfileStore(tmp_path / "profiles.sqlite3")
    runtime = BackendRuntime(workflow_service=TranscriptionService(), profile_store=store)
    with TestClient(create_app(runtime=runtime), base_url="http://127.0.0.1:8000", client=("127.0.0.1", 51000)) as client:
        headers = {"Origin": "http://127.0.0.1:5173"}
        mqtt_body = {"name": "MQTT", "protocol": "mqtt", "endpoint_url": "mqtt://127.0.0.1:1883",
                     "mqtt_topic": "speech/clear-me", "mqtt_qos": 1, "mqtt_retain": False}
        created = client.post("/api/v1/destinations", headers=headers, json=mqtt_body)
        assert created.status_code == 201, created.text
        assert created.json()["retained_cleanup"] == {"status": "cleared", "attempts": 1, "error_code": None}
        assert records == [("speech/clear-me", b"", 1, True)]

        replaced = client.put(f"/api/v1/destinations/{created.json()['destination_id']}", headers=headers,
                              json=mqtt_body)
        assert replaced.status_code == 200, replaced.text
        assert len(records) == 2, "saving an already-disabled retain setting retries cleanup"

        retained = {**mqtt_body, "name": "Retained", "mqtt_topic": "speech/keep", "mqtt_retain": True}
        retained_result = client.post("/api/v1/destinations", headers=headers, json=retained)
        assert retained_result.status_code == 201
        assert "retained_cleanup" not in retained_result.json()
        http_result = client.post("/api/v1/destinations", headers=headers, json={"name": "HTTP", "endpoint_url": "http://127.0.0.1:8123"})
        assert http_result.status_code == 201
        assert "retained_cleanup" not in http_result.json()
        assert len(records) == 2


def test_destination_save_survives_retained_cleanup_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEECH_TO_TEXT_DESTINATION_ALLOWED_NETWORKS", "127.0.0.1/32")
    monkeypatch.setattr(mqtt_output, "_make_client", lambda *_args: _CaptureMqttClient([], connect_error=ConnectionRefusedError()))
    store = SQLiteProfileStore(tmp_path / "profiles.sqlite3")
    runtime = BackendRuntime(workflow_service=TranscriptionService(), profile_store=store)
    with TestClient(create_app(runtime=runtime), base_url="http://127.0.0.1:8000", client=("127.0.0.1", 51000)) as client:
        response = client.post("/api/v1/destinations", headers={"Origin": "http://127.0.0.1:5173"}, json={
            "name": "MQTT", "protocol": "mqtt", "endpoint_url": "mqtt://127.0.0.1:1883",
            "mqtt_topic": "speech/clear-me", "mqtt_qos": 1, "mqtt_retain": False,
        })
        assert response.status_code == 201, response.text
        assert response.json()["retained_cleanup"] == {"status": "failed", "attempts": 3, "error_code": "delivery_failed"}
        saved = client.get(f"/api/v1/destinations/{response.json()['destination_id']}")
        assert saved.status_code == 200 and saved.json()["config"]["mqtt_retain"] is False


def test_real_broker_save_with_retain_off_removes_existing_retained_packet(tmp_path, monkeypatch, broker_subscription):
    import paho.mqtt.client as mqtt

    broker_url, topic, received = broker_subscription
    monkeypatch.setenv("SPEECH_TO_TEXT_DESTINATION_ALLOWED_NETWORKS", "127.0.0.1/32")
    MqttForwarder(MqttForwarderConfig(broker_url, topic, retain=True,
                                     allowed_networks=("127.0.0.1/32",))).forward(
        {"source_id": "seed", "event_id": "seed", "transcript": "retained marker"}
    )
    seeded = received.get(timeout=5)
    assert json.loads(seeded.payload)["transcript"] == "retained marker"

    store = SQLiteProfileStore(tmp_path / "profiles.sqlite3")
    runtime = BackendRuntime(workflow_service=TranscriptionService(), profile_store=store)
    with TestClient(create_app(runtime=runtime), base_url="http://127.0.0.1:8000", client=("127.0.0.1", 51000)) as client:
        response = client.post("/api/v1/destinations", headers={"Origin": "http://127.0.0.1:5173"}, json={
            "name": "MQTT", "protocol": "mqtt", "endpoint_url": broker_url,
            "mqtt_topic": topic, "mqtt_qos": 1, "mqtt_retain": False,
        })
        assert response.status_code == 201, response.text
        assert response.json()["retained_cleanup"]["status"] == "cleared"
    cleared = received.get(timeout=5)
    assert cleared.payload == b""

    parsed = urlsplit(broker_url)
    subscribed = threading.Event()
    fresh_messages = queue.Queue()
    fresh_client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    fresh_client.on_connect = lambda client, *_args: client.subscribe(topic, qos=2)
    fresh_client.on_subscribe = lambda *_args: subscribed.set()
    fresh_client.on_message = lambda _client, _data, message: fresh_messages.put(message)
    fresh_client.connect(parsed.hostname, parsed.port or 1883)
    fresh_client.loop_start()
    try:
        assert subscribed.wait(5)
        with pytest.raises(queue.Empty):
            fresh_messages.get(timeout=0.3)
    finally:
        fresh_client.disconnect()
        fresh_client.loop_stop()


@pytest.mark.parametrize("kind", ["clip", "shared", "per_workflow_process"])
def test_mqtt_destination_test_and_terminal_workflows(tmp_path, monkeypatch, broker_subscription, kind):
    broker_url, topic, received = broker_subscription
    monkeypatch.setenv("SPEECH_TO_TEXT_DESTINATION_ALLOWED_NETWORKS", "127.0.0.1/32")
    closed = []
    monkeypatch.setattr(service_module, "load_model", lambda _: SimpleNamespace(close=lambda: closed.append("model")))
    def transcribe_clip(_clip, _model, _config, *, measurement_sink=None, **_kwargs):
        if measurement_sink is not None:
            measurement_sink({"sequence": 0, "rtf": 0.25})
        return "สวัสดีครับ"

    monkeypatch.setattr(workflow_api, "transcribe_clip", transcribe_clip)
    sessions = {}

    class Session:
        def __init__(self, source_id):
            self.source_id = source_id
            self.result_queue = queue.Queue()
            self.result_queue.put({"type": "measurement", "source_id": source_id, "sequence": 0, "rtf": 0.1})
            text = "สวัสดี สวัสดี" if source_id else ""
            self.result_queue.put({"type": "transcript", "source_id": source_id, "sequence": 0, "text": text})
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
        assert created.json()["retained_cleanup"]["status"] == "cleared"
        assert received.get(timeout=5).payload == b""
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
            assert first_chunk["transcript"] == "สวัสดี สวัสดี"
            assert first_chunk["matches"] == [{"keyword": "สวัสดี", "count": 2}]
            assert first_chunk["rtf"] == 0.1
            sessions[workflow_id].result_queue.put(
                {"type": "measurement", "source_id": workflow_id, "sequence": 1, "rtf": 0.2}
            )
            sessions[workflow_id].result_queue.put(
                {"type": "transcript", "source_id": workflow_id, "sequence": 1, "text": "ลาก่อน"}
            )
            second_chunk = json.loads(received.get(timeout=5).payload)
            assert second_chunk["event_id"] == f"{workflow_id}:chunk:2"
            assert second_chunk["transcript"] == "ลาก่อน"
            assert second_chunk["matches"] == [{"keyword": "สวัสดี", "count": 0}]
            assert second_chunk["rtf"] == 0.2
            deadline = time.monotonic() + 5
            snapshot = client.get(f"/api/v1/transcriptions/{workflow_id}").json()
            while snapshot["matches"][0]["count"] != 2 and time.monotonic() < deadline:
                time.sleep(.01)
                snapshot = client.get(f"/api/v1/transcriptions/{workflow_id}").json()
            assert snapshot["matches"] == [{"keyword": "สวัสดี", "count": 2}]
            assert client.post(f"/api/v1/transcriptions/{workflow_id}/stop").status_code == 202
        if kind == "clip":
            deadline = time.monotonic() + 5
            snapshot = client.get(f"/api/v1/transcriptions/{workflow_id}").json()
            while snapshot["status"] not in {"completed", "failed"} and time.monotonic() < deadline:
                time.sleep(.01)
                snapshot = client.get(f"/api/v1/transcriptions/{workflow_id}").json()
            assert snapshot["status"] == "completed"
            assert snapshot["transcript"] == "สวัสดีครับ"
            assert snapshot["matches"] == [{"keyword": "สวัสดี", "count": 1}]
            assert snapshot["latest_rtf"] == 0.25
            assert snapshot["delivery"]["status"] == "disabled"
            events = client.get(f"/api/v1/transcriptions/{workflow_id}/events?after=0").json()["events"]
            assert not {event["type"] for event in events} & {"forwarded", "forwarding_error"}
            assert received.empty(), "MQTT clip processing must not publish a payload"
        else:
            deadline = time.monotonic() + 5
            snapshot = client.get(f"/api/v1/transcriptions/{workflow_id}").json()
            while snapshot["status"] not in {"completed", "stopped", "failed"} and time.monotonic() < deadline:
                time.sleep(.01)
                snapshot = client.get(f"/api/v1/transcriptions/{workflow_id}").json()
            assert snapshot["transcript"] is None
            assert snapshot["matches"] == [{"keyword": "สวัสดี", "count": 2}]
            assert received.empty(), "microphone stop must not publish an aggregate record"
            transcript_events = [
                event for event in client.get(
                    f"/api/v1/transcriptions/{workflow_id}/events?after=0"
                ).json()["events"] if event["type"] == "transcript"
            ]
            assert len(transcript_events) == 2
            assert all("text" not in event for event in transcript_events)
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
        if kind != "clip":
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
