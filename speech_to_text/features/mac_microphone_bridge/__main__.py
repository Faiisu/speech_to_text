"""Run a loopback-only host microphone controller for the Mac Docker profile."""

from __future__ import annotations

from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from queue import Empty, Full, Queue
import signal
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

import numpy as np


SERVICE_URL = os.environ.get("SPEECH_TO_TEXT_BRIDGE_SERVICE_URL", "http://127.0.0.1:18766").rstrip("/")
FEATURE_PATH = "/api/features/feature-01-model-deployment"
BRIDGE_PORT = int(os.environ.get("SPEECH_TO_TEXT_BRIDGE_PORT", "18767"))
CONTROL_ORIGIN = os.environ.get("SPEECH_TO_TEXT_BRIDGE_CONTROL_ORIGIN", "*")
MAX_QUEUED_CALLBACKS = 64
BATCH_SECONDS = 0.1


def _validate_local_url(value, *, name, path=""):
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.path.rstrip("/") != path):
        raise ValueError(f"{name} must use local HTTP" + (f" at {path}" if path else ""))
    return value.rstrip("/")


SERVICE_URL = _validate_local_url(SERVICE_URL, name="SPEECH_TO_TEXT_BRIDGE_SERVICE_URL")
CONTROL_ORIGIN_PARSED = urlsplit(CONTROL_ORIGIN)
if CONTROL_ORIGIN != "*" and (
        CONTROL_ORIGIN_PARSED.scheme != "http" or CONTROL_ORIGIN_PARSED.hostname not in {"127.0.0.1", "localhost", "::1"}
        or CONTROL_ORIGIN_PARSED.username or CONTROL_ORIGIN_PARSED.password or CONTROL_ORIGIN_PARSED.path
        or CONTROL_ORIGIN_PARSED.query or CONTROL_ORIGIN_PARSED.fragment):
    raise ValueError("SPEECH_TO_TEXT_BRIDGE_CONTROL_ORIGIN must be '*' or a local HTTP origin")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def allowed_control_origin(origin, configured_origin=None):
    """Return wildcard access or an equivalent loopback origin in strict mode."""
    if configured_origin is None:
        configured_origin = CONTROL_ORIGIN
    if configured_origin == "*":
        return "*"
    try:
        requested = urlsplit(origin or "")
        configured = urlsplit(configured_origin)
        requested_port = requested.port
        configured_port = configured.port
    except ValueError:
        return None
    for parsed in (requested, configured):
        if (parsed.scheme != "http" or parsed.hostname not in LOOPBACK_HOSTS
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
                or not parsed.netloc or parsed.netloc.endswith(":")):
            return None
    if requested.scheme != configured.scheme or requested_port != configured_port:
        return None
    return origin


def _request(path, *, method="GET", payload=None, token=None, body=None, timeout=10):
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None and payload is None:
        headers["Content-Type"] = "application/octet-stream"
    request = Request(f"{SERVICE_URL}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = exc.read(1024).decode("utf-8", "replace")
        raise RuntimeError(f"Speech service returned HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Cannot reach speech service at {SERVICE_URL}: {exc}") from exc


def _resolve_device(device_name):
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        if device_name:
            matches = [(index, item) for index, item in enumerate(devices)
                       if item.get("max_input_channels", 0) and item.get("name") == device_name]
            if len(matches) != 1:
                raise ValueError("Selected Mac microphone was not found uniquely")
            return sd, matches[0][0], matches[0][1]
        index = sd.default.device[0]
        item = sd.query_devices(index, "input")
        return sd, None, item
    except ImportError as exc:
        raise RuntimeError("Install the microphone extra in the host .venv to capture Mac audio") from exc


def list_devices():
    sd, _, _ = _resolve_device(None)
    default_index = sd.default.device[0]
    return [{"name": item["name"], "channels": item["max_input_channels"], "default": index == default_index}
            for index, item in enumerate(sd.query_devices()) if item.get("max_input_channels", 0) > 0]


class BridgeSession:
    def __init__(self, *, source_id, ingest_token, device, sample_rate):
        import sounddevice as sd
        self.source_id = source_id
        self.ingest_token = ingest_token
        self.sample_rate = sample_rate
        self._queue = Queue(MAX_QUEUED_CALLBACKS)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._stream = None
        self._failure = None
        self._backend_finalized = False
        self._worker = threading.Thread(target=self._send_audio, name=f"mac-mic-{source_id[:8]}", daemon=True)
        try:
            self._stream = sd.InputStream(device=device, samplerate=sample_rate, channels=1,
                dtype="float32", blocksize=0, callback=self._capture)
            self._worker.start()
            self._stream.start()
        except Exception:
            self._stop.set()
            if self._stream:
                try:
                    self._stream.close()
                except Exception:
                    pass
            raise

    def _capture(self, frames, frame_count, time_info, status):
        if status:
            self._failure = f"Mac microphone capture status: {status}"
            self._stop.set()
            return
        if self._stop.is_set():
            return
        try:
            self._queue.put_nowait(frames[:, 0].copy())
        except Full:
            self._failure = "Mac microphone bridge queue is full; audio was not silently dropped"
            self._stop.set()

    def _send_audio(self):
        target = max(1, int(self.sample_rate * BATCH_SECONDS))
        pending = deque()
        pending_frames = 0
        try:
            while True:
                if pending_frames < target and not self._stop.is_set():
                    try:
                        frames = self._queue.get(timeout=0.05)
                        pending.append(frames)
                        pending_frames += len(frames)
                    except Empty:
                        pass
                if self._failure and self._stop.is_set():
                    self._close_stream()
                while self._stop.is_set():
                    try:
                        frames = self._queue.get_nowait()
                        pending.append(frames)
                        pending_frames += len(frames)
                    except Empty:
                        break
                if pending_frames >= target or (self._stop.is_set() and pending_frames):
                    count = min(pending_frames, target)
                    pieces = []
                    while count:
                        first = pending.popleft()
                        take = min(len(first), count)
                        pieces.append(first[:take])
                        count -= take
                        pending_frames -= take
                        if take < len(first):
                            pending.appendleft(first[take:])
                    batch = np.concatenate(pieces).astype("<f4", copy=False).tobytes()
                    _request(f"{FEATURE_PATH}/sessions/{quote(self.source_id, safe='')}/audio",
                             method="POST", token=self.ingest_token, body=batch)
                elif self._stop.is_set():
                    if self._failure:
                        self._close_stream()
                        try:
                            _request(f"{FEATURE_PATH}/sessions/{quote(self.source_id, safe='')}/capture-error",
                                     method="POST", token=self.ingest_token,
                                     payload={"message": self._failure})
                            self._backend_finalized = True
                        except Exception:
                            pass
                    return
        except Exception as exc:
            self._failure = str(exc)
            self._stop.set()
            self._close_stream()
            try:
                _request(f"{FEATURE_PATH}/sessions/{quote(self.source_id, safe='')}/capture-error",
                         method="POST", token=self.ingest_token, payload={"message": self._failure})
                self._backend_finalized = True
            except Exception:
                pass

    def _close_stream(self):
        with self._lock:
            stream, self._stream = self._stream, None
        if stream:
            try:
                stream.stop()
            finally:
                stream.close()

    def stop(self):
        self._close_stream()
        self._stop.set()
        self._worker.join(timeout=12)
        if self._worker.is_alive():
            raise TimeoutError("Mac microphone bridge did not drain its accepted audio before timeout")
        try:
            if not self._backend_finalized:
                _request(f"{FEATURE_PATH}/sessions/{quote(self.source_id, safe='')}/stop",
                         method="POST", payload={}, timeout=90)
        finally:
            if self._failure:
                raise RuntimeError(self._failure)


class BridgeRegistry:
    def __init__(self):
        self.sessions = {}
        self.lock = threading.Lock()

    def start(self, payload):
        sd, device, info = _resolve_device(payload.get("device"))
        sample_rate = int(round(info["default_samplerate"]))
        if not 8000 <= sample_rate <= 48000:
            raise ValueError(f"Mac microphone sample rate {sample_rate} is outside the supported 8–48 kHz range")
        started = _request(f"{FEATURE_PATH}/microphones", method="POST", payload={
            "handle_id": payload.get("handle_id"), "capture_mode": "host-bridge", "sample_rate": sample_rate,
            "flow_config": payload.get("flow_config", {})})
        source_id = started["source_id"]
        try:
            session = BridgeSession(source_id=source_id, ingest_token=started["ingest_token"],
                                    device=device, sample_rate=sample_rate)
        except Exception:
            _request(f"{FEATURE_PATH}/sessions/{quote(source_id, safe='')}/stop", method="POST", payload={})
            raise
        with self.lock:
            self.sessions[source_id] = session
        return {"source_id": source_id, "state": "running"}

    def stop(self, source_id):
        with self.lock:
            session = self.sessions.get(source_id)
        if session is None:
            raise KeyError("Host microphone session was not found")
        try:
            session.stop()
        except TimeoutError:
            raise
        finally:
            if not session._worker.is_alive() and session._stream is None:
                with self.lock:
                    self.sessions.pop(source_id, None)
        with self.lock:
            self.sessions.pop(source_id, None)
        return {"source_id": source_id, "state": "stopped"}


REGISTRY = BridgeRegistry()


class Handler(BaseHTTPRequestHandler):
    server_version = "SpeechToTextMacMicrophoneBridge/1"

    def _allowed_origin(self):
        return allowed_control_origin(self.headers.get("Origin"))

    def _send(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        origin = self._allowed_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        origin = self._allowed_origin()
        if not origin:
            self._send(403, {"detail": "Origin is not allowed"})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self):
        if self.path != "/api/devices" or not self._allowed_origin():
            self._send(403, {"detail": "Request is not allowed"})
            return
        try:
            self._send(200, {"devices": list_devices()})
        except Exception as exc:
            self._send(503, {"detail": str(exc)})

    def do_POST(self):
        if self.path != "/api/sessions" or not self._allowed_origin():
            self._send(403, {"detail": "Request is not allowed"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError("Invalid request size")
            self._send(200, REGISTRY.start(json.loads(self.rfile.read(length))))
        except Exception as exc:
            self._send(400, {"detail": str(exc)})

    def do_DELETE(self):
        if not self._allowed_origin() or not self.path.startswith("/api/sessions/"):
            self._send(403, {"detail": "Request is not allowed"})
            return
        source_id = self.path.rsplit("/", 1)[-1]
        try:
            self._send(200, REGISTRY.stop(source_id))
        except KeyError as exc:
            self._send(404, {"detail": str(exc)})
        except Exception as exc:
            self._send(500, {"detail": str(exc)})

    def log_message(self, fmt, *args):
        return


def main():
    try:
        with urlopen(f"{SERVICE_URL}/api/system", timeout=3) as response:
            if response.status != 200:
                raise RuntimeError(f"Speech service readiness returned HTTP {response.status}")
    except Exception as exc:
        raise SystemExit(f"Cannot reach Mac Docker speech service: {exc}") from exc
    server = ThreadingHTTPServer(("127.0.0.1", BRIDGE_PORT), Handler)
    server.daemon_threads = True
    print(f"Mac microphone bridge listening on http://127.0.0.1:{BRIDGE_PORT}")
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with REGISTRY.lock:
            source_ids = list(REGISTRY.sessions)
        for source_id in source_ids:
            try:
                REGISTRY.stop(source_id)
            except Exception:
                pass
        server.server_close()


if __name__ == "__main__":
    main()
