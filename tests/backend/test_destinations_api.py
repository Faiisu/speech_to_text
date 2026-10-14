from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import queue
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from speech_to_text.backend.app import create_app
from speech_to_text.backend.dependencies import BackendRuntime
from speech_to_text.backend.profile_store import SQLiteProfileStore
from speech_to_text.workflows.transcribe_match_forward import TranscriptionService
from speech_to_text.workflows.transcribe_match_forward import api as transcribe_api
from speech_to_text.workflows.transcribe_match_forward import service as transcribe_service


class _Receiver(BaseHTTPRequestHandler):
    records = []

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        self.records.append(
            (
                self.path,
                self.headers.get("Idempotency-Key"),
                json.loads(self.rfile.read(size)),
            )
        )
        self.send_response(400 if self.path == "/fail" else 204)
        self.end_headers()

    def log_message(self, *_args):
        pass


def _receiver(*, reset=True):
    if reset:
        _Receiver.records = []
    server = HTTPServer(("127.0.0.1", 0), _Receiver)
    server.timeout = 0.1
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.mark.parametrize(
    "peer",
    [
        ("192.168.1.25", 51234),  # LAN client
        ("172.20.0.2", 51234),  # Docker bridge peer
    ],
    ids=("lan-client", "docker-bridge-peer"),
)
def test_destination_management_accepts_remote_peers_and_arbitrary_headers(
    tmp_path, monkeypatch, peer
):
    server, _thread = _receiver()
    monkeypatch.setenv("SPEECH_TO_TEXT_DESTINATION_ALLOWED_NETWORKS", "127.0.0.0/8")
    store = SQLiteProfileStore(tmp_path / "profiles.sqlite3")
    runtime = BackendRuntime(profile_store=store)
    client = TestClient(
        create_app(runtime=runtime), base_url="http://127.0.0.1:8000", client=peer
    )
    try:
        with client:
            created = client.post(
                "/api/v1/destinations",
                json={
                    "name": "Local receiver",
                    "endpoint_url": f"http://127.0.0.1:{server.server_port}/records",
                },
            )
            assert created.status_code == 201, created.text
            destination_id = created.json()["destination_id"]
            assert "config" in created.json()

            result = client.post(
                f"/api/v1/destinations/{destination_id}/test",
                headers={
                    "Host": "receiver.example:1234",
                    "Origin": "https://untrusted.example",
                },
            )
            assert result.status_code == 200, result.text
            assert result.json()["status"] == "delivered"
            _path, key, record = _Receiver.records[0]
            assert key == record["event_id"] == result.json()["event_id"]
            assert record["transcript"] == "Synthetic destination test"
            assert record["language"] == "th"

            public_list = client.get("/api/v1/destinations").json()["destinations"]
            assert public_list[0]["destination_id"] == destination_id
            assert "config" not in public_list[0]
            assert client.get(
                f"/api/v1/destinations/{destination_id}",
                headers={"Host": "arbitrary.invalid", "Origin": "null"},
            ).status_code == 200
            replaced = client.put(
                f"/api/v1/destinations/{destination_id}",
                json={
                    "name": "Updated local receiver",
                    "endpoint_url": f"http://127.0.0.1:{server.server_port}/updated",
                },
            )
            assert replaced.status_code == 200
            assert replaced.json()["destination_id"] == destination_id
            assert replaced.json()["name"] == "Updated local receiver"
            deleted = client.delete(
                f"/api/v1/destinations/{destination_id}",
                headers={
                    "Origin": "https://untrusted.example/path?query=yes",
                    "Host": "attacker.example:8000",
                },
            )
            assert deleted.status_code == 204
            assert client.get("/api/v1/destinations").json() == {"destinations": []}
    finally:
        server.shutdown()
        server.server_close()


def test_destination_delete_is_rejected_while_profile_references_it(tmp_path):
    store = SQLiteProfileStore(tmp_path / "profiles.sqlite3")
    store.save_destination(
        "destination-1",
        "Local receiver",
        {"endpoint_url": "http://127.0.0.1:9000/records"},
    )
    store.create_profile(
        {
            "profile_id": "profile-1",
            "name": "Profile",
            "device": None,
            "execution_mode": "shared",
            "keywords": ["สวัสดี"],
            "silence_threshold": 0,
            "model": "turbo",
            "runtime": "openvino-gpu",
            "destination_id": "destination-1",
        }
    )
    assert store.get_profile("profile-1")["destination_id"] == "destination-1"
    assert store.destination_is_referenced("destination-1")
    assert not store.delete_destination("destination-1")
    runtime = BackendRuntime(profile_store=store)
    client = TestClient(
        create_app(runtime=runtime),
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 51234),
    )
    with client:
        response = client.delete(
            "/api/v1/destinations/destination-1",
        )
        assert response.status_code == 409
    reopened = SQLiteProfileStore(tmp_path / "profiles.sqlite3")
    assert reopened.get_profile("profile-1")["destination_id"] == "destination-1"
    assert reopened.get_destination("destination-1")["config"]["endpoint_url"] == (
        "http://127.0.0.1:9000/records"
    )
    reopened.close()


def test_clip_worker_forwards_terminal_record_to_saved_destination(
    tmp_path, monkeypatch
):
    from speech_to_text.workflows.transcribe_match_forward import api as workflow_api
    from speech_to_text.workflows.transcribe_match_forward import service as workflow_service

    server, _thread = _receiver()
    monkeypatch.setenv("SPEECH_TO_TEXT_DESTINATION_ALLOWED_NETWORKS", "127.0.0.0/8")

    class _Model:
        def close(self):
            pass

    monkeypatch.setattr(workflow_service, "load_model", lambda _config: _Model())
    monkeypatch.setattr(workflow_api, "transcribe_clip", lambda *_args, **_kwargs: "สวัสดี")
    runtime = BackendRuntime(
        workflow_service=TranscriptionService(model_config={"model": "turbo"}),
        profile_store=SQLiteProfileStore(tmp_path / "profiles.sqlite3"),
    )
    client = TestClient(
        create_app(runtime=runtime),
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 51234),
    )
    operator_headers = {"Origin": "http://127.0.0.1:5173"}
    try:
        with client:
            created = client.post(
                "/api/v1/destinations",
                headers=operator_headers,
                json={
                    "name": "Clip receiver",
                    "endpoint_url": f"http://127.0.0.1:{server.server_port}/records",
                },
            )
            assert created.status_code == 201, created.text
            destination_id = created.json()["destination_id"]
            accepted = client.post(
                "/api/v1/transcriptions/clips",
                data={"keywords": "สวัสดี", "destination_id": destination_id},
                files={"file": ("sample.wav", b"fake wav", "audio/wav")},
            )
            assert accepted.status_code == 202, accepted.text
            workflow_id = accepted.json()["workflow_id"]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                snapshot = client.get(
                    f"/api/v1/transcriptions/{workflow_id}"
                ).json()
                if snapshot["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.02)
            assert snapshot["status"] == "completed"
            assert snapshot["transcript"] == "สวัสดี"
            assert snapshot["delivery"]["status"] == "delivered"
            assert snapshot["delivery"]["destination_id"] == destination_id
            _path, _key, delivered = _Receiver.records[0]
            assert delivered["transcript"] == "สวัสดี"
            assert delivered["matches"] == [{"keyword": "สวัสดี", "count": 1}]
            changed = client.put(
                f"/api/v1/destinations/{destination_id}",
                headers=operator_headers,
                json={
                    "name": "Clip receiver",
                    "endpoint_url": f"http://127.0.0.1:{server.server_port}/fail",
                },
            )
            assert changed.status_code == 200, changed.text
            rejected = client.post(
                "/api/v1/transcriptions/clips",
                data={"keywords": "สวัสดี", "destination_id": destination_id},
                files={"file": ("sample.wav", b"fake wav", "audio/wav")},
            )
            failed_id = rejected.json()["workflow_id"]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                failed_snapshot = client.get(
                    f"/api/v1/transcriptions/{failed_id}"
                ).json()
                if failed_snapshot["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.02)
            assert failed_snapshot["status"] == "completed"
            assert failed_snapshot["transcript"] == "สวัสดี"
            assert failed_snapshot["matches"] == [{"keyword": "สวัสดี", "count": 1}]
            assert failed_snapshot["delivery"]["status"] == "failed"
            assert failed_snapshot["delivery"]["attempts"] == 1
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("execution_mode", ["shared", "per_workflow_process"])
def test_profile_microphone_uses_start_time_forwarder_snapshot(
    tmp_path, monkeypatch, execution_mode
):
    old_server, _ = _receiver()
    new_server, _ = _receiver(reset=False)
    monkeypatch.setenv("SPEECH_TO_TEXT_DESTINATION_ALLOWED_NETWORKS", "127.0.0.0/8")
    completion_gate = threading.Event()

    class _Model:
        def close(self):
            pass

    class _Session:
        source_id = "profile-run"

        def __init__(self):
            self.result_queue = queue.Queue()
            self.result_queue.put(
                {"type": "transcript", "sequence": 1, "text": "สวัสดี"}
            )
            threading.Thread(target=self._complete, daemon=True).start()

        def _complete(self):
            completion_gate.wait(timeout=5)
            self.result_queue.put(
                {"type": "completed", "source_id": self.source_id, "status": "completed"}
            )

        def stop(self, timeout=30):
            completion_gate.set()

    monkeypatch.setattr(transcribe_service, "load_model", lambda _config: _Model())
    monkeypatch.setattr(
        transcribe_service,
        "start_microphone_and_forward",
        lambda _device, _model, forwarder, matching, _flow, *, on_finished=None: (
            transcribe_api.forward_session(
                _Session(), forwarder, matching, on_finished=on_finished
            )
        ),
    )

    def start_group(**_kwargs):
        return SimpleNamespace(
            sessions=[_Session()], stop=lambda: None, abort=lambda: None
        )

    monkeypatch.setattr(
        transcribe_service, "start_multiprocess_microphone_flows", start_group
    )
    service = TranscriptionService(model_config={"model": "turbo"})
    store = SQLiteProfileStore(tmp_path / "profiles.sqlite3")
    runtime = BackendRuntime(workflow_service=service, profile_store=store)
    client = TestClient(
        create_app(runtime=runtime),
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 51234),
    )
    operator_headers = {"Origin": "http://127.0.0.1:5173"}
    try:
        with client:
            created = client.post(
                "/api/v1/destinations",
                headers=operator_headers,
                json={
                    "name": "Snapshot receiver",
                    "endpoint_url": f"http://127.0.0.1:{old_server.server_port}/old",
                },
            )
            assert created.status_code == 201, created.text
            destination_id = created.json()["destination_id"]
            store.create_profile(
                {
                    "profile_id": "profile-id",
                    "name": "Profile",
                    "device": "Studio Mic",
                    "execution_mode": execution_mode,
                    "keywords": ["สวัสดี"],
                    "silence_threshold": 0,
                    "model": "turbo",
                    "runtime": "openvino-gpu",
                    "destination_id": destination_id,
                }
            )
            started = client.post("/api/v1/profiles/profile-id/runs")
            assert started.status_code == 202, started.text
            workflow_id = started.json()["workflow_id"]
            replaced = client.put(
                f"/api/v1/destinations/{destination_id}",
                headers=operator_headers,
                json={
                    "name": "Snapshot receiver",
                    "endpoint_url": f"http://127.0.0.1:{new_server.server_port}/new",
                },
            )
            assert replaced.status_code == 200, replaced.text
            completion_gate.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                snapshot = client.get(
                    f"/api/v1/transcriptions/{workflow_id}"
                ).json()
                if snapshot["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.02)
            assert snapshot["status"] == "completed"
            assert snapshot["destination_id"] == destination_id
            assert snapshot["delivery"]["status"] == "delivered"
            assert any(path == "/old" for path, _key, _record in _Receiver.records)
            assert not any(path == "/new" for path, _key, _record in _Receiver.records)
    finally:
        completion_gate.set()
        old_server.shutdown()
        old_server.server_close()
        new_server.shutdown()
        new_server.server_close()
