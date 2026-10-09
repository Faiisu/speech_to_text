"""The sampler records only explicit feature-owned process descriptors."""

from datetime import datetime, timezone
import multiprocessing
from queue import Queue

from speech_to_text.features.system_observability.sampler import ObservationSampler, ProcessDescriptor
from speech_to_text.features.model_deployment.process_topology import _enqueue_measurement


class Writer:
    def __init__(self):
        self.samples = []
        self.events = []
        self.dropped_count = 0
        self.write_failures = 0
        self.last_error = None
        self.queue_depth = 0

    def publish_sample(self, kind, payload):
        self.samples.append((kind, payload))
        return True

    def publish_event(self, event):
        self.events.append(event)
        return True

    def publish_measurement(self, record):
        self.samples.append(("measurement", record))
        return True


class Reader:
    def __init__(self):
        self.processes = []

    def host(self):
        return {"recorded_at": datetime.now(timezone.utc), "statuses": {"cpu": {"status":"unsupported","reason":"test"}}}

    def process(self, descriptor):
        self.processes.append(descriptor.pid)
        return {"recorded_at": datetime.now(timezone.utc), "pid": descriptor.pid,
            "feature_id": descriptor.feature_id, "role": descriptor.role, "source_id": descriptor.source_id}


def test_sampler_persists_main_and_explicit_provider_pids_only():
    writer, reader = Writer(), Reader()
    descriptor = ProcessDescriptor("feature-x", "worker", "source-a", 98765)
    sampler = ObservationSampler(writer, reader=reader,
        providers=(("feature-x", object(), lambda _state: [descriptor]),))
    sampler.sample_once()
    rows = [row for kind, row in writer.samples if kind == "process_sample"]
    assert {row["pid"] for row in rows} == {__import__("os").getpid(), 98765}
    assert set(reader.processes) == {__import__("os").getpid(), 98765}
    assert any(kind == "host_sample" for kind, _row in writer.samples)
    assert any(kind == "writer_health" for kind, _row in writer.samples)


def test_full_process_measurement_queue_drops_without_blocking_and_counts_loss():
    measurements = Queue(maxsize=1)
    dropped = multiprocessing.Value("Q", 0)
    assert _enqueue_measurement(measurements, dropped, {"sequence": 1})
    assert not _enqueue_measurement(measurements, dropped, {"sequence": 2})
    assert dropped.value == 1
    assert measurements.get_nowait() == {"sequence": 1}
