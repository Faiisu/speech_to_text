"""Deterministic backend runtime for browser-level profile lifecycle tests."""

from __future__ import annotations

import atexit
import queue
import shutil
import tempfile
import threading
import time
from pathlib import Path

from speech_to_text.backend.app import create_app
from speech_to_text.backend.dependencies import BackendRuntime
from speech_to_text.backend.profile_store import SQLiteProfileStore


_database_directory = Path(tempfile.mkdtemp(prefix="speech-to-text-e2e-"))
atexit.register(shutil.rmtree, _database_directory, ignore_errors=True)


class FakeMicrophoneWorkflow:
    def __init__(self, workflow_id: str):
        self.output_queue: queue.Queue[dict] = queue.Queue()
        self.workflow_id = workflow_id
        self.stopped = threading.Event()
        threading.Thread(target=self._emit_results, daemon=True).start()

    def _emit_results(self):
        time.sleep(0.2)
        self.output_queue.put(
            {
                "type": "transcript",
                "source_id": self.workflow_id,
                "sequence": 1,
                "text": "สวัสดีครับ",
            }
        )
        self.output_queue.put(
            {
                "type": "match_results",
                "source_id": self.workflow_id,
                "matches": [{"keyword": "สวัสดี", "count": 1}],
            }
        )

    def stop(self, timeout=30):
        if not self.stopped.is_set():
            self.stopped.set()
            self.output_queue.put(
                {
                    "type": "completed",
                    "source_id": self.workflow_id,
                    "status": "stopped",
                }
            )

    def wait(self, timeout=None):
        if not self.stopped.wait(timeout):
            raise TimeoutError


class FakeWorkflowService:
    default_model = "turbo"
    default_runtime = "openvino-gpu"

    @staticmethod
    def runtime_options():
        return [
            {"key": key, "compatible": True, "ready": key == "openvino-gpu", "precision_options": ["int8"], "reason": None if key == "openvino-gpu" else "Runtime dependencies are unavailable"}
            for key in ("openvino-gpu", "openvino-cpu", "ctranslate2")
        ]

    def list_models(self):
        return {
            "default_model": self.default_model,
            "models": [
                {"key": key, "display_name": key.title(), "installed": True, "ready": True}
                | {"runtimes": self.runtime_options()}
                for key in ("turbo", "small", "tiny")
            ],
        }

    def validate_model_key(self, model):
        if model not in {"turbo", "small", "tiny"}:
            from speech_to_text.workflows.transcribe_match_forward import WorkflowConfigurationError
            raise WorkflowConfigurationError(f"Unknown model key {model!r}")

    def validate_model_runtime(self, model, runtime):
        self.validate_model_key(model)
        if runtime not in {item["key"] for item in self.runtime_options()}:
            from speech_to_text.workflows.transcribe_match_forward import WorkflowConfigurationError
            raise WorkflowConfigurationError(f"Unsupported runtime {runtime!r}")

    def validate_keywords(self, keywords):
        return tuple(keyword.strip() for keyword in keywords if keyword.strip())

    def list_microphones(self):
        return [
            {
                "name": "E2E Studio Mic",
                "selectable": True,
                "max_input_channels": 1,
                "default_samplerate": 16000,
                "is_default": True,
            }
        ]

    def start_microphone(self, device, keywords, flow_config, *, execution_mode, model=None, runtime=None):
        return FakeMicrophoneWorkflow(flow_config["source_id"])

    def close(self):
        pass


runtime = BackendRuntime(
    workflow_service=FakeWorkflowService(),
    profile_store=SQLiteProfileStore(_database_directory / "profiles.sqlite3"),
)
app = create_app(runtime=runtime)
