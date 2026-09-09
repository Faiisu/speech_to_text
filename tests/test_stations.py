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
