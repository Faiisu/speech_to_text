"""Process-local backend workflow and run lifecycle dependencies."""

from __future__ import annotations

import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from speech_to_text.backend.profile_store import SQLiteProfileStore
from speech_to_text.workflows.transcribe_match_forward import TranscriptionService


class RunRegistryFullError(RuntimeError):
    """No terminal run can be evicted to admit a new workflow."""


class MicrophoneLimitError(RuntimeError):
    """The process has reached its concurrent microphone-session limit."""


@dataclass
class RunRecord:
    workflow_id: str
    kind: str
    status: str
    keywords: tuple[str, ...]
    created_at: float = field(default_factory=time.time)
    transcript: str | None = None
    matches: list[dict] | None = None
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
    ):
        if event_limit < 1 or max_runs < 1 or max_microphone_runs < 1:
            raise ValueError("event_limit and run limits must be positive integers")
        self.event_limit = event_limit
        self.max_runs = max_runs
        self.max_microphone_runs = max_microphone_runs
        self.registry_lock = threading.RLock()
        self.workflow_service = (
            TranscriptionService() if workflow_service is None else workflow_service
        )
        self.profile_store = (
            SQLiteProfileStore() if profile_store is None else profile_store
        )
        self.runs: dict[str, RunRecord] = {}
        self.executor = ThreadPoolExecutor(
            max_workers=worker_count, thread_name_prefix="speech-api"
        )

    def add_run(self, record):
        with self.registry_lock:
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

    def remove_run(self, workflow_id):
        with self.registry_lock:
            self.runs.pop(workflow_id, None)

    def get_run(self, workflow_id):
        with self.registry_lock:
            return self.runs.get(workflow_id)

    def append_event(self, record, event):
        with record.lock:
            item = {**event, "cursor": record.next_cursor}
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
