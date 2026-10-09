"""Spawned microphone/process IPC tests use only external boundary doubles."""

import numpy as np
import multiprocessing
import time
import pytest

from .support import collect_until_completed, texts
from queue import Empty


def test_multiprocess_device_argument_must_be_a_list_or_tuple(api):
    with pytest.raises(api.ConfigurationError, match="list or tuple"):
        api.start_multiprocess_microphone_flows("microphone-name", runtime_factory=ProcessRuntimeFactory())


class ProcessRuntime:
    def transcribe(self, audio, *, language, decoding_options):
        return f"frames-{len(audio)}"

    def close(self):
        pass


class ProcessRuntimeFactory:
    def __call__(self, config):
        return ProcessRuntime()


class ProcessAudioSource:
    sample_rate = 16000
    channels = 1

    def __init__(self, *, device, on_audio, on_error):
        self.device = device
        self.on_audio = on_audio
        self.on_error = on_error
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True
        if self.device == "overloaded":
            self.on_audio(np.full(4800, 0.3, dtype=np.float32))
        else:
            if self.device == "healthy":
                time.sleep(0.4)
            frames = 1600 if self.device == "healthy" else 2000
            self.on_audio(np.full(frames, 0.3, dtype=np.float32))

    def stop(self):
        self.stopped = True
        self.started = False


def test_spawned_capture_and_model_processes_route_ordered_partial_chunks(api):
    for topology in ("shared-model", "per-input-model"):
        group = api.start_multiprocess_microphone_flows(
            ["process-test-device"],
            model_config={},
            flow_config={"chunk_seconds": 0.1, "silence_threshold": 0},
            topology=topology,
            runtime_factory=ProcessRuntimeFactory(),
            audio_source_factory=ProcessAudioSource,
        )
        session, = group.sessions
        group.stop(timeout=10)
        events = collect_until_completed(session, timeout=2)
        assert texts(events) == ["frames-1600", "frames-400"], (topology, events, session.process.exitcode)
        assert [event["sequence"] for event in events if event["type"] == "transcript"] == [0, 1]
        assert events[-1]["status"] == "stopped"
        measurements = [event for event in events if event["type"] == "measurement"]
        assert [record["sequence"] for record in measurements] == [0, 1]
        assert all(record["source_id"] == session.source_id for record in measurements)
        inference_pid = group._model_process.pid if group._model_process is not None else session.process.pid
        assert all(record["pid"] == inference_pid for record in measurements)
        assert all(record["rtf"] >= 0 and record["completed_at"].endswith("+00:00") for record in measurements)
        assert [record["audio_seconds"] for record in measurements] == pytest.approx([.1, .025])
        assert events.index(measurements[-1]) < next(i for i, event in enumerate(events) if event["type"] == "completed")


def test_shared_ipc_routes_two_spawned_sources_independently(api):
    group = api.start_multiprocess_microphone_flows(
        ["left", "right"], model_config={},
        flow_config={"chunk_seconds": 0.1, "silence_threshold": 0},
        topology="shared-model", runtime_factory=ProcessRuntimeFactory(),
        audio_source_factory=ProcessAudioSource,
    )
    try:
        group.stop(timeout=10)
        assert len({session.source_id for session in group.sessions}) == 2
        for session in group.sessions:
            events = collect_until_completed(session, timeout=2)
            assert texts(events) == ["frames-1600", "frames-400"]
            assert all(event["source_id"] == session.source_id for event in events)
    finally:
        if group._manager is not None:
            group.stop(timeout=10)


class PerFlowRuntime:
    def transcribe(self, audio, *, language, decoding_options):
        return f"{language}:{len(audio)}"

    def close(self):
        pass


class PerFlowRuntimeFactory:
    def __call__(self, config):
        return PerFlowRuntime()


def test_shared_ipc_keeps_per_source_flow_settings_independent(api):
    group = api.start_multiprocess_microphone_flows(
        ["left", "right"], model_config={},
        flow_config={"silence_threshold": 0},
        flow_configs=[{"source_id": "thai-short", "language": "th", "chunk_seconds": 0.1},
                      {"source_id": "english-long", "language": "en", "chunk_seconds": 0.2}],
        topology="shared-model", runtime_factory=PerFlowRuntimeFactory(),
        audio_source_factory=ProcessAudioSource,
    )
    try:
        group.stop(timeout=10)
        results = {session.source_id: collect_until_completed(session, timeout=2) for session in group.sessions}
        assert texts(results["thai-short"]) == ["th:1600", "th:400"]
        assert texts(results["english-long"]) == ["en:2000"]
    finally:
        if group._manager is not None:
            group.stop(timeout=10)


class BlockingProcessRuntime:
    def __init__(self, entered, release):
        self.entered = entered
        self.release = release

    def transcribe(self, audio, *, language, decoding_options):
        if not self.entered.is_set():
            self.entered.set()
            self.release.wait(10)
            return "late-overloaded-text"
        return "healthy-text"

    def close(self):
        pass


class BlockingProcessRuntimeFactory:
    def __init__(self, entered, release):
        self.entered = entered
        self.release = release

    def __call__(self, config):
        return BlockingProcessRuntime(self.entered, self.release)


def test_shared_ipc_timeout_discards_inflight_source_and_keeps_other_source(api):
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    group = api.start_multiprocess_microphone_flows(
        ["overloaded", "healthy"],
        model_config={"queue_capacity": 1, "enqueue_timeout_seconds": 0.15},
        flow_config={"chunk_seconds": 0.1, "silence_threshold": 0},
        topology="shared-model",
        runtime_factory=BlockingProcessRuntimeFactory(entered, release),
        audio_source_factory=ProcessAudioSource,
    )
    overloaded, healthy = group.sessions
    try:
        assert entered.wait(3)
        failed = collect_until_completed(overloaded, timeout=3)
        assert texts(failed) == []
        overload = next((event for event in failed if event.get("code") == "INPUT_QUEUE_TIMEOUT"), None)
        assert overload is not None, failed
        assert overload.get("discarded_queued_chunks", 0) >= 1
        release.set()
        group.stop(timeout=10)
        surviving = collect_until_completed(healthy, timeout=2)
        assert texts(surviving) == ["healthy-text"]
        assert failed[-1]["status"] == "failed"
        assert overloaded._terminal_event.is_set()
    finally:
        release.set()
        if group._manager is not None:
            group.stop(timeout=10)
