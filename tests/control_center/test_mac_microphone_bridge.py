"""The Mac host bridge must preserve captured sample order across HTTP batches."""

from types import SimpleNamespace
from http.client import HTTPConnection
import json
from threading import Thread
import sys
from http.server import ThreadingHTTPServer

import numpy as np

from speech_to_text.features.mac_microphone_bridge import __main__ as bridge


class FakeInputStream:
    def __init__(self, *, callback, **_kwargs):
        self.callback = callback
        self.started = False
        self.closed = False

    def start(self):
        self.started = True
        for index, count in enumerate((3500, 3500, 3000, 2400, 2400)):
            self.callback(np.full((count, 1), index + 1, dtype=np.float32), count, None, None)

    def stop(self):
        pass

    def close(self):
        self.closed = True


def test_bridge_batches_variable_callback_sizes_without_reordering_or_dropping(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(InputStream=FakeInputStream))
    requests = []

    def record_request(path, **kwargs):
        requests.append((path, kwargs))
        return {}

    monkeypatch.setattr(bridge, "_request", record_request)
    session = bridge.BridgeSession(source_id="batching-test", ingest_token="session-token",
                                  device=None, sample_rate=48000)
    session.stop()

    audio = [kwargs["body"] for path, kwargs in requests if path.endswith("/audio")]
    batches = [np.frombuffer(body, dtype="<f4") for body in audio]
    assert [len(batch) for batch in batches] == [4800, 4800, 4800, 400]
    actual = np.concatenate(batches)
    expected = np.concatenate([np.full(count, index + 1, dtype=np.float32)
                               for index, count in enumerate((3500, 3500, 3000, 2400, 2400))])
    np.testing.assert_array_equal(actual, expected)
    assert any(path.endswith("/stop") and kwargs["method"] == "POST" for path, kwargs in requests)


def test_control_origin_supports_wildcard_and_strict_loopback_modes(monkeypatch):
    class FakeRegistry:
        def start(self, payload):
            assert payload == {"device": "MacBook Air Microphone"}
            return {"source_id": "origin-test", "state": "running"}

        def stop(self, source_id):
            assert source_id == "origin-test"
            return {"source_id": source_id, "state": "stopped"}

    monkeypatch.setattr(bridge, "CONTROL_ORIGIN", "http://127.0.0.1:18766")
    monkeypatch.setattr(bridge, "REGISTRY", FakeRegistry())
    monkeypatch.setattr(bridge, "list_devices", lambda: [{"name": "MacBook Air Microphone", "default": True}])
    server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def send(method, path, origin, body=None, headers=None):
            request_headers = {"Origin": origin, **(headers or {})}
            if body is not None:
                request_headers.setdefault("Content-Length", str(len(body)))
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            result = response.status, dict(response.getheaders()), response.read()
            connection.close()
            return result

        origin = "http://localhost:18766"
        status, headers, body = send("OPTIONS", "/api/sessions", origin, headers={
            "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
        assert status == 204
        assert headers["Access-Control-Allow-Origin"] == origin
        status, headers, body = send("GET", "/api/devices", origin)
        assert status == 200
        assert headers["Access-Control-Allow-Origin"] == origin
        assert json.loads(body)["devices"][0]["name"] == "MacBook Air Microphone"
        payload = json.dumps({"device": "MacBook Air Microphone"}).encode()
        status, headers, body = send("POST", "/api/sessions", origin, payload,
                                     {"Content-Type": "application/json"})
        assert status == 200
        assert headers["Access-Control-Allow-Origin"] == origin
        status, headers, body = send("DELETE", "/api/sessions/origin-test", origin)
        assert status == 200
        assert json.loads(body)["state"] == "stopped"

        for rejected_origin in (
            "http://attacker.example:18766", "http://localhost.evil:18766",
            "http://localhost:18767", "https://localhost:18766",
            "http://localhost:18766/path", "http://user@localhost:18766",
            "http://localhost:18766?next=attacker.example",
        ):
            status, headers, _ = send("GET", "/api/devices", rejected_origin)
            assert status == 403, rejected_origin
            assert "Access-Control-Allow-Origin" not in headers, rejected_origin

        monkeypatch.setattr(bridge, "CONTROL_ORIGIN", "*")
        wildcard_origin = "https://attacker.example"
        status, headers, _ = send("OPTIONS", "/api/sessions", wildcard_origin)
        assert status == 204
        assert headers["Access-Control-Allow-Origin"] == "*"
        status, headers, _ = send("OPTIONS", "/api/sessions", "")
        assert status == 204
        assert headers["Access-Control-Allow-Origin"] == "*"
        status, headers, body = send("GET", "/api/devices", wildcard_origin)
        assert status == 200
        assert headers["Access-Control-Allow-Origin"] == "*"
        assert json.loads(body)["devices"]
        payload = json.dumps({"device": "MacBook Air Microphone"}).encode()
        status, headers, body = send("POST", "/api/sessions", wildcard_origin, payload,
                                     {"Content-Type": "application/json"})
        assert status == 200
        assert headers["Access-Control-Allow-Origin"] == "*"
        status, headers, body = send("DELETE", "/api/sessions/origin-test", wildcard_origin)
        assert status == 200
        assert headers["Access-Control-Allow-Origin"] == "*"
        status, headers, body = send("GET", "/api/devices", "")
        assert status == 200
        assert headers["Access-Control-Allow-Origin"] == "*"
        status, headers, body = send("POST", "/api/sessions", "", payload,
                                     {"Content-Type": "application/json"})
        assert status == 200
        assert headers["Access-Control-Allow-Origin"] == "*"
        status, headers, body = send("DELETE", "/api/sessions/origin-test", "")
        assert status == 200
        assert headers["Access-Control-Allow-Origin"] == "*"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
