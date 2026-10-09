import threading
import time

import numpy as np

import stations.capture as capture_module
from stations.capture import StationCapture
from stations.config import Settings, Station
from stations.engine import Engine


def _station(**changes):
    values = dict(id="line1", label="Line 1", device="USB Mic", keywords=[],
                  language="th", silence_threshold=0.0,
                  chunk_seconds=0.05, overlap_seconds=0.0)
    values.update(changes)
    return Station(**values)


class FakeRuntime:
    def __init__(self, text="controlled transcript", fail=None):
        self.text = text
        self.fail = fail
        self.calls = []

    def transcribe(self, audio, language):
        self.calls.append((audio.copy(), language))
        if self.fail:
            raise self.fail
        return self.text


def test_input_callback_reaches_worker_and_emits_station_transcript(monkeypatch):
    delivered = threading.Event()
    events = []
    samples = np.arange(1, 1_201, dtype="float32").reshape(-1, 1) / 1_200

    monkeypatch.setattr(capture_module, "input_devices", lambda: [
        {"index": 4, "name": "USB Mic", "channels": 1},
    ])

    class InputStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
            self.active = True

        def __enter__(self):
            self.callback(samples, len(samples), None, None)
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(capture_module.sd, "InputStream", InputStream)
    station = _station(overlap_seconds=0.025)
    runtime = FakeRuntime("สวัสดีครับ")
    engine = Engine(Settings(runtime="ctranslate2", stations=[station]), on_event=events.append)
    engine._runtime = runtime
    engine.register(station)
    engine.start()
    cap = StationCapture(station, engine.submit)
    cap.start()
    try:
        deadline = time.monotonic() + 2
        while engine.health["line1"].chunks_done < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        delivered.set()
    finally:
        cap.stop()
        workers = list(engine._workers)
        engine.stop()

    assert delivered.is_set()
    assert len(runtime.calls) == 2 and all(call[1] == "th" for call in runtime.calls)
    assert runtime.calls[0][0].shape == runtime.calls[1][0].shape == (800,)
    np.testing.assert_array_equal(runtime.calls[0][0][400:], runtime.calls[1][0][:400])
    assert [event["type"] for event in events] == ["chunk", "chunk"]
    assert all(event["station_id"] == "line1" and event["label"] == "Line 1"
               and event["text"] == "สวัสดีครับ" for event in events)
    assert engine.health["line1"].chunks_done == 2
    assert workers and all(not worker.is_alive() for worker in workers)
    assert not cap.running


def test_silent_chunk_skips_inference_and_emits_silent_event():
    events = []
    station = _station(silence_threshold=0.02)
    runtime = FakeRuntime()
    engine = Engine(Settings(runtime="ctranslate2", stations=[station]), on_event=events.append)
    engine._runtime = runtime
    engine.register(station)

    engine._process(runtime, station, np.zeros(800, dtype="float32"), 0.0)

    assert runtime.calls == []
    assert events == [{"type": "chunk", "station_id": "line1", "label": "Line 1",
                       "time": 0.0, "text": None, "silent": True}]
    assert engine.health["line1"].chunks_silent == 1


def test_runtime_failure_is_visible_in_event_and_station_health():
    events = []
    station = _station()
    runtime = FakeRuntime(fail=RuntimeError("controlled inference failure"))
    engine = Engine(Settings(runtime="ctranslate2", stations=[station]), on_event=events.append)
    engine._runtime = runtime
    engine.register(station)
    engine.start()
    try:
        engine.submit(station, np.ones(800, dtype="float32"), 0.0)
        deadline = time.monotonic() + 2
        while not events and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        workers = list(engine._workers)
        engine.stop()

    assert events[0]["type"] == "error"
    assert events[0]["station_id"] == "line1"
    assert "controlled inference failure" in engine.health["line1"].last_error
    assert workers and all(not worker.is_alive() for worker in workers)


def test_queue_overload_event_is_attributed_to_evicted_station():
    events = []
    first, second = _station(), _station(id="line2", label="Line 2", device="Other Mic")
    engine = Engine(Settings(runtime="ctranslate2", queue_size=1), on_event=events.append)
    engine.register(first)
    engine.register(second)

    engine.submit(first, np.ones(10, dtype="float32"), 0.0)
    engine.submit(second, np.ones(10, dtype="float32"), 1.0)

    assert engine.queue.depth == 1
    assert engine.health["line1"].chunks_dropped == 1
    assert events == [{"type": "drop", "station_id": "line1", "label": "Line 1",
                       "dropped_total": 1, "queue_depth": 1}]
