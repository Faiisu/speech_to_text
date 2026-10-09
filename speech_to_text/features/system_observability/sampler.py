"""Two-second sampler for aggregate host state and explicitly owned processes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import threading
import time

from .writer import make_event


@dataclass(frozen=True)
class ProcessDescriptor:
    feature_id: str
    role: str
    source_id: str
    pid: int
    service: str = "speech-to-text"


class PsutilReader:
    def __init__(self, model_volume_path: str | None = None):
        self.model_volume_path = model_volume_path or os.getenv("SPEECH_TO_TEXT_MODEL_VOLUME_PATH", str(Path.cwd() / "models"))
        self._psutil = None
        self._boot_time = None
        self._cpu_warmed = False
        self._process_cpu_warmed = set()

    def _load(self):
        if self._psutil is None:
            import psutil
            self._psutil = psutil
            self._boot_time = psutil.boot_time()
        return self._psutil

    def host(self):
        now = datetime.now(timezone.utc)
        try:
            psutil = self._load()
        except Exception as exc:
            reason = type(exc).__name__
            return {"kind": "host_sample", "recorded_at": now, "statuses": {
                key: {"status": "unavailable", "reason": reason}
                for key in ("cpu", "memory", "swap", "storage", "uptime", "load_average")}}
        statuses = {}
        try:
            cpu = psutil.cpu_percent(interval=None) if self._cpu_warmed else None
            self._cpu_warmed = True
            if cpu is None:
                statuses["cpu"] = {"status": "unavailable", "reason": "initial_sample"}
        except Exception as exc:
            cpu = None
            statuses["cpu"] = {"status": "unavailable", "reason": type(exc).__name__}
        try:
            memory = psutil.virtual_memory()
            memory_used, memory_total = int(memory.used), int(memory.total)
        except Exception as exc:
            memory_used = memory_total = None
            statuses["memory"] = {"status": "unavailable", "reason": type(exc).__name__}
        try:
            swap = psutil.swap_memory()
            swap_used, swap_total = int(swap.used), int(swap.total)
        except Exception as exc:
            swap_used = swap_total = None
            statuses["swap"] = {"status": "unavailable", "reason": type(exc).__name__}
        try:
            disk = psutil.disk_usage(self.model_volume_path)
            storage_used, storage_total = int(disk.used), int(disk.total)
        except Exception as exc:
            storage_used = storage_total = None
            statuses["storage"] = {"status": "unavailable", "reason": type(exc).__name__}
        try:
            uptime = max(0.0, time.time() - self._boot_time)
        except Exception as exc:
            uptime = None
            statuses["uptime"] = {"status": "unavailable", "reason": type(exc).__name__}
        try:
            loads = psutil.getloadavg()
            load_average = {"1m": loads[0], "5m": loads[1], "15m": loads[2]}
        except Exception as exc:
            load_average = None
            statuses["load_average"] = {"status": "unsupported", "reason": type(exc).__name__}
        return {"kind": "host_sample", "recorded_at": now, "cpu_percent": cpu,
                "memory_used_bytes": memory_used, "memory_total_bytes": memory_total,
                "swap_used_bytes": swap_used, "swap_total_bytes": swap_total,
                "storage_used_bytes": storage_used, "storage_total_bytes": storage_total,
                "uptime_seconds": uptime, "load_average": load_average, "statuses": statuses}

    def process(self, descriptor: ProcessDescriptor):
        now = datetime.now(timezone.utc)
        row = {"kind": "process_sample", "recorded_at": now, "service": descriptor.service,
               "feature_id": descriptor.feature_id, "role": descriptor.role,
               "source_id": descriptor.source_id, "pid": int(descriptor.pid),
               "lifecycle_state": "running", "cpu_percent": None, "rss_bytes": None,
               "memory_percent": None, "thread_count": None, "started_at": None, "statuses": {}}
        try:
            psutil = self._load()
            process = psutil.Process(descriptor.pid)
            with process.oneshot():
                sampled_cpu = process.cpu_percent(interval=None)
                if descriptor.pid in self._process_cpu_warmed:
                    row["cpu_percent"] = sampled_cpu
                else:
                    self._process_cpu_warmed.add(descriptor.pid)
                    row["statuses"]["cpu"] = {"status": "unavailable", "reason": "initial_sample"}
                info = process.memory_info()
                row["rss_bytes"] = int(info.rss)
                row["memory_percent"] = process.memory_percent()
                row["thread_count"] = process.num_threads()
                row["started_at"] = datetime.fromtimestamp(process.create_time(), timezone.utc)
                row["lifecycle_state"] = process.status()
        except Exception as exc:
            row["lifecycle_state"] = "unavailable"
            row["statuses"] = {"process": {"status": "unavailable", "reason": type(exc).__name__}}
        return row


class ObservationSampler:
    """Collect samples without walking the machine process table."""

    def __init__(self, writer, *, providers=(), measurement_providers=(), reader=None, interval_seconds=2.0):
        self.writer = writer
        self.providers = tuple(providers)
        self.measurement_providers = tuple(measurement_providers)
        self.reader = reader or PsutilReader()
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread = None
        self._observed_dropped = 0

    def start(self):
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="stt-host-sampler", daemon=True)
        self._thread.start()
        self.writer.publish_event(make_event(event_name="resource_sampler_started", attributes={"state": "running"}))

    def stop(self, timeout=3.0):
        self._stop.set()
        thread = self._thread
        if thread:
            thread.join(timeout)
            if not thread.is_alive():
                self._thread = None
        self.sample_once()
        self.writer.publish_event(make_event(event_name="resource_sampler_stopped", attributes={"state": "stopped"}))

    def sample_once(self):
        self.writer.publish_sample("host_sample", self.reader.host())
        main_process = ProcessDescriptor(feature_id="speech-to-text-service", role="service",
            source_id="service-main", pid=os.getpid())
        self.writer.publish_sample("process_sample", self.reader.process(main_process))
        seen = set()
        for feature_id, state, provider in tuple(self.providers):
            try:
                for descriptor in provider(state):
                    key = (descriptor.feature_id, descriptor.role, descriptor.source_id, descriptor.pid)
                    if key in seen:
                        continue
                    seen.add(key)
                    self.writer.publish_sample("process_sample", self.reader.process(descriptor))
            except Exception as exc:
                self.writer.publish_event(make_event(event_name="process_sample_failed", severity="warning",
                    feature_id=feature_id, attributes={"code": type(exc).__name__}))
        for feature_id, state, provider in tuple(self.measurement_providers):
            try:
                for measurement in provider(state):
                    self.writer.publish_measurement(measurement)
                    if measurement.get("status") == "failed":
                        self.writer.publish_event(make_event(event_name="inference_chunk_failed", severity="error",
                            feature_id=measurement.get("feature_id", feature_id), pid=measurement.get("pid"),
                            source_id=measurement.get("source_id"), attributes={"operation": measurement.get("operation", "inference")}))
            except Exception as exc:
                self.writer.publish_event(make_event(event_name="measurement_drain_failed", severity="warning",
                    feature_id=feature_id, attributes={"code": type(exc).__name__}))
        if hasattr(self.writer, "queue_depth"):
            if self.writer.dropped_count > self._observed_dropped:
                self.writer.publish_event(make_event(event_name="telemetry_records_dropped", severity="warning",
                    attributes={"dropped_count": self.writer.dropped_count, "queue_depth": self.writer.queue_depth}))
                self._observed_dropped = self.writer.dropped_count
            self.writer.publish_sample("writer_health", {"recorded_at": datetime.now(timezone.utc),
                "queue_depth": self.writer.queue_depth, "dropped_count": self.writer.dropped_count,
                "write_failures": self.writer.write_failures,
                "last_error": self.writer.last_error})

    def _run(self):
        while not self._stop.is_set():
            started = time.monotonic()
            self.sample_once()
            self._stop.wait(max(0.0, self.interval_seconds - (time.monotonic() - started)))
