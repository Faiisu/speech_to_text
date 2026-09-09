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


def test_health_reports_keeping_up_from_recent_chunks_only():
    health = StationHealth(station_id="a", label="Line 1", session_id="s")
    assert health.keeping_up is None, "no data yet is not the same as falling behind"

    for _ in range(20):
        health._recent_rtf.append(2.0)
    assert health.keeping_up is False

    # The window is rolling: recovery has to show up without a restart.
    for _ in range(20):
        health._recent_rtf.append(0.3)
    assert health.keeping_up is True


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
