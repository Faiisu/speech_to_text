"""Tests for the parts of the station service that need no model and no mic.

These cover the failures that would be expensive to find in production: memory
that grows until the machine dies, audio silently lost under load, and a
station bound to the wrong microphone.
"""

import json
import threading

import numpy as np
import pytest

from stations.capture import DeviceError, _RingBuffer
from stations.config import ConfigError, Settings, Station, _from_dict, load, save
from stations.engine import ChunkQueue, StationHealth


# -- the PoC's leak, as a test --------------------------------------------


def test_ring_buffer_memory_is_bounded_over_a_long_run():
    """The reason capture.py exists rather than reusing run_recording_session.

    The PoC keeps every sample of a session. A station runs for weeks, so what
    matters is that retained audio is a function of the chunk size and nothing
    else.
    """
    chunk, step = 80_000, 64_000  # 5s chunks, 1s overlap at 16kHz
    buffer = _RingBuffer(chunk)
    chunks_out = 0

    # ~1 hour of audio in 1024-sample callback blocks
    for _ in range(int(3600 * 16_000 / 1024)):
        buffer.add(np.zeros(1024, dtype="float32"))
        while buffer.take_chunk() is not None:
            buffer.advance(step)
            chunks_out += 1

    # 3600s of audio at a 4s step (5s chunk minus 1s overlap) is ~900 chunks.
    assert 890 < chunks_out < 910, f"expected ~900 chunks from an hour, got {chunks_out}"
    assert buffer.available < chunk + 1024, "retained audio must not grow with uptime"


def test_ring_buffer_chunks_overlap_by_exactly_the_configured_amount():
    chunk, step = 100, 80  # 20 samples of overlap
    buffer = _RingBuffer(chunk)
    buffer.add(np.arange(500, dtype="float32"))

    first = buffer.take_chunk()
    buffer.advance(step)
    second = buffer.take_chunk()

    assert first[0] == 0 and first[-1] == 99
    assert second[0] == 80, "the next chunk starts one step later"
    np.testing.assert_array_equal(first[80:], second[:20], "the overlap must be the same audio")


# -- backpressure ----------------------------------------------------------


def _station(station_id="a", label="Line 1", device="mic"):
    return Station(id=station_id, label=label, device=device)


def test_full_queue_drops_the_oldest_chunk_and_counts_it():
    """Under overload the oldest audio is the least useful; losing it is the
    designed behaviour, and it must be counted rather than silent."""
    a = _station("a", "Line 1")
    queue = ChunkQueue(maxsize=2)
    queue.put((a, None, 0.0))
    queue.put((a, None, 1.0))

    evicted = queue.put((a, None, 2.0))

    assert evicted == "a"
    assert queue.dropped["a"] == 1
    _, _, oldest = queue.get(timeout=1)
    assert oldest == 1.0, "the chunk from t=0 was the one dropped"


def test_queue_bound_and_drop_accounting_survive_concurrent_producers():
    a, b = _station("a", "Line 1"), _station("b", "Line 2")
    queue = ChunkQueue(maxsize=4)

    def flood(station):
        for i in range(300):
            queue.put((station, None, float(i)))

    threads = [threading.Thread(target=flood, args=(s,)) for s in (a, b)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert queue.depth <= 4, "the bound is what stops memory growing under overload"
    assert sum(queue.dropped.values()) + queue.depth == 600, "every chunk is accounted for"


def test_health_reports_demand_from_recent_chunks_only():
    """Demand is a rolling figure: a station that recovers has to show it
    without waiting for a restart."""
    health = StationHealth(station_id="a", label="Line 1", session_id="s", step_seconds=4.0)
    assert health.demand is None, "no data yet is not the same as falling behind"

    for _ in range(20):
        health._recent_latency.append(8.0)
    assert health.demand == pytest.approx(2.0), "needs two workers, has one"

    for _ in range(20):
        health._recent_latency.append(1.2)
    assert health.demand == pytest.approx(0.3)


# -- configuration ---------------------------------------------------------


def test_duplicate_labels_are_rejected():
    """Two stations called "Line 1" make every stored detection ambiguous."""
    with pytest.raises(ConfigError, match="Duplicate station label"):
        Settings(stations=[_station("a", "Line 1"), _station("b", "line 1")])


def test_duplicate_ids_are_rejected():
    with pytest.raises(ConfigError, match="Duplicate station id"):
        Settings(stations=[_station("a", "Line 1"), _station("a", "Line 2")])


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"id": "Line 1"}, "must be lowercase"),
        ({"label": "   "}, "needs a label"),
        ({"device": ""}, "needs a device name"),
        ({"language": "klingon"}, "unknown language"),
        ({"chunk_seconds": 45}, "exceeds Whisper"),
        ({"overlap_seconds": 9, "chunk_seconds": 5}, "overlap_seconds"),
        ({"silence_threshold": 1.5}, "silence_threshold"),
    ],
)
def test_invalid_station_is_rejected_with_a_usable_message(kwargs, match):
    base = {"id": "a", "label": "Line 1", "device": "mic"}
    with pytest.raises(ConfigError, match=match):
        Station(**{**base, **kwargs})


def test_unknown_field_is_rejected_rather_than_silently_defaulted():
    """A typo'd key taking a default is how a station ends up on the wrong mic."""
    with pytest.raises(ConfigError, match="unknown field"):
        _from_dict({"stations": [{"id": "a", "label": "L", "device": "m", "devcie": "typo"}]})


def test_config_round_trips_through_the_file(tmp_path):
    path = tmp_path / "stations.json"
    settings = Settings(
        runtime="openvino-gpu",
        stations=[Station(id="line1", label="Line 1", device="USB Mic", keywords=["สวัสดี"])],
    )
    save(settings, path)
    loaded = load(path)

    assert loaded.runtime == "openvino-gpu"
    assert loaded.stations[0].keywords == ["สวัสดี"]
    assert json.loads(path.read_text())["stations"][0]["label"] == "Line 1"


def test_broken_config_raises_rather_than_running_zero_stations(tmp_path):
    """Falling back to an empty default would look identical to running fine."""
    path = tmp_path / "stations.json"
    path.write_text("{not json")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load(path)


def test_missing_config_file_is_an_empty_default(tmp_path):
    assert load(tmp_path / "absent.json").stations == []


# -- device binding --------------------------------------------------------


def test_ambiguous_device_name_is_an_error_not_a_guess(monkeypatch):
    """Two identical USB mics: picking one would mislabel every detection."""
    import stations.capture as capture

    monkeypatch.setattr(capture, "input_devices", lambda: [
        {"index": 0, "name": "USB Audio Device", "channels": 1},
        {"index": 1, "name": "USB Audio Device #2", "channels": 1},
    ])
    with pytest.raises(DeviceError, match="matches 2 inputs"):
        capture.resolve_device("USB Audio")

    # An exact match is unambiguous even when it is a prefix of another name.
    assert capture.resolve_device("USB Audio Device") == 0


def test_absent_device_lists_what_is_available(monkeypatch):
    import stations.capture as capture

    monkeypatch.setattr(capture, "input_devices", lambda: [
        {"index": 0, "name": "Built-in Microphone", "channels": 1},
    ])
    with pytest.raises(DeviceError, match="Built-in Microphone"):
        capture.resolve_device("Rode NT-USB")


# -- what reaches the database --------------------------------------------


def test_report_event_sends_the_station(monkeypatch):
    import transcribe

    sent = {}
    monkeypatch.setattr(
        transcribe.requests, "post",
        lambda url, json, timeout: sent.update({"url": url, **json}),
    )
    transcribe.report_event("http://backend", "สวัสดี", "turbo", "sess-1", "Line 2")

    assert sent["station"] == "Line 2"
    assert sent["word"] == "สวัสดี"
    assert sent["url"] == "http://backend/events"


def test_report_event_survives_an_unreachable_backend(monkeypatch, capsys):
    """A database outage must not stop transcription."""
    import transcribe

    def boom(*args, **kwargs):
        raise transcribe.requests.RequestException("connection refused")

    monkeypatch.setattr(transcribe.requests, "post", boom)
    transcribe.report_event("http://backend", "x", "turbo", "s", "Line 1")

    assert "failed to report event" in capsys.readouterr().out


# -- benchmark API ---------------------------------------------------------


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import production_server

    production_server._supervisor = None
    production_server._benchmark_running = False
    return TestClient(production_server.app)


def _benchmark_body(**overrides):
    body = {
        "clips": ["test_clip.wav"],
        "streams": [1],
        "duration": 60,
        "runtime": "ctranslate2",
        "chunk_seconds": 5,
    }
    return {**body, **overrides}


@pytest.mark.parametrize(
    "overrides, match",
    [
        ({"clips": []}, "at least one clip"),
        ({"clips": ["../server.py"]}, "No such clip"),
        ({"clips": ["nope.wav"]}, "No such clip"),
        ({"streams": []}, "at least one stream"),
        ({"streams": [99]}, "ceiling"),
        ({"duration": 10}, "at least 20s"),
        ({"duration": 25, "chunk_seconds": 10}, "at least 40s"),
    ],
)
def test_benchmark_rejects_bad_requests(client, overrides, match):
    response = client.post("/api/benchmark", json=_benchmark_body(**overrides))
    assert response.status_code == 422
    assert match in response.json()["detail"]


def test_benchmark_clip_names_cannot_escape_the_audio_directory(client):
    """The PoC panel shipped exactly this hole once (issue 14)."""
    for name in ["../server.py", "/etc/passwd", "../../tmp/x.wav"]:
        response = client.post("/api/benchmark", json=_benchmark_body(clips=[name]))
        assert response.status_code == 422, f"{name} was not contained"


def test_benchmark_refuses_while_stations_are_live(client):
    """It saturates the machine; running it against live microphones would
    both corrupt the timings and starve the real work."""
    import production_server

    production_server._supervisor = object()
    try:
        response = client.post("/api/benchmark", json=_benchmark_body())
        assert response.status_code == 409
        assert "Stop the station service" in response.json()["detail"]
    finally:
        production_server._supervisor = None


def test_benchmark_refuses_a_second_concurrent_run(client):
    """Two runs would contend for the same hardware and silently corrupt the
    timings the benchmark exists to produce."""
    import production_server

    production_server._benchmark_running = True
    try:
        response = client.post("/api/benchmark", json=_benchmark_body())
        assert response.status_code == 409
        assert "already running" in response.json()["detail"]
    finally:
        production_server._benchmark_running = False


def test_short_runs_are_rejected_because_the_drain_absorbs_the_backlog(client):
    """A 12s run reported 1 stream as sustained where a 25s run did not: the
    post-run drain window swallowed the backlog. The floor is what stops the
    benchmark reporting a capacity the machine does not have."""
    ok = client.post("/api/benchmark", json=_benchmark_body(duration=19))
    assert ok.status_code == 422, "19s must be under the floor for a 5s chunk"


def test_benchmark_summary_reports_the_largest_sustained_count():
    from benchmark_parallel import summarise

    results = [
        {"streams": 1, "sustained": True},
        {"streams": 2, "sustained": True},
        {"streams": 3, "sustained": False},
    ]
    summary = summarise(results, 5.0, "openvino-gpu")
    assert summary["max_sustained"] == 2
    assert summary["first_failure"] == 3
    assert "Sustained 2" in summary["text"]

    none_kept_up = summarise([{"streams": 1, "sustained": False}], 5.0, "openvino-gpu")
    assert none_kept_up["max_sustained"] == 0
    assert "No stream count kept up" in none_kept_up["text"]


# -- runtime auto-detection ------------------------------------------------


def test_runtimes_endpoint_reports_availability_and_a_reason(client):
    """The UI greys out what can't run here, so every entry needs a reason —
    "why isn't openvino-gpu in the list" is the question this answers."""
    response = client.get("/api/runtimes", params={"model": "turbo"})
    assert response.status_code == 200

    entries = response.json()["runtimes"]
    assert {e["name"] for e in entries} >= {"pytorch", "openvino-gpu", "ctranslate2"}
    for entry in entries:
        assert entry["reason"], f"{entry['name']} has no reason"
        assert isinstance(entry["available"], bool)
    assert any(e["available"] for e in entries), "pytorch is always available"


def test_runtime_availability_depends_on_the_model(client):
    """A runtime needs *that model's* weights converted for it, so the answer
    changes with the model. Asking once and reusing it is how the demo panel
    ended up offering a runtime the door then rejected."""
    converted = {e["name"]: e for e in client.get("/api/runtimes", params={"model": "turbo"}).json()["runtimes"]}
    unconverted = {e["name"]: e for e in client.get("/api/runtimes", params={"model": "large-v3"}).json()["runtimes"]}

    assert converted["ctranslate2"]["available"] is True
    assert unconverted["ctranslate2"]["available"] is False
    assert "convert_model.py" in unconverted["ctranslate2"]["reason"]


def test_runtimes_endpoint_rejects_an_unknown_model(client):
    assert client.get("/api/runtimes", params={"model": "no-such-model"}).status_code == 422


def test_models_endpoint_lists_what_this_machine_offers(client):
    models = client.get("/api/models").json()["models"]
    keys = {m["key"] for m in models}
    assert "turbo" in keys, "the builtin Typhoon models are always offered"
    turbo = next(m for m in models if m["key"] == "turbo")
    assert turbo["repo"] == "typhoon-ai/typhoon-whisper-turbo"


# -- workers vs the accelerator -------------------------------------------


@pytest.mark.parametrize("runtime", ["openvino-gpu", "openvino-npu"])
def test_gpu_and_npu_runtimes_are_pinned_to_one_worker(runtime):
    """Every worker shares one loaded model, so a second worker on a single
    exclusive accelerator both contends for the same execution units and calls
    a library that does not promise thread-safety from two threads. It can
    only do harm, so it is refused rather than offered."""
    assert Settings(runtime=runtime, workers=1).workers == 1
    with pytest.raises(ConfigError, match="single exclusive accelerator"):
        Settings(runtime=runtime, workers=2)


@pytest.mark.parametrize("runtime", ["ctranslate2", "openvino-cpu", "whispercpp", "pytorch"])
def test_cpu_runtimes_still_allow_extra_workers(runtime):
    assert Settings(runtime=runtime, workers=4).workers == 4


def test_config_endpoint_rejects_extra_workers_on_the_igpu(client):
    body = {
        "model": "turbo",
        "runtime": "openvino-gpu",
        "backend_url": "http://localhost:8000",
        "queue_size": 6,
        "workers": 2,
        "stations": [],
    }
    response = client.put("/api/config", json=body)
    assert response.status_code == 422
    assert "single exclusive accelerator" in response.json()["detail"]


def test_benchmark_rejects_extra_workers_on_the_igpu_before_loading(client):
    """Caught at the door, not a minute into the stream."""
    response = client.post(
        "/api/benchmark",
        json=_benchmark_body(runtime="openvino-gpu", workers=2, duration=60),
    )
    assert response.status_code == 422
    assert "1 worker" in response.json()["detail"]


# -- clearing the live feed ------------------------------------------------


def test_clearing_the_feed_leaves_stored_detections_alone(client, monkeypatch):
    """Clearing is a display action. The detections in TimescaleDB are the
    record this system exists to produce, and must survive it."""
    import production_server

    production_server._recent.clear()
    production_server._recent["line1"] = [{"type": "chunk", "station_id": "line1"}]
    production_server._recent["line2"] = [{"type": "chunk", "station_id": "line2"}] * 3

    calls = []
    monkeypatch.setattr(production_server.requests, "get",
                        lambda *a, **k: calls.append(a) or pytest.fail("must not touch the DB"))

    response = client.request("DELETE", "/api/recent")
    assert response.status_code == 200
    assert response.json()["events"] == 4
    assert production_server._recent == {}
    assert not calls


def test_clearing_one_station_leaves_the_others(client):
    import production_server

    production_server._recent.clear()
    production_server._recent["line1"] = [{"type": "chunk"}, {"type": "chunk"}]
    production_server._recent["line2"] = [{"type": "chunk"}]

    response = client.request("DELETE", "/api/recent", params={"station_id": "line1"})
    assert response.json()["events"] == 2
    assert "line1" not in production_server._recent
    assert len(production_server._recent["line2"]) == 1, "the other station is untouched"


def test_clearing_an_unknown_station_is_not_an_error(client):
    """Clearing a station that has said nothing yet is a no-op, not a 404."""
    import production_server

    production_server._recent.clear()
    assert client.request("DELETE", "/api/recent", params={"station_id": "nope"}).status_code == 200


# -- room noise measurement ------------------------------------------------


def test_measure_noise_reports_the_floor_and_a_threshold_above_it(monkeypatch):
    """The threshold has to sit above what the mic reads in a quiet room, and
    that number is a property of the room — not something to guess at."""
    import stations.capture as capture

    quiet = np.full(16_000 * 3, 0.01, dtype="float32")
    monkeypatch.setattr(capture, "resolve_device", lambda name: 0)
    monkeypatch.setattr(capture.sd, "rec", lambda *a, **k: quiet.reshape(-1, 1))
    monkeypatch.setattr(capture.sd, "wait", lambda: None)

    result = capture.measure_noise("USB Mic", 3.0)

    assert result["rms"] == pytest.approx(0.01, abs=1e-4)
    assert result["dbfs"] == pytest.approx(-40.0, abs=0.5)
    assert result["suggested_threshold"] > result["rms"], "must leave headroom over the floor"
    assert result["suggested_threshold"] == pytest.approx(0.03, abs=1e-3)
    assert result["noisy"] is False


def test_measure_noise_flags_a_room_too_loud_to_gate(monkeypatch):
    """A floor this high is not a threshold problem, and saying so beats
    letting someone raise the gate until real speech is dropped too."""
    import stations.capture as capture

    loud = np.full(16_000, 0.2, dtype="float32")
    monkeypatch.setattr(capture, "resolve_device", lambda name: 0)
    monkeypatch.setattr(capture.sd, "rec", lambda *a, **k: loud.reshape(-1, 1))
    monkeypatch.setattr(capture.sd, "wait", lambda: None)

    assert capture.measure_noise("USB Mic", 1.0)["noisy"] is True


def test_measure_noise_endpoint_rejects_a_silly_duration(client):
    for seconds in [0.5, 60]:
        response = client.post("/api/measure-noise", json={"device": "x", "seconds": seconds})
        assert response.status_code == 422
        assert "between 1 and 15" in response.json()["detail"]


def test_measure_noise_endpoint_reports_a_missing_microphone(client, monkeypatch):
    import production_server

    def absent(name, seconds):
        raise DeviceError(f"No input device matching {name!r}")

    monkeypatch.setattr(production_server, "measure_noise", absent)
    response = client.post("/api/measure-noise", json={"device": "Rode NT-USB", "seconds": 5})
    assert response.status_code == 422
    assert "Rode NT-USB" in response.json()["detail"]


def test_measure_noise_endpoint_reports_a_busy_microphone(client, monkeypatch):
    """A mic held exclusively by something else is a 503 with the reason, not
    a 500 the operator has to go read the logs for."""
    import production_server

    def busy(name, seconds):
        raise RuntimeError("Device unavailable")

    monkeypatch.setattr(production_server, "measure_noise", busy)
    response = client.post("/api/measure-noise", json={"device": "USB Mic", "seconds": 5})
    assert response.status_code == 503
    assert "Device unavailable" in response.json()["detail"]


# -- network stream stations (CCTV) ---------------------------------------


@pytest.mark.parametrize(
    "device, expected",
    [
        ("rtsp://cam:554/stream1", True),
        ("RTSP://Cam:554/stream1", True),
        ("rtsps://cam/s", True),
        ("http://host/audio.mp3", True),
        ("USB Audio Device", False),
        ("HDA Intel PCH: ALC888-VD Analog (hw:0,0)", False),
        ("", False),
    ],
)
def test_stream_urls_are_told_apart_from_device_names(device, expected):
    from stations.capture import is_stream_url

    assert is_stream_url(device) is expected


def test_a_stream_station_gets_the_stream_capture():
    """The engine must not care which it is — that is what makes a camera a
    station rather than a special case."""
    from stations.capture import StationCapture, StreamCapture, open_capture

    cam = Station(id="cam1", label="Camera 1", device="rtsp://cam:554/s")
    mic = Station(id="mic1", label="Desk", device="USB Audio Device")

    assert isinstance(open_capture(cam, lambda *a: None), StreamCapture)
    assert isinstance(open_capture(mic, lambda *a: None), StationCapture)


def test_the_ffmpeg_command_asks_for_what_whisper_needs():
    from stations.capture import StreamCapture

    cam = Station(id="cam1", label="Camera 1", device="rtsp://cam:554/s")
    command = StreamCapture(cam, lambda *a: None)._command()

    assert command[:1] == ["ffmpeg"]
    assert "-vn" in command, "the video is not our business"
    assert command[command.index("-ar") + 1] == "16000", "Whisper wants 16kHz"
    assert command[command.index("-ac") + 1] == "1", "mono"
    assert command[command.index("-f") + 1] == "f32le", "the format the ring buffer holds"
    # UDP RTSP loses packets silently, which arrives as subtly wrong audio
    # rather than as an error.
    assert command[command.index("-rtsp_transport") + 1] == "tcp"
    # Without this an unreachable camera leaves a thread alive and no audio,
    # which the watchdog would read as healthy.
    assert "-timeout" in command


def test_a_bad_url_scheme_is_rejected_at_configuration_time():
    """Otherwise it is treated as a microphone name and fails much later with
    'no input device matching rtspp://...'."""
    with pytest.raises(ConfigError, match="not a stream this can read"):
        Station(id="cam1", label="Camera 1", device="rtspp://typo/s")


def test_check_device_requires_ffmpeg_for_streams(monkeypatch):
    import stations.capture as capture

    monkeypatch.setattr(capture.shutil, "which", lambda name: None)
    with pytest.raises(DeviceError, match="needs ffmpeg"):
        capture.check_device("rtsp://cam:554/s")

    monkeypatch.setattr(capture.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    capture.check_device("rtsp://cam:554/s")  # no raise


def test_an_unreachable_camera_is_a_device_error_not_a_traceback(monkeypatch):
    """A hung ffmpeg must not reach the operator as TimeoutExpired."""
    import subprocess

    import stations.capture as capture

    monkeypatch.setattr(capture.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1)

    monkeypatch.setattr(capture.subprocess, "run", timeout)
    with pytest.raises(DeviceError, match="did not respond"):
        capture.measure_noise("rtsp://cam:554/s", 2.0)


def test_a_camera_with_no_audio_says_so(monkeypatch):
    """Many cameras ship with audio disabled, or have no microphone at all —
    the most likely reason for an empty stream, and worth saying."""
    import stations.capture as capture

    monkeypatch.setattr(capture.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    class Result:
        stdout = b""
        stderr = b"Stream map '0:a' matches no streams"

    monkeypatch.setattr(capture.subprocess, "run", lambda *a, **k: Result())
    with pytest.raises(DeviceError, match="audio disabled"):
        capture.measure_noise("rtsp://cam:554/s", 2.0)


def test_rtsp_only_options_are_not_passed_to_other_schemes():
    """ffmpeg rejects the whole command if -rtsp_transport is given for an
    http:// input — "Option rtsp_transport not found" — so the stream produces
    nothing at all, silently."""
    from stations.capture import _input_options

    rtsp = _input_options("rtsp://cam:554/s")
    assert rtsp[:2] == ["-rtsp_transport", "tcp"]

    for url in ["http://host/a.mp4", "srt://host:1234", "udp://host:5000"]:
        assert "-rtsp_transport" not in _input_options(url), url
        assert "-timeout" in _input_options(url), "every scheme still needs a timeout"


def test_a_dead_stream_stops_the_chunk_loop_once_the_buffer_is_drained():
    """Without this the loop waits for a chunk that can never arrive, with the
    thread still alive — which the watchdog reads as a healthy station."""
    from stations.capture import StreamCapture

    cam = Station(id="cam1", label="Camera 1", device="rtsp://cam:554/s",
                  chunk_seconds=1, overlap_seconds=0)
    capture = StreamCapture(cam, lambda *a: None)

    class Process:
        def __init__(self, code):
            self._code = code

        def poll(self):
            return self._code

    capture._process = Process(None)  # still running
    assert capture._source_alive() is True

    capture._process = Process(1)  # died, nothing buffered
    assert capture._source_alive() is False

    # Audio already captured is still worth transcribing before giving up.
    capture._buffer.add(np.zeros(cam.chunking.chunk_samples, dtype="float32"))
    assert capture._source_alive() is True


# -- load: the number that actually answers "are we keeping up" ------------


def _health(step_seconds, latency, samples=5):
    from stations.engine import StationHealth

    health = StationHealth(station_id="a", label="L", session_id="s", step_seconds=step_seconds)
    for _ in range(samples):
        health._recent_rtf.append(latency / (step_seconds + 1))
        health._recent_latency.append(latency)
    return health


def test_demand_divides_by_the_step_not_the_chunk():
    """Chunks arrive one step apart, not one chunk apart. Dividing by the
    chunk understated the load by exactly the overlap: with the default 5s/1s
    a station could show RTF 0.9 while its queue grew."""
    # 5s chunk, 1s overlap -> a chunk every 4s; each takes 4.5s.
    health = _health(step_seconds=4.0, latency=4.5)

    assert health.demand == pytest.approx(4.5 / 4.0)
    assert health.demand > 1.0, "one station already needs more than one worker"


def test_load_adds_the_stations_up():
    """Three stations at 0.375 need 1.125 workers, which one iGPU is not.
    RTF never showed this because it does not know there are three."""
    from stations.engine import Engine

    settings = Settings(runtime="ctranslate2", workers=1)
    engine = Engine(settings)
    for n in range(3):
        engine.health[f"s{n}"] = _health(step_seconds=4.0, latency=1.5)

    assert engine.load == pytest.approx(1.125)
    assert engine.keeping_up is False, "over capacity, however good each RTF looks"


def test_load_accounts_for_extra_workers():
    from stations.engine import Engine

    settings = Settings(runtime="ctranslate2", workers=2)
    engine = Engine(settings)
    for n in range(3):
        engine.health[f"s{n}"] = _health(step_seconds=4.0, latency=1.5)

    assert engine.load == pytest.approx(0.5625)
    assert engine.keeping_up is True


def test_load_is_unknown_before_any_chunk():
    from stations.engine import Engine

    engine = Engine(Settings(runtime="ctranslate2"))
    assert engine.load is None
    assert engine.keeping_up is None, "no data is not the same as falling behind"


def test_rtf_is_still_reported_and_still_means_what_it_meant():
    """RTF stays: it is a correct measure of model speed on this machine, and
    the thing to watch when comparing runtimes. It just isn't the capacity
    number."""
    health = _health(step_seconds=4.0, latency=1.5)
    assert health.mean_rtf is not None
    assert health.mean_latency == pytest.approx(1.5)
    assert health.as_dict()["mean_rtf"] is not None
    assert health.as_dict()["demand"] == pytest.approx(0.375)


def test_the_engine_can_still_load_its_model_and_process_a_chunk():
    """Regression: adding a `load` property shadowed the `load()` method that
    loads the runtime, and every inference worker died at start with
    'NoneType is not callable'. Nothing caught it, because no test had ever
    run a worker."""
    import time

    from stations.engine import Engine

    class FakeRuntime:
        def transcribe(self, audio, language="th", decoding=None):
            return "สวัสดี"

    station = Station(id="s1", label="Line 1", device="mic",
                      chunk_seconds=1.0, overlap_seconds=0.0, silence_threshold=0.0)
    engine = Engine(Settings(runtime="ctranslate2", stations=[station]))
    engine._runtime = FakeRuntime()

    assert engine.load_model() is engine._runtime
    engine.register(station)
    engine.start()
    try:
        engine.submit(station, np.ones(16_000, dtype="float32") * 0.5, 0.0)
        deadline = time.time() + 5
        while engine.health["s1"].chunks_done == 0 and time.time() < deadline:
            time.sleep(0.05)
    finally:
        engine.stop()

    assert engine.health["s1"].chunks_done == 1, "the worker never processed the chunk"
    assert engine.load is not None, "and the capacity metric still works"
