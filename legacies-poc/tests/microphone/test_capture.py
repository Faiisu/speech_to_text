import threading
import time

import numpy as np
import pytest

import stations.capture as capture_module
from stations.capture import DeviceError, StationCapture, resolve_device
from stations.config import Settings, Station
from stations.supervisor import Supervisor


def _station(device="USB Mic", **changes):
    return Station(id="line1", label="Line 1", device=device,
                   chunk_seconds=0.05, overlap_seconds=0.0,
                   silence_threshold=0.0, **changes)


def test_capture_resolves_exact_device_and_delivers_nonempty_samples(monkeypatch):
    observed = []
    entered = threading.Event()

    monkeypatch.setattr(capture_module, "input_devices", lambda: [
        {"index": 7, "name": "USB Mic", "channels": 1},
        {"index": 8, "name": "USB Mic Conference", "channels": 1},
    ])

    class InputStream:
        def __init__(self, **kwargs):
            assert kwargs["device"] == 7
            assert kwargs["samplerate"] == 16_000
            assert kwargs["channels"] == 1
            self.callback = kwargs["callback"]
            self.active = True

        def __enter__(self):
            self.callback(np.ones((800, 1), dtype="float32"), 800, None, None)
            entered.set()
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(capture_module.sd, "InputStream", InputStream)
    cap = StationCapture(_station(), lambda station, chunk, elapsed:
                         observed.append((station.id, chunk.copy(), elapsed)))
    cap.start()
    try:
        assert entered.wait(1)
        deadline = time.monotonic() + 1
        while not observed and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        cap.stop()

    assert cap._device_index == 7
    assert observed and observed[0][0] == "line1"
    assert observed[0][1].size == 800
    assert np.any(observed[0][1] != 0)
    assert not cap.running


@pytest.mark.parametrize("devices, requested, match", [
    ([], "USB Mic", "No audio input devices"),
    ([{"index": 0, "name": "Built-in Mic", "channels": 1}], "USB Mic", "No input device"),
    ([{"index": 0, "name": "USB Mic A", "channels": 1},
      {"index": 1, "name": "USB Mic B", "channels": 1}], "USB Mic", "matches 2 inputs"),
])
def test_missing_and_ambiguous_names_fail_without_guessing(monkeypatch, devices, requested, match):
    monkeypatch.setattr(capture_module, "input_devices", lambda: devices)
    with pytest.raises(DeviceError, match=match):
        resolve_device(requested)


def test_capture_start_failure_reaches_supervisor_and_watchdog_recovers(monkeypatch):
    events = []
    attempts = []
    active = []

    monkeypatch.setattr(capture_module, "input_devices", lambda: [
        {"index": 0, "name": "USB Mic", "channels": 1},
    ])

    class InputStream:
        def __init__(self, **kwargs):
            attempts.append("open")
            self.callback = kwargs["callback"]
            self.active = True

        def __enter__(self):
            if len(attempts) == 1:
                raise OSError("device disconnected")
            active.append(self)
            self.callback(np.ones((800, 1), dtype="float32"), 800, None, None)
            return self

        def __exit__(self, *args):
            if self in active:
                active.remove(self)
            return False

    monkeypatch.setattr(capture_module.sd, "InputStream", InputStream)
    monkeypatch.setattr("stations.supervisor.WATCH_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr("stations.supervisor.RETRY_BACKOFF_SECONDS", 0.0)
    settings = Settings(runtime="ctranslate2", stations=[_station()])
    supervisor = Supervisor(settings, on_event=events.append)
    # Avoid loading a real model: this test exercises capture lifecycle only.
    supervisor.engine.load_model = lambda: object()
    supervisor.engine.start = lambda: None
    supervisor.engine.stop = lambda: None

    supervisor.start()
    try:
        deadline = time.monotonic() + 1
        while len(attempts) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(attempts) >= 2
        assert supervisor.health()["stations"][0]["id"] == "line1"
        assert any(e.get("state") == "error" for e in events)
        assert any(e.get("state") == "restarting" for e in events)
        assert supervisor.health()["stations"][0]["running"]
    finally:
        supervisor.stop()

    assert not active
    assert not supervisor.health()["stations"][0]["running"]


@pytest.mark.xfail(strict=True, reason="AUDIT: inactive PortAudio stream remains invisible to capture watchdog")
def test_active_input_stream_disconnect_is_detected_and_watchdog_reopens_same_station(monkeypatch):
    events = []
    instances = []
    monkeypatch.setattr(capture_module, "input_devices", lambda: [
        {"index": 0, "name": "USB Mic", "channels": 1},
    ])

    class InputStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
            self.active = True
            instances.append(self)

        def __enter__(self):
            self.callback(np.ones((800, 1), dtype="float32"), 800, None, None)
            return self

        def __exit__(self, *args):
            self.active = False
            return False

    monkeypatch.setattr(capture_module.sd, "InputStream", InputStream)
    monkeypatch.setattr("stations.supervisor.WATCH_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr("stations.supervisor.RETRY_BACKOFF_SECONDS", 0.0)
    station = _station()
    supervisor = Supervisor(Settings(runtime="ctranslate2", stations=[station]), events.append)
    supervisor.engine._runtime = type("Runtime", (), {
        "transcribe": lambda self, audio, language: "",
    })()
    supervisor.engine.load_model = lambda: supervisor.engine._runtime
    supervisor.start()
    try:
        deadline = time.monotonic() + 1
        while (not instances or not supervisor.health()["stations"][0]["chunks_done"]) \
                and time.monotonic() < deadline:
            time.sleep(0.01)
        assert instances and supervisor.health()["stations"][0]["chunks_done"] > 0

        # Model an unplug that leaves the PortAudio Python context open but
        # marks the underlying stream inactive, as happens with hot-unplug.
        instances[0].active = False
        deadline = time.monotonic() + 1
        while len(instances) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(instances) >= 2
        assert any(e.get("state") == "error" and "stopped unexpectedly" in e.get("message", "")
                   for e in events)
        assert any(e.get("state") == "restarting" and e.get("station_id") == station.id
                   for e in events)
        restarted = supervisor.health()["stations"][0]
        assert restarted["id"] == "line1"
        assert restarted["running"]
    finally:
        supervisor.stop()
    assert all(not instance.active for instance in instances)


def test_station_capture_can_stop_and_start_again(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    opened = []
    monkeypatch.setattr(capture_module, "input_devices", lambda: [
        {"index": 2, "name": "USB Mic", "channels": 1},
    ])

    class InputStream:
        def __init__(self, **kwargs):
            self.active = True

        def __enter__(self):
            opened.append(1)
            entered.set()
            release.wait(2)
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(capture_module.sd, "InputStream", InputStream)
    cap = StationCapture(_station(), lambda *_: None)
    cap.start()
    assert entered.wait(1)
    release.set()
    cap.stop()
    assert not cap.running

    entered.clear()
    release.clear()
    release.set()
    cap.start()
    cap.stop()
    assert len(opened) == 2
    assert not cap.running


@pytest.mark.parametrize("failure", ["permission denied", "device busy"])
def test_permission_and_busy_failures_are_reported_and_leave_no_capture_thread(monkeypatch, failure):
    errors = []
    monkeypatch.setattr(capture_module, "input_devices", lambda: [
        {"index": 0, "name": "USB Mic", "channels": 1},
    ])

    class InputStream:
        def __init__(self, **kwargs):
            self.active = True

        def __enter__(self):
            raise OSError(failure)

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(capture_module.sd, "InputStream", InputStream)
    cap = StationCapture(_station(), lambda *_: None,
                         on_error=lambda station, exc: errors.append((station.id, str(exc))))
    cap.start()
    cap._thread.join(timeout=1)

    assert not cap.running
    assert errors and errors[0][0] == "line1"
    assert failure in errors[0][1]
