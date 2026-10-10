"""Process-local backend workflow and run lifecycle dependencies."""

from __future__ import annotations

import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from speech_to_text.backend.profile_store import SQLiteProfileStore
from speech_to_text.workflows.file_replay_stress import run_file_replay_stress
from speech_to_text.workflows.transcribe_match_forward import TranscriptionService


class RunRegistryFullError(RuntimeError):
    """No terminal run can be evicted to admit a new workflow."""


class MicrophoneLimitError(RuntimeError):
    """The process has reached its concurrent microphone-session limit."""


class StressTestConflictError(RuntimeError):
    """A stress matrix conflicts with another stress or microphone workflow."""


def _utc_timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class RunRecord:
    workflow_id: str
    kind: str
    status: str
    keywords: tuple[str, ...]
    created_at: float = field(default_factory=time.time)
    transcript: str | None = None
    matches: list[dict] | None = None
    latest_rtf: float | None = None
    first_queued_at: str | None = None
    last_response_at: str | None = None
    error: str | None = None
    profile_id: str | None = None
    profile_name: str | None = None
    device: str | None = None
    execution_mode: str | None = None
    silence_threshold: float | None = None
    model: str | None = None
    runtime: str | None = None
    events: deque = field(default_factory=deque)
    next_cursor: int = 1
    lock: threading.RLock = field(default_factory=threading.RLock)
    workflow: object | None = None


@dataclass
class StressTestRecord:
    stress_test_id: str
    status: str
    model: str
    runtime: str
    precision: str
    created_at: float = field(default_factory=time.time)
    report: dict | None = None
    error: str | None = None
    events: deque = field(default_factory=deque)
    next_cursor: int = 1
    lock: threading.RLock = field(default_factory=threading.RLock)


class BackendRuntime:
    """Own a workflow service, worker pool, and bounded in-memory run registry."""

    def __init__(
        self,
        *,
        event_limit=1000,
        max_runs=256,
        max_microphone_runs=16,
        worker_count=4,
        workflow_service=None,
        profile_store=None,
        stress_workflow=None,
    ):
        if event_limit < 1 or max_runs < 1 or max_microphone_runs < 1:
            raise ValueError("event_limit and run limits must be positive integers")
        self.event_limit = event_limit
        self.max_runs = max_runs
        self.max_microphone_runs = max_microphone_runs
        self.max_stress_runs = max_runs
        self.registry_lock = threading.RLock()
        self.profile_run_start_lock = threading.RLock()
        self.stress_start_lock = threading.RLock()
        self.workflow_service = (
            TranscriptionService() if workflow_service is None else workflow_service
        )
        self.profile_store = (
            SQLiteProfileStore() if profile_store is None else profile_store
        )
        self.stress_workflow = (
            run_file_replay_stress if stress_workflow is None else stress_workflow
        )
        self.runs: dict[str, RunRecord] = {}
        self.stress_runs: dict[str, StressTestRecord] = {}
        self.executor = ThreadPoolExecutor(
            max_workers=worker_count, thread_name_prefix="speech-api"
        )

    def add_run(self, record):
        with self.registry_lock:
            if record.kind == "microphone" and any(
                stress_record.status in {"queued", "running"}
                for stress_record in self.stress_runs.values()
            ):
                raise StressTestConflictError("a stress test is queued or running")
            active_microphones = sum(
                old_record.kind == "microphone"
                and old_record.status in {"starting", "recording", "stopping"}
                for old_record in self.runs.values()
            )
            if (
                record.kind == "microphone"
                and active_microphones >= self.max_microphone_runs
            ):
                raise MicrophoneLimitError("concurrent microphone limit reached")
            if len(self.runs) >= self.max_runs:
                terminal = {"completed", "stopped", "failed"}
                old_id = next(
                    (
                        workflow_id
                        for workflow_id, old_record in self.runs.items()
                        if old_record.status in terminal
                    ),
                    None,
                )
                if old_id is None:
                    raise RunRegistryFullError("workflow registry is full")
                del self.runs[old_id]
            self.runs[record.workflow_id] = record

    def add_stress_test(self, record):
        """Atomically enforce stress exclusivity and reserve a bounded record."""
        with self.registry_lock:
            with self.stress_start_lock:
                if any(
                    stress_record.status in {"queued", "running"}
                    for stress_record in self.stress_runs.values()
                ):
                    raise StressTestConflictError(
                        "a stress test is already queued or running"
                    )
                if any(
                    run.kind == "microphone"
                    and run.status in {"starting", "recording", "stopping"}
                    for run in self.runs.values()
                ):
                    raise StressTestConflictError("a microphone workflow is active")
                if len(self.stress_runs) >= self.max_stress_runs:
                    terminal = {"completed", "failed"}
                    old_id = next(
                        (
                            stress_test_id
                            for stress_test_id, old_record in self.stress_runs.items()
                            if old_record.status in terminal
                        ),
                        None,
                    )
                    if old_id is None:
                        raise RunRegistryFullError("stress-test registry is full")
                    del self.stress_runs[old_id]
                self.stress_runs[record.stress_test_id] = record

    def remove_stress_test(self, stress_test_id):
        with self.registry_lock:
            self.stress_runs.pop(stress_test_id, None)

    def get_stress_test(self, stress_test_id):
        with self.registry_lock:
            return self.stress_runs.get(stress_test_id)

    def remove_run(self, workflow_id):
        with self.registry_lock:
            self.runs.pop(workflow_id, None)

    def get_run(self, workflow_id):
        with self.registry_lock:
            return self.runs.get(workflow_id)

    def get_active_profile_run(self, profile_id):
        """Return the active microphone run for a saved profile, if one exists."""
        active_statuses = {"starting", "recording", "stopping"}
        with self.registry_lock:
            records = tuple(self.runs.values())
        for record in records:
            if record.kind != "microphone" or record.profile_id != profile_id:
                continue
            with record.lock:
                if record.status in active_statuses:
                    return record
        return None

    def append_event(self, record, event):
        with record.lock:
            item = {**event, "cursor": record.next_cursor}
            if "timestamp" not in item:
                item["timestamp"] = _utc_timestamp()
            record.events.append(item)
            record.next_cursor += 1
            while len(record.events) > self.event_limit:
                record.events.popleft()
            return item

    def append_stress_event(self, record, event):
        with record.lock:
            item = {**event, "cursor": record.next_cursor}
            if "timestamp" not in item:
                item["timestamp"] = _utc_timestamp()
            record.events.append(item)
            record.next_cursor += 1
            while len(record.events) > self.event_limit:
                record.events.popleft()
            return item

    def shutdown(self):
        with self.registry_lock:
            microphone_workflows = [
                record.workflow
                for record in self.runs.values()
                if record.kind == "microphone"
                and record.workflow is not None
                and record.status not in {"completed", "stopped", "failed"}
            ]
        for workflow in microphone_workflows:
            try:
                workflow.stop(timeout=30)
            except Exception:
                pass
        self.executor.shutdown(wait=True, cancel_futures=False)
        try:
            self.workflow_service.close()
        finally:
            close = getattr(self.profile_store, "close", None)
            if callable(close):
                close()

    def list_runs(self):
        with self.registry_lock:
            records = list(self.runs.values())
        return [
            run_snapshot(record)
            for record in sorted(
                records,
                key=lambda record: (record.created_at, record.workflow_id),
                reverse=True,
            )
        ]


def run_snapshot(record):
    with record.lock:
        return {
            "workflow_id": record.workflow_id,
            "kind": record.kind,
            "status": record.status,
            "keywords": list(record.keywords),
            "profile_id": record.profile_id,
            "profile_name": record.profile_name,
            "device": record.device,
            "execution_mode": record.execution_mode,
            "silence_threshold": record.silence_threshold,
            "model": record.model,
            "runtime": record.runtime,
            "transcript": record.transcript,
            "matches": record.matches,
            "latest_rtf": record.latest_rtf,
            "first_queued_at": record.first_queued_at,
            "last_response_at": record.last_response_at,
            "error": record.error,
            "created_at": record.created_at,
        }


def events_after(record, after):
    with record.lock:
        latest = record.next_cursor - 1
        oldest = record.events[0]["cursor"] if record.events else record.next_cursor
        if after < oldest - 1:
            return None, oldest
        if after > latest:
            return None, latest + 1
        events = [event for event in record.events if event["cursor"] > after]
        cursor = events[-1]["cursor"] if events else after
        return {
            "workflow_id": record.workflow_id,
            "events": events,
            "next_cursor": cursor,
        }, oldest


def stress_test_snapshot(record):
    with record.lock:
        return {
            "stress_test_id": record.stress_test_id,
            "status": record.status,
            "model": record.model,
            "runtime": record.runtime,
            "precision": record.precision,
            "report": record.report,
            "error": record.error,
            "created_at": record.created_at,
        }


def stress_events_after(record, after):
    with record.lock:
        latest = record.next_cursor - 1
        oldest = record.events[0]["cursor"] if record.events else record.next_cursor
        if after < oldest - 1:
            return None, oldest
        if after > latest:
            return None, latest + 1
        events = [event for event in record.events if event["cursor"] > after]
        cursor = events[-1]["cursor"] if events else after
        return {
            "stress_test_id": record.stress_test_id,
            "events": events,
            "next_cursor": cursor,
        }, oldest
