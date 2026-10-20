"""Deterministic backend runtime for browser-level profile lifecycle tests."""

from __future__ import annotations

import atexit
import queue
import shutil
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

from speech_to_text.backend.app import create_app
from speech_to_text.backend.dependencies import BackendRuntime
from speech_to_text.backend.profile_store import SQLiteProfileStore
from speech_to_text.workflows.audio_recording import RecordingService
from speech_to_text.features.model_deployment import load_model
from speech_to_text.features.model_deployment.live_audio import AudioMonitor
from speech_to_text.workflows.transcribe_match_forward import TranscriptionService
from speech_to_text.workflows.transcribe_match_forward import service as transcription_service_module


class FakeDecoderRuntime:
    def transcribe(self, audio, *, language, decoding_options):
        return "Hello there" if language == "en" else "สวัสดีครับ"

    def close(self):
        pass


transcription_service_module.load_model = lambda config: load_model(
    config, runtime_factory=lambda _config: FakeDecoderRuntime()
)


_database_directory = Path(tempfile.mkdtemp(prefix="speech-to-text-e2e-"))
atexit.register(shutil.rmtree, _database_directory, ignore_errors=True)


class FakeMicrophoneWorkflow:
    def __init__(self, workflow_id: str, *, source_type: str = "microphone"):
        self.output_queue: queue.Queue[dict] = queue.Queue()
        self.workflow_id = workflow_id
        self.source_type = source_type
        self.stopped = threading.Event()
        self.audio_monitor = AudioMonitor()
        threading.Thread(target=self._emit_audio, daemon=True).start()
        threading.Thread(target=self._emit_results, daemon=True).start()

    def _emit_audio(self):
        samples = np.sin(np.arange(1600) * 2 * np.pi * 440 / 16000).astype(np.float32) * 0.05
        while not self.stopped.wait(0.1):
            self.audio_monitor.publish(samples)
        self.audio_monitor.close()

    def subscribe_audio(self):
        return self.audio_monitor.subscribe()

    def unsubscribe_audio(self, listener):
        self.audio_monitor.unsubscribe(listener)

    def _emit_results(self):
        time.sleep(0.2)
        self.output_queue.put(
            {
                "type": "transcript",
                "source_id": self.workflow_id,
                "sequence": 1,
                "text": "สวัสดีครับ (IPCAM test source)" if self.source_type == "rtsp" else "สวัสดีครับ",
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


class FakeWorkflowService(TranscriptionService):

    @staticmethod
    def runtime_options():
        return [
            {"key": key, "compatible": True, "ready": key in {"openvino-gpu", "openvino-cpu"}, "precision_options": ["int8"], "reason": None if key in {"openvino-gpu", "openvino-cpu"} else "Runtime dependencies are unavailable"}
            for key in ("openvino-gpu", "openvino-cpu", "ctranslate2")
        ]

    def __init__(self):
        super().__init__()

    def list_models(self):
        return {
            "default_model": self.default_model,
            "default_runtime": self.default_runtime,
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

    def start_microphone(self, device, keywords, flow_config, *, execution_mode, model=None, runtime=None, audio_source_factory=None):
        source_type = "rtsp" if audio_source_factory is not None else "microphone"
        return FakeMicrophoneWorkflow(flow_config["source_id"], source_type=source_type)

class FakeRecordingAudioSource:
    sample_rate = 16000
    channels = 1
    device_name = "E2E Studio Mic"

    def __init__(self, *, on_audio, **_):
        self.on_audio = on_audio

    def start(self):
        self.on_audio(np.full(16000, 0.5, dtype=np.float32))

    def stop(self):
        pass


recordings_directory = _database_directory / "recordings"
recording_service = RecordingService(
    output_directory=recordings_directory,
    audio_source_factory=FakeRecordingAudioSource,
)


runtime = BackendRuntime(
    workflow_service=FakeWorkflowService(),
    profile_store=SQLiteProfileStore(_database_directory / "profiles.sqlite3"),
    recording_service=recording_service,
)
app = create_app(runtime=runtime)
