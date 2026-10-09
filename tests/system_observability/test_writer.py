"""Persistence contract and non-blocking writer behavior."""

import json
import threading
import time

from speech_to_text.features.system_observability.writer import (
    TelemetryWriter, WriterConfig, make_event, make_measurement,
)


class Cursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def executemany(self, query, rows):
        self.connection.rows.extend((query, row) for row in rows)


class Connection:
    def __init__(self):
        self.rows = []
        self.closed = False
        self.commits = 0

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


def test_measurement_and_event_are_utc_safe_and_keep_rtf_per_chunk():
    measured = make_measurement(feature_id="feature-01", operation="clip", pid=123,
        source_id="clip-1", sequence=4, audio_seconds=2.0, inference_seconds=1.5,
        recorded_at="2026-10-09T20:00:00+07:00")
    assert measured["recorded_at"].isoformat() == "2026-10-09T13:00:00+00:00"
    assert measured["rtf"] == .75
    event = make_event(event_name="inference_failed", severity="error", attributes={
        "code": "decode", "password": "must-not-persist", "message": "must-not-persist",
    })
    assert event["attributes"] == {"code": "decode"}
    assert "message" not in event and "password" not in event


def test_writer_batches_and_serializes_event_and_measurement():
    connection = Connection()
    writer = TelemetryWriter(WriterConfig("unused", batch_size=2, flush_interval_seconds=.02),
        connect=lambda _: connection)
    writer.start()
    writer.publish_measurement({"feature_id":"feature-01","operation":"clip","pid":321,
        "source_id":"s-1","sequence":7,"audio_seconds":1,"inference_seconds":.2,
        "completed_at":"2026-10-09T12:00:00Z"})
    writer.publish_event(make_event(event_name="service_ready"))
    deadline = time.monotonic() + 2
    while connection.commits == 0 and time.monotonic() < deadline:
        time.sleep(.01)
    writer.close()
    assert connection.commits >= 1
    assert len(connection.rows) == 3
    measurement = next(row for query, row in connection.rows if "operation_measurements" in query)
    assert measurement["rtf"] == .2
    event = next(row for query, row in connection.rows if "service_events" in query)
    assert event["severity"] == "info"


def test_database_retry_and_queue_saturation_never_block_publisher():
    entered, release = threading.Event(), threading.Event()
    attempts = []
    connection = Connection()

    def connect(_):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("database unavailable")
        entered.set()
        release.wait(1)
        return connection

    writer = TelemetryWriter(WriterConfig("unused", queue_capacity=1, batch_size=1,
        flush_interval_seconds=.02, max_retries=1), connect=connect)
    writer.start()
    record = make_event(event_name="queued")
    writer.publish_event(record)
    assert entered.wait(1)
    assert writer.publish_event(record)
    started = time.monotonic()
    assert not writer.publish_event(record)
    assert time.monotonic() - started < .1
    assert writer.dropped_count >= 1
    release.set()
    writer.close(timeout=2)
    assert len(attempts) >= 2


def test_safe_event_attributes_do_not_serialize_arbitrary_values():
    row = make_event(event_name="queue_warning", attributes={"queue_depth": 9,
        "operation": "shared-model", "transcript": "secret", "payload": {"arbitrary": True}})
    assert row["attributes"] == {"queue_depth": 9, "operation": "shared-model"}
    assert "transcript" not in json.dumps(row, default=str)
