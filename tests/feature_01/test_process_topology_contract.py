"""Spawned microphone/process IPC tests use only external boundary doubles."""

import numpy as np
import multiprocessing
import threading
import time
import pytest

from .support import collect_until_completed, texts
from queue import Empty, Queue


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
        if self.device == "overflow":
            from speech_to_text.features.model_deployment.capture import AudioInputOverflow

            self.on_error(AudioInputOverflow("input overflow"))
            self.on_audio(np.full(1600, 0.3, dtype=np.float32))
            return
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
        assert all(record["queue_wait_seconds"] >= 0 for record in measurements)
        assert [record["elapsed_seconds"] for record in measurements] == pytest.approx(
            [record["queue_wait_seconds"] + record["inference_seconds"] for record in measurements]
        )
        assert [record["rtf"] for record in measurements] == pytest.approx(
            [record["elapsed_seconds"] / record["audio_seconds"] for record in measurements]
        )
        assert events.index(measurements[-1]) < next(i for i, event in enumerate(events) if event["type"] == "completed")


def test_vad_options_propagate_to_both_process_topologies_and_account_cost(api):
    for topology in ("shared-model", "per-input-model"):
        group = api.start_multiprocess_microphone_flows(
            ["vad-process-test"],
            model_config={},
            flow_config={
                "chunk_seconds": 0.1,
                "silence_threshold": 0,
                "vad": {"enabled": False, "threshold": 0.5},
            },
            flow_configs=[{"vad": {"enabled": True}}],
            topology=topology,
            runtime_factory=ProcessRuntimeFactory(),
            audio_source_factory=ProcessAudioSource,
        )
        session, = group.sessions
        try:
            group.stop(timeout=15)
            events = collect_until_completed(session, timeout=3)
            measurements = [event for event in events if event["type"] == "measurement"]
            assert measurements, (topology, events)
            assert all(record["status"] == "skipped" for record in measurements)
            assert all(record["skip_reason"] == "no_speech" for record in measurements)
            assert all(record["inference_seconds"] >= 0 for record in measurements)
            assert [record["audio_seconds"] for record in measurements] == pytest.approx([0.1, 0.025])
            assert all(record["elapsed_seconds"] == pytest.approx(record["queue_wait_seconds"] + record["inference_seconds"]) for record in measurements)
            assert all(record["rtf"] == pytest.approx(record["elapsed_seconds"] / record["audio_seconds"]) for record in measurements)
            assert all(record["pid"] == session.process.pid for record in measurements)
            telemetry = []
            while True:
                try:
                    telemetry.append(group.telemetry_queue.get_nowait())
                except Empty:
                    break
            assert len(telemetry) == len(measurements)
            assert all(item["model_inference_seconds"] == 0 for item in telemetry)
            assert texts(events) == []
        finally:
            if group._manager is not None:
                group.stop(timeout=15)


def test_shared_vad_failure_retires_accepted_work_before_terminal(api, monkeypatch):
    topology_module = __import__(
        "speech_to_text.features.model_deployment.process_topology",
        fromlist=["_capture_chunks", "_BoundedFIFO"],
    )
    decisions = iter([True, RuntimeError("detector failed")])

    def detect(_audio, _options):
        result = next(decisions)
        if isinstance(result, Exception):
            raise result
        return result, 0.01

    monkeypatch.setattr(topology_module, "detect_speech", detect)

    class TwoChunkSource:
        sample_rate = 16000
        channels = 1

        def __init__(self, *, device, on_audio, on_error):
            self.on_audio = on_audio
            self.finished = False

        def start(self):
            self.on_audio(np.full(3200, 0.3, dtype=np.float32))
            self.finished = True

        def stop(self):
            pass

    flow = api.flow_config(
        {"chunk_seconds": 0.1, "silence_threshold": 0, "vad": {"enabled": True}}
    )
    input_queue = topology_module._BoundedFIFO(4)
    model_control, output, ready, telemetry = Queue(), Queue(), Queue(), Queue()
    topology_module._capture_chunks(
        None,
        "vad-failure-source",
        flow,
        {"enqueue_timeout_seconds": 0.1},
        input_queue,
        Queue(),
        model_control,
        output,
        ready,
        telemetry,
        "shared-model",
        audio_source_factory=TwoChunkSource,
    )
    events = []
    while not output.empty():
        events.append(output.get_nowait())
    assert input_queue.qsize() == 0
    assert "vad-failure-source" in input_queue.retired
    assert model_control.get_nowait() == ("retire", "vad-failure-source")
    error, = [event for event in events if event["type"] == "error"]
    completed, = [event for event in events if event["type"] == "completed"]
    assert error["code"] == "VAD_FAILED"
    assert error["discarded_queued_chunks"] == 1
    assert completed["status"] == "failed"
    assert [event["type"] for event in events].count("completed") == 1


@pytest.mark.parametrize("policy", ["drop", "fail"])
def test_full_shared_queue_applies_overload_policy_to_vad_skip(api, monkeypatch, policy):
    topology_module = __import__(
        "speech_to_text.features.model_deployment.process_topology",
        fromlist=["_capture_chunks", "_BoundedFIFO"],
    )
    monkeypatch.setattr(topology_module, "detect_speech", lambda *_args: (False, 0.01))

    class FullForSkipFIFO(topology_module._BoundedFIFO):
        def put(self, item, timeout=None):
            if item[0] == "skip":
                from queue import Full

                raise Full
            return super().put(item, timeout)

    class OneChunkSource:
        sample_rate = 16000
        channels = 1

        def __init__(self, *, device, on_audio, on_error):
            self.on_audio = on_audio
            self.finished = False

        def start(self):
            self.on_audio(np.full(1600, 0.3, dtype=np.float32))
            self.finished = True

        def stop(self):
            pass

    flow = api.flow_config(
        {
            "chunk_seconds": 0.1,
            "silence_threshold": 0,
            "overload_policy": policy,
            "vad": {"enabled": True},
        }
    )
    input_queue = FullForSkipFIFO(4)
    model_control, output, ready, telemetry = Queue(), Queue(), Queue(), Queue()
    topology_module._capture_chunks(
        None,
        "vad-full-source",
        flow,
        {"enqueue_timeout_seconds": 0.1},
        input_queue,
        Queue(),
        model_control,
        output,
        ready,
        telemetry,
        "shared-model",
        audio_source_factory=OneChunkSource,
    )
    events = []
    while not output.empty():
        events.append(output.get_nowait())
    measurements = [event for event in events if event["type"] == "measurement"]
    errors = [event for event in events if event["type"] == "error"]
    assert measurements == []
    assert errors[0]["code"] == "INPUT_QUEUE_TIMEOUT"
    assert errors[0]["fatal"] is (policy == "fail")
    if policy == "fail":
        assert [event["status"] for event in events if event["type"] == "completed"] == ["failed"]
        assert model_control.get_nowait() == ("retire", "vad-full-source")
    else:
        assert input_queue.items[0][0] == "complete"


def test_input_overflow_is_recoverable_in_both_process_topologies(api):
    for topology in ("shared-model", "per-input-model"):
        group = api.start_multiprocess_microphone_flows(
            ["overflow"],
            model_config={},
            flow_config={"chunk_seconds": 0.1, "silence_threshold": 0},
            topology=topology,
            runtime_factory=ProcessRuntimeFactory(),
            audio_source_factory=ProcessAudioSource,
        )
        session, = group.sessions
        try:
            group.stop(timeout=10)
            events = collect_until_completed(session, timeout=2)
            diagnostic, = [event for event in events if event.get("code") == "INPUT_OVERFLOW"]
            assert diagnostic["fatal"] is False
            assert texts(events) == ["frames-1600"]
            assert events[-1]["status"] == "stopped"
        finally:
            if group._manager is not None:
                group.stop(timeout=10)


def test_unexpected_per_input_process_exit_emits_fatal_terminal_event(api):
    group = api.start_multiprocess_microphone_flows(
        ["process-test-device"],
        model_config={},
        flow_config={"chunk_seconds": 0.1, "silence_threshold": 0},
        topology="per-input-model",
        runtime_factory=ProcessRuntimeFactory(),
        audio_source_factory=ProcessAudioSource,
    )
    session, = group.sessions
    try:
        session.process.terminate()
        session.process.join(2)
        events = collect_until_completed(session, timeout=3)
        error, = [event for event in events if event.get("code") == "PROCESS_EXITED"]
        assert error["fatal"] is True
        assert "exit code" in error["message"]
        assert events[-1]["status"] == "failed"
    finally:
        if group._manager is not None:
            group.stop(timeout=10)


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
        flow_configs=[{"source_id": "short-language", "language": "th", "chunk_seconds": 0.1},
                      {"source_id": "english-long", "language": "en", "chunk_seconds": 0.2}],
        topology="shared-model", runtime_factory=PerFlowRuntimeFactory(),
        audio_source_factory=ProcessAudioSource,
    )
    try:
        group.stop(timeout=10)
        results = {session.source_id: collect_until_completed(session, timeout=2) for session in group.sessions}
        assert texts(results["short-language"]) == ["th:1600", "th:400"]
        assert texts(results["english-long"]) == ["en:2000"]
    finally:
        if group._manager is not None:
            group.stop(timeout=10)


class BlockingProcessRuntime:
    def __init__(self, entered, release, second_entered=None):
        self.entered = entered
        self.release = release
        self.second_entered = second_entered

    def transcribe(self, audio, *, language, decoding_options):
        if not self.entered.is_set():
            self.entered.set()
            self.release.wait(10)
            return "late-overloaded-text"
        if self.second_entered is not None:
            self.second_entered.set()
        return "healthy-text"

    def close(self):
        pass


class BlockingProcessRuntimeFactory:
    def __init__(self, entered, release, second_entered=None):
        self.entered = entered
        self.release = release
        self.second_entered = second_entered

    def __call__(self, config):
        return BlockingProcessRuntime(self.entered, self.release, self.second_entered)


class QueuePressureSource:
    sample_rate = 16000
    channels = 1

    def __init__(self, *, device, on_audio, on_error, allow_pressure, pressure_sent, allow_later, later_sent):
        self.on_audio = on_audio
        self.allow_pressure = allow_pressure
        self.pressure_sent = pressure_sent
        self.allow_later = allow_later
        self.later_sent = later_sent

    def start(self):
        self.on_audio(np.full(1600, 0.3, dtype=np.float32))

    def start_stream(self):
        threading.Thread(target=self._send_audio, daemon=True).start()

    def _send_audio(self):
        if not self.allow_pressure.wait(5):
            return
        self.on_audio(np.full(3200, 0.3, dtype=np.float32))
        self.pressure_sent.set()
        if self.allow_later.wait(5):
            self.on_audio(np.full(1600, 0.4, dtype=np.float32))
            self.later_sent.set()

    def stop(self):
        pass


class QueuePressureSourceFactory:
    def __init__(self, allow_pressure, pressure_sent, allow_later, later_sent):
        self.allow_pressure = allow_pressure
        self.pressure_sent = pressure_sent
        self.allow_later = allow_later
        self.later_sent = later_sent

    def __call__(self, *, device, on_audio, on_error):
        return QueuePressureSource(
            device=device,
            on_audio=on_audio,
            on_error=on_error,
            allow_pressure=self.allow_pressure,
            pressure_sent=self.pressure_sent,
            allow_later=self.allow_later,
            later_sent=self.later_sent,
        )


def test_drop_policy_preserves_flow_after_process_queue_pressure_in_both_topologies(api):
    for topology in ("shared-model", "per-input-model"):
        context = multiprocessing.get_context("spawn")
        entered, release = context.Event(), context.Event()
        allow_pressure, pressure_sent = context.Event(), context.Event()
        allow_later, later_sent = context.Event(), context.Event()
        group = api.start_multiprocess_microphone_flows(
            ["drop-pressure"],
            model_config={"queue_capacity": 1, "enqueue_timeout_seconds": 0.15},
            flow_config={
                "chunk_seconds": 0.1,
                "silence_threshold": 0,
                "overload_policy": "drop",
            },
            topology=topology,
            runtime_factory=BlockingProcessRuntimeFactory(entered, release),
            audio_source_factory=QueuePressureSourceFactory(
                allow_pressure, pressure_sent, allow_later, later_sent
            ),
        )
        session, = group.sessions
        try:
            assert entered.wait(3)
            allow_pressure.set()
            assert pressure_sent.wait(3)
            events_seen = []
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not any(
                event.get("code") == "INPUT_QUEUE_TIMEOUT" for event in events_seen
            ):
                try:
                    events_seen.append(session.result_queue.get(timeout=0.1))
                except Empty:
                    pass
            diagnostics = [
                event for event in events_seen
                if event.get("code") == "INPUT_QUEUE_TIMEOUT"
            ]
            assert diagnostics, (topology, events_seen)
            diagnostic = diagnostics[0]
            assert diagnostic["sequence"] >= 1
            assert diagnostic["fatal"] is False
            release.set()
            while not any(
                event.get("type") == "transcript" and event.get("sequence") == 1
                for event in events_seen
            ):
                event = session.result_queue.get(timeout=3)
                events_seen.append(event)
            allow_later.set()
            assert later_sent.wait(3)
            group.stop(timeout=10)
            events = [*events_seen, *collect_until_completed(session, timeout=2)]
            assert "healthy-text" in texts(events)
            later = [
                event for event in events
                if event.get("type") == "transcript" and event.get("sequence") == 3
            ]
            assert later, (topology, events)
            assert later[0]["sequence"] > diagnostic["sequence"]
            assert events[-1]["status"] == "stopped"
        finally:
            release.set()
            if group._manager is not None:
                group.stop(timeout=10)


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


class MonitoringSource(ProcessAudioSource):
    def start(self):
        self.stopped_event = threading.Event()
        self.started = True

    def start_stream(self):
        threading.Thread(target=self._stream, daemon=True).start()

    def _stream(self):
        while not self.stopped_event.wait(0.05):
            self.on_audio(np.full(800, 0.25, dtype=np.float32))

    def stop(self):
        self.stopped_event.set()


@pytest.mark.parametrize('topology', ['shared-model', 'per-input-model'])
def test_live_pcm_crosses_bounded_process_channel_without_entering_results(api, topology):
    group = api.start_multiprocess_microphone_flows(
        ['monitor-test'], model_config={}, flow_config={'chunk_seconds': 0.1, 'silence_threshold': 0},
        topology=topology, runtime_factory=ProcessRuntimeFactory(), audio_source_factory=MonitoringSource,
    )
    session, = group.sessions
    listener = session.audio_monitor.subscribe()
    try:
        packet = listener.get(timeout=3)
        np.testing.assert_allclose(np.frombuffer(packet, dtype='<i2'), int(0.25 * 32767), atol=1)
        session.audio_monitor.unsubscribe(listener)
        time.sleep(0.15)
        group.stop(timeout=10)
        events = collect_until_completed(session, timeout=2)
        assert texts(events)
        assert all(isinstance(event, dict) and 'pcm' not in event for event in events)
    finally:
        group.abort()
