"""Bounded asynchronous persistence for safe operational records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
import re
from queue import Empty, Full, Queue
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)
_SAFE_EVENT_ATTRIBUTES = {"code", "role", "state", "status", "queue_depth", "dropped_count",
                         "attempt", "operation", "topology", "duration_seconds", "retryable"}
_IDENTIFIER = re.compile(r"[^A-Za-z0-9_.:-]")


def _identifier(value, *, limit=160):
    if value is None:
        return None
    return _IDENTIFIER.sub("_", str(value))[:limit]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | str | None) -> datetime:
    if value is None:
        return utc_now()
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def make_measurement(*, feature_id: str, operation: str, pid: int | None = None,
                     source_id: str | None = None, sequence: int | None = None,
                     elapsed_seconds: float | None = None, audio_seconds: float | None = None,
                     inference_seconds: float | None = None, status: str = "completed",
                     recorded_at: datetime | str | None = None, attributes: dict | None = None) -> dict:
    """Create the stable feature measurement contract; never include inference payloads."""
    record = {"kind": "measurement", "recorded_at": _timestamp(recorded_at),
              "feature_id": _identifier(feature_id), "operation": _identifier(operation),
              "pid": int(pid if pid is not None else os.getpid()), "source_id": source_id,
              "sequence": sequence, "elapsed_seconds": elapsed_seconds,
              "audio_seconds": audio_seconds, "inference_seconds": inference_seconds,
              "rtf": (float(inference_seconds) / float(audio_seconds)
                      if audio_seconds and inference_seconds is not None else None),
              "status": str(status), "attributes": _safe_attributes(attributes)}
    return record


def make_event(*, event_name: str, severity: str = "info", service: str = "speech-to-text",
               feature_id: str | None = None, pid: int | None = None, source_id: str | None = None,
               recorded_at: datetime | str | None = None, attributes: dict | None = None) -> dict:
    """Create a safe event row. Free-form messages and unknown attributes are discarded."""
    if severity not in {"debug", "info", "warning", "error"}:
        raise ValueError("severity must be debug, info, warning, or error")
    return {"kind": "event", "recorded_at": _timestamp(recorded_at),
              "severity": severity, "event_name": _identifier(event_name, limit=120),
            "service": _identifier(service, limit=120), "feature_id": _identifier(feature_id),
            "pid": int(pid if pid is not None else os.getpid()), "source_id": _identifier(source_id),
            "attributes": _safe_attributes(attributes)}


def _safe_attributes(attributes: dict | None) -> dict:
    if not isinstance(attributes, dict):
        return {}
    result = {}
    for key, value in attributes.items():
        if key not in _SAFE_EVENT_ATTRIBUTES or isinstance(value, (dict, list, bytes)):
            continue
        if value is None or isinstance(value, (str, int, float, bool)):
            result[key] = value[:160] if isinstance(value, str) else value
    return result


@dataclass(frozen=True)
class WriterConfig:
    database_url: str
    queue_capacity: int = 4096
    batch_size: int = 100
    flush_interval_seconds: float = 1.0
    shutdown_timeout_seconds: float = 5.0
    max_retries: int = 3


class TelemetryWriter:
    """Non-blocking publisher backed by one bounded queue and one database thread."""

    def __init__(self, config: WriterConfig, *, connect=None):
        if config.queue_capacity < 1 or config.batch_size < 1:
            raise ValueError("queue_capacity and batch_size must be positive")
        self.config = config
        self._connect = connect or _connect
        self._queue: Queue = Queue(maxsize=config.queue_capacity)
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self.dropped_count = 0
        self.write_failures = 0
        self.last_error: str | None = None

    @classmethod
    def from_environment(cls, *, connect=None):
        url = os.getenv("SPEECH_TO_TEXT_TELEMETRY_DATABASE_URL", "").strip()
        if not url:
            return None
        return cls(WriterConfig(
            database_url=url,
            queue_capacity=int(os.getenv("SPEECH_TO_TEXT_TELEMETRY_QUEUE_CAPACITY", "4096")),
            batch_size=int(os.getenv("SPEECH_TO_TEXT_TELEMETRY_BATCH_SIZE", "100")),
            shutdown_timeout_seconds=float(os.getenv("SPEECH_TO_TEXT_TELEMETRY_SHUTDOWN_TIMEOUT_SECONDS", "5"))),
            connect=connect)

    @property
    def queue_depth(self):
        return self._queue.qsize()

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="stt-telemetry-writer", daemon=True)
        self._thread.start()
        self.publish_event(make_event(event_name="telemetry_writer_started"))

    def publish(self, record: dict) -> bool:
        """Queue a record without waiting for database I/O; return false on overflow/stopping."""
        if self._stopping.is_set():
            self._drop("writer_stopping")
            return False
        try:
            self._queue.put_nowait(dict(record))
            return True
        except Full:
            self._drop("queue_saturated")
            return False

    def publish_measurement(self, record: dict) -> bool:
        return self.publish(_normalize_measurement(record))

    def publish_event(self, record: dict) -> bool:
        return self.publish(make_event(event_name=record.get("event_name", "unknown"),
            severity=record.get("severity", "info"), service=record.get("service", "speech-to-text"),
            feature_id=record.get("feature_id"), pid=record.get("pid"), source_id=record.get("source_id"),
            recorded_at=record.get("recorded_at"), attributes=record.get("attributes")))

    def publish_sample(self, kind: str, payload: dict) -> bool:
        if kind not in {"host_sample", "process_sample", "writer_health"}:
            raise ValueError("unsupported telemetry sample kind")
        row = {"kind": kind, "recorded_at": _timestamp(payload.get("recorded_at")), **payload}
        return self.publish(row)

    def close(self, timeout: float | None = None):
        self._stopping.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(self.config.shutdown_timeout_seconds if timeout is None else timeout)
        if thread.is_alive():
            logger.warning("Telemetry writer shutdown timed out with %s queued records", self.queue_depth)
        else:
            self._thread = None

    def _drop(self, reason):
        with self._lock:
            self.dropped_count += 1
            dropped = self.dropped_count
        logger.warning("Telemetry record dropped: %s (total=%d)", reason, dropped)

    def _run(self):
        connection = None
        pending = []
        last_flush = time.monotonic()
        while not self._stopping.is_set() or pending or not self._queue.empty():
            try:
                item = self._queue.get(timeout=min(.25, self.config.flush_interval_seconds))
                pending.append(item)
            except Empty:
                pass
            due = len(pending) >= self.config.batch_size or (
                pending and time.monotonic() - last_flush >= self.config.flush_interval_seconds)
            if not due:
                continue
            batch, pending = pending, []
            success = False
            for attempt in range(self.config.max_retries + 1):
                try:
                    if connection is None or getattr(connection, "closed", False):
                        connection = self._connect(self.config.database_url)
                    _write_batch(connection, batch)
                    self.last_error = None
                    success = True
                    break
                except Exception as exc:  # telemetry must never escape into the service path
                    self.last_error = type(exc).__name__
                    with self._lock:
                        self.write_failures += 1
                    logger.warning("Telemetry batch write failed (%s)", type(exc).__name__)
                    try:
                        if connection is not None:
                            connection.close()
                    except Exception:
                        pass
                    connection = None
                    if attempt < self.config.max_retries and not self._stopping.is_set():
                        time.sleep(min(.25 * (2 ** attempt), 2.0))
            if not success:
                with self._lock:
                    self.dropped_count += len(batch)
            for _ in batch:
                self._queue.task_done()
            last_flush = time.monotonic()
        try:
            if connection is not None:
                connection.close()
        except Exception:
            pass


def _normalize_measurement(record: dict) -> dict:
    timestamp = _timestamp(record.get("completed_at") or record.get("recorded_at"))
    audio = record.get("audio_seconds")
    inference = record.get("inference_seconds")
    return make_measurement(feature_id=record.get("feature_id", "unknown"),
        operation=record.get("operation", "unknown"), pid=record.get("pid"),
        source_id=record.get("source_id"), sequence=record.get("sequence"),
        elapsed_seconds=record.get("elapsed_seconds", inference), audio_seconds=audio,
        inference_seconds=inference, status=record.get("status", "completed"),
        recorded_at=timestamp, attributes=record.get("attributes"))


def _connect(url):
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("Install the telemetry extra to enable database persistence") from exc
    return psycopg.connect(url, autocommit=False)


def _write_batch(connection, batch):
    grouped: dict[str, list[dict]] = {}
    for row in batch:
        grouped.setdefault(row["kind"], []).append(row)
    with connection.cursor() as cursor:
        for kind, rows in grouped.items():
            if kind == "measurement":
                cursor.executemany("""INSERT INTO operation_measurements
                    (recorded_at, feature_id, operation, pid, source_id, sequence, elapsed_seconds,
                     audio_seconds, inference_seconds, rtf, status, attributes)
                    VALUES (%(recorded_at)s,%(feature_id)s,%(operation)s,%(pid)s,%(source_id)s,%(sequence)s,
                    %(elapsed_seconds)s,%(audio_seconds)s,%(inference_seconds)s,%(rtf)s,%(status)s,%(attributes)s)""",
                    [_json_row(row, "attributes") for row in rows])
            elif kind == "event":
                cursor.executemany("""INSERT INTO service_events
                    (recorded_at,severity,event_name,service,feature_id,pid,source_id,attributes)
                    VALUES (%(recorded_at)s,%(severity)s,%(event_name)s,%(service)s,%(feature_id)s,
                    %(pid)s,%(source_id)s,%(attributes)s)""", [_json_row(row, "attributes") for row in rows])
            elif kind == "host_sample":
                cursor.executemany("""INSERT INTO host_samples
                    (recorded_at,cpu_percent,memory_used_bytes,memory_total_bytes,swap_used_bytes,swap_total_bytes,
                     storage_used_bytes,storage_total_bytes,uptime_seconds,load_average,statuses)
                    VALUES (%(recorded_at)s,%(cpu_percent)s,%(memory_used_bytes)s,%(memory_total_bytes)s,
                    %(swap_used_bytes)s,%(swap_total_bytes)s,%(storage_used_bytes)s,%(storage_total_bytes)s,
                    %(uptime_seconds)s,%(load_average)s,%(statuses)s)""", [_json_row(row, "load_average", "statuses") for row in rows])
            elif kind == "process_sample":
                cursor.executemany("""INSERT INTO process_samples
                    (recorded_at,service,feature_id,role,source_id,pid,lifecycle_state,cpu_percent,rss_bytes,
                     memory_percent,thread_count,started_at,statuses)
                    VALUES (%(recorded_at)s,%(service)s,%(feature_id)s,%(role)s,%(source_id)s,%(pid)s,
                    %(lifecycle_state)s,%(cpu_percent)s,%(rss_bytes)s,%(memory_percent)s,%(thread_count)s,
                    %(started_at)s,%(statuses)s)""", [_json_row(row, "statuses") for row in rows])
            elif kind == "writer_health":
                cursor.executemany("""INSERT INTO writer_health
                    (recorded_at,queue_depth,dropped_count,write_failures,last_error)
                    VALUES (%(recorded_at)s,%(queue_depth)s,%(dropped_count)s,%(write_failures)s,%(last_error)s)""",
                    rows)
            else:
                raise ValueError(f"Unknown telemetry record kind: {kind}")
    connection.commit()


def _json_row(row, *keys):
    result = dict(row)
    for key in keys:
        value = result.get(key)
        if value is None:
            result[key] = None
        else:
            try:
                from psycopg.types.json import Jsonb
                result[key] = Jsonb(value)
            except ImportError:
                result[key] = json.dumps(value)
    return result
