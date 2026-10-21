from __future__ import annotations

import io
import multiprocessing
import os
import queue
import wave

import pytest
import numpy as np

from speech_to_text.features.word_matching import WordMatchingConfig, WordMatchingError
from speech_to_text.workflows import transcribe_match_forward as workflow
from speech_to_text.workflows.transcribe_match_forward import api


class FakeSession:
    def __init__(self, events, source_id="source-17"):
        self.source_id = source_id
        self.result_queue = queue.Queue()
        for event in events:
            self.result_queue.put(event)
        self.stop_calls = []

    def stop(self, *, timeout=None):
        self.stop_calls.append(timeout)


class _IsolatedRuntime:
    def __init__(self, marker, transcript):
        self.marker = marker
        self.transcript = transcript

    def transcribe(self, audio, *, language, decoding_options):
        if isinstance(self.transcript, Exception):
            raise self.transcript
        return self.transcript

    def close(self):
        with open(self.marker, "w", encoding="utf-8") as marker:
            marker.write(str(os.getpid()))


class _IsolatedRuntimeFactory:
    def __init__(self, marker, transcript):
        self.marker = str(marker)
        self.transcript = transcript

    def __call__(self, _config):
        return _IsolatedRuntime(self.marker, self.transcript)


class _VADUnavailableRuntime:
    def transcribe(self, *_args, **_kwargs):
        pytest.fail("VAD should fail before runtime inference")

    def close(self):
        pass


class _VADUnavailableRuntimeFactory:
    def __call__(self, _config):
        from speech_to_text.features.model_deployment import model as model_module
        from speech_to_text.features.model_deployment import VADUnavailableError

        def fail_detection(_samples, _options):
            raise VADUnavailableError("Silero VAD is unavailable")

        model_module.detect_speech = fail_detection
        return _VADUnavailableRuntime()


def _wav_bytes(samples):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(np.asarray(samples, dtype="<i2").tobytes())
    return output.getvalue()


def drain_events(workflow_session):
    events = []
    while True:
        try:
            events.append(workflow_session.output_queue.get_nowait())
        except queue.Empty:
            return events


def test_clip_workflow_returns_matches_and_forwards_one_completed_record(monkeypatch):
    calls = []

    def transcribe_clip(clip, model_handle, flow_config, *, source_id):
        calls.append((clip, model_handle, flow_config, source_id))
        return "สวัสดี สวัสดี"

    monkeypatch.setattr(api, "transcribe_clip", transcribe_clip)
    matching = WordMatchingConfig(keywords=("สวัสดี", "ลาก่อน"))
    receipts = []

    result = workflow.transcribe_clip_and_forward(
        b"wav data",
        "model-handle",
        lambda record: receipts.append(record) or "accepted",
        matching,
        {"language": "th", "source_id": "clip-42"},
    )

    assert calls == [
        (
            b"wav data",
            "model-handle",
            {"language": "th", "source_id": "clip-42"},
            "clip-42",
        )
    ]
    assert result.transcript == "สวัสดี สวัสดี"
    assert [(item.keyword, item.count) for item in result.matches] == [
        ("สวัสดี", 2),
        ("ลาก่อน", 0),
    ]
    assert result.source_id == "clip-42"
    assert result.forwarding_receipt == "accepted"
    assert result.forwarding_error is None
    assert len(receipts) == 1
    assert {
        key: receipts[0][key]
        for key in (
            "event_type",
            "event_id",
            "source_id",
            "language",
            "transcript",
            "matches",
            "status",
        )
    } == {
        "event_type": "transcription.match_results",
        "event_id": "clip-42",
        "source_id": "clip-42",
        "language": "th",
        "transcript": "สวัสดี สวัสดี",
        "matches": [
            {"keyword": "สวัสดี", "count": 2},
            {"keyword": "ลาก่อน", "count": 0},
        ],
        "status": "completed",
    }
    assert receipts[0]["completed_at"].endswith("Z")


def test_isolated_clip_returns_only_after_spawned_model_process_exits(tmp_path):
    from speech_to_text.workflows.transcribe_match_forward.service import (
        TranscriptionService,
    )

    marker = tmp_path / "child-closed"
    transcript = "isolated result " + "x" * (1024 * 1024)
    measurements = []
    service = TranscriptionService(
        runtime_factory=_IsolatedRuntimeFactory(marker, transcript)
    )

    result = service.transcribe_clip(
        _wav_bytes(np.full(1600, 10000, dtype=np.int16)),
        ["isolated"],
        {"source_id": "isolated-clip", "language": "en", "chunk_seconds": 0.1},
        model="turbo",
        runtime="openvino-gpu",
        execution_mode="per_workflow_process",
        measurement_callback=measurements.append,
    )

    child_pid = int(marker.read_text(encoding="utf-8"))
    assert child_pid != os.getpid()
    assert not any(child.pid == child_pid and child.is_alive() for child in multiprocessing.active_children())
    assert result.transcript == transcript
    assert [(item.keyword, item.count) for item in result.matches] == [("isolated", 1)]
    assert result.latest_rtf == measurements[-1]["rtf"]
    assert measurements
    service.close()
    with pytest.raises(workflow.WorkflowUnavailableError, match="service is closing"):
        service.transcribe_clip(
            _wav_bytes(np.full(1600, 10000, dtype=np.int16)),
            ["isolated"],
            {"source_id": "after-close", "language": "en"},
            execution_mode="per_workflow_process",
        )


def test_isolated_clip_relays_failed_measurement_and_closes_child(tmp_path):
    from speech_to_text.workflows.transcribe_match_forward.service import (
        TranscriptionService,
    )

    marker = tmp_path / "child-closed-on-error"
    measurements = []
    service = TranscriptionService(
        runtime_factory=_IsolatedRuntimeFactory(
            marker, RuntimeError("fake inference failure")
        )
    )

    result = service.transcribe_clip(
        _wav_bytes(np.full(1600, 10000, dtype=np.int16)),
        ["isolated"],
        {"source_id": "failed-isolated-clip", "language": "en", "chunk_seconds": 0.1},
        model="turbo",
        runtime="openvino-gpu",
        execution_mode="per_workflow_process",
        measurement_callback=measurements.append,
    )

    child_pid = int(marker.read_text(encoding="utf-8"))
    assert child_pid != os.getpid()
    assert not any(child.pid == child_pid and child.is_alive() for child in multiprocessing.active_children())
    assert result.transcript == ""
    assert measurements[0]["status"] == "failed"
    assert "fake inference failure" in measurements[0]["error"]


@pytest.mark.parametrize("execution_mode", ["shared", "per_workflow_process"])
def test_vad_unavailable_maps_to_workflow_unavailable(execution_mode, monkeypatch):
    from speech_to_text.features.model_deployment import model as model_module
    from speech_to_text.features.model_deployment import VADUnavailableError
    from speech_to_text.workflows.transcribe_match_forward import service as service_module
    from speech_to_text.workflows.transcribe_match_forward.service import (
        TranscriptionService,
    )

    def fail_detection(_samples, _options):
        raise VADUnavailableError("Silero VAD is unavailable")

    monkeypatch.setattr(model_module, "detect_speech", fail_detection)
    if execution_mode == "per_workflow_process":
        class FakeQueue(queue.Queue):
            def close(self):
                pass

            def join_thread(self):
                pass

        class FakeProcess:
            def __init__(self, target, args):
                self.target = target
                self.args = args
                self.pid = None
                self.exitcode = None

            def start(self):
                self.pid = 1
                self.target(*self.args)
                self.exitcode = 0

            def is_alive(self):
                return False

            def join(self):
                pass

            def close(self):
                pass

        class FakeContext:
            def Queue(self):
                return FakeQueue()

            def Process(self, *, target, args, name):
                return FakeProcess(target, args)

        monkeypatch.setattr(service_module.mp, "get_context", lambda _method: FakeContext())
    service = TranscriptionService(runtime_factory=_VADUnavailableRuntimeFactory())
    with pytest.raises(workflow.WorkflowUnavailableError, match="Silero VAD is unavailable"):
        service.transcribe_clip(
            _wav_bytes(np.full(1600, 10000, dtype=np.int16)),
            ["isolated"],
            {"source_id": "vad-unavailable", "language": "en", "vad": {"enabled": True}},
            model="turbo",
            runtime="openvino-gpu",
            execution_mode=execution_mode,
        )
    service.close()


class _HardExitRuntime:
    def __init__(self, marker):
        self.marker = marker

    def transcribe(self, audio, *, language, decoding_options):
        with open(self.marker, "w", encoding="utf-8") as marker:
            marker.write(str(os.getpid()))
        os._exit(17)

    def close(self):
        pass


class _HardExitRuntimeFactory:
    def __init__(self, marker):
        self.marker = str(marker)

    def __call__(self, _config):
        return _HardExitRuntime(self.marker)


def test_isolated_clip_reports_child_crash_without_leaving_a_process(tmp_path):
    from speech_to_text.workflows.transcribe_match_forward.service import (
        TranscriptionService,
    )

    marker = tmp_path / "crashed-child"
    service = TranscriptionService(runtime_factory=_HardExitRuntimeFactory(marker))
    with pytest.raises(workflow.WorkflowUnavailableError, match="exited with code 17"):
        service.transcribe_clip(
            _wav_bytes(np.full(1600, 10000, dtype=np.int16)),
            ["isolated"],
            {"source_id": "crashed-isolated-clip", "language": "en", "chunk_seconds": 0.1},
            model="turbo",
            runtime="openvino-gpu",
            execution_mode="per_workflow_process",
        )
    child_pid = int(marker.read_text(encoding="utf-8"))
    assert child_pid != os.getpid()
    assert not any(child.pid == child_pid and child.is_alive() for child in multiprocessing.active_children())


@pytest.mark.parametrize("language", ["en", "auto"])
def test_clip_forwarding_uses_configured_language_for_mixed_script_matches(
    monkeypatch, language
):
    monkeypatch.setattr(api, "transcribe_clip", lambda *args, **kwargs: "Hello สวัสดี")
    records = []

    result = workflow.transcribe_clip_and_forward(
        "clip.wav",
        object(),
        lambda record: records.append(record),
        WordMatchingConfig(keywords=("hello", "สวัสดี"), language=language),
        {"language": language},
    )

    assert [(match.keyword, match.count) for match in result.matches] == [
        ("hello", 1),
        ("สวัสดี", 1),
    ]
    assert records[0]["language"] == language


def test_clip_workflow_without_forwarder_returns_transcript_and_matches(monkeypatch):
    monkeypatch.setattr(api, "transcribe_clip", lambda *args, **kwargs: "ประเทศไทย")

    result = workflow.transcribe_clip_and_forward(
        "clip.wav", object(), None, WordMatchingConfig(keywords=("ประเทศไทย",))
    )

    assert result.transcript == "ประเทศไทย"
    assert [(item.keyword, item.count) for item in result.matches] == [("ประเทศไทย", 1)]
    assert result.forwarding_receipt is None
    assert result.forwarding_error is None


@pytest.mark.parametrize(
    "flow_config",
    [{"language": "en"}, {"language": "auto"}],
)
def test_clip_workflow_rejects_language_mismatch_before_transcription(
    monkeypatch, flow_config
):
    called = False

    def transcribe_clip(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(api, "transcribe_clip", transcribe_clip)

    with pytest.raises(WordMatchingError, match="must match transcript language"):
        workflow.transcribe_clip_and_forward(
            "clip.wav",
            object(),
            None,
            WordMatchingConfig(keywords=("hello",)),
            flow_config,
        )

    assert called is False


def test_clip_workflow_requires_public_matching_configuration():
    with pytest.raises(TypeError, match="WordMatchingConfig"):
        workflow.transcribe_clip_and_forward("clip.wav", object(), None, ("hello",))


def test_session_forwards_each_chunk_and_matches_partial_failure_incrementally():
    source_events = [
        {
            "type": "transcript",
            "source_id": "source-17",
            "sequence": 0,
            "text": "สวัสดี",
        },
        {"type": "error", "source_id": "source-17", "code": "CHUNK_INFERENCE_FAILED"},
        {
            "type": "transcript",
            "source_id": "source-17",
            "sequence": 1,
            "text": "ลาก่อน",
        },
        {"type": "completed", "source_id": "source-17", "status": "failed"},
    ]
    received = []
    wrapped = workflow.forward_session(
        FakeSession(source_events),
        lambda record: received.append(record) or {"delivered": True},
        WordMatchingConfig(keywords=("สวัสดี", "ลาก่อน", "ขอบคุณ")),
    )

    wrapped.wait(timeout=1)
    events = drain_events(wrapped)

    assert [event["type"] for event in events] == [
        "transcript",
        "match_results",
        "forwarded",
        "error",
        "transcript",
        "match_results",
        "forwarded",
        "match_results",
        "completed",
    ]
    assert events[0] == source_events[0]
    assert events[1]["matches"] == [
        {"keyword": "สวัสดี", "count": 1},
        {"keyword": "ลาก่อน", "count": 0},
        {"keyword": "ขอบคุณ", "count": 0},
    ]
    assert events[3] == source_events[1]
    assert events[4] == source_events[2]
    expected_matches = {
        "type": "match_results",
        "source_id": "source-17",
        "language": "th",
        "matches": [
            {"keyword": "สวัสดี", "count": 1},
            {"keyword": "ลาก่อน", "count": 1},
            {"keyword": "ขอบคุณ", "count": 0},
        ],
    }
    assert events[5] == expected_matches
    assert events[7] == expected_matches
    assert events[-1] == source_events[-1]
    assert [record["transcript"] for record in received] == ["สวัสดี", "ลาก่อน"]
    assert [record["event_id"] for record in received] == [
        "source-17:chunk:1", "source-17:chunk:2"
    ]
    assert received[0]["status"] == "completed"
    assert events[2]["receipt"] == {"delivered": True}
    assert events[6]["receipt"] == {"delivered": True}


def test_session_without_forwarder_emits_matches_and_completion_only():
    source_events = [
        {
            "type": "transcript",
            "source_id": "source-17",
            "sequence": 0,
            "text": "สวัสดี",
        },
        {"type": "completed", "source_id": "source-17", "status": "completed"},
    ]
    wrapped = workflow.forward_session(
        FakeSession(source_events), None, WordMatchingConfig(keywords=("สวัสดี",))
    )

    wrapped.wait(timeout=1)

    events = drain_events(wrapped)
    assert [event["type"] for event in events] == [
        "transcript",
        "match_results",
        "match_results",
        "completed",
    ]


def test_session_emits_cumulative_snapshots_and_forwards_each_chunk():
    source_events = [
        {"type": "transcript", "source_id": "source-17", "sequence": 0, "text": "สวัสดี"},
        {"type": "transcript", "source_id": "source-17", "sequence": 1, "text": " สวัสดี"},
        {"type": "completed", "source_id": "source-17", "status": "stopped"},
    ]
    received = []
    wrapped = workflow.forward_session(
        FakeSession(source_events),
        lambda record: received.append(record),
        WordMatchingConfig(keywords=("สวัสดี", "ลาก่อน")),
    )

    wrapped.wait(timeout=1)
    events = drain_events(wrapped)
    snapshots = [event for event in events if event["type"] == "match_results"]

    assert [event["matches"] for event in snapshots] == [
        [{"keyword": "สวัสดี", "count": 1}, {"keyword": "ลาก่อน", "count": 0}],
        [{"keyword": "สวัสดี", "count": 2}, {"keyword": "ลาก่อน", "count": 0}],
        [{"keyword": "สวัสดี", "count": 2}, {"keyword": "ลาก่อน", "count": 0}],
    ]
    assert [event["type"] for event in events] == [
        "transcript", "match_results", "forwarded", "transcript",
        "match_results", "forwarded", "match_results", "completed",
    ]
    assert [record["transcript"] for record in received] == ["สวัสดี", " สวัสดี"]
    assert [record["event_id"] for record in received] == [
        "source-17:chunk:1", "source-17:chunk:2"
    ]


def test_session_publishes_chunk_local_payloads_for_live_forwarders():
    class FakeMqttForwarder:
        transport = "mqtt"

        def __init__(self):
            self.records = []

        def forward(self, record):
            self.records.append(record)
            return {"event_id": record["event_id"]}

    forwarder = FakeMqttForwarder()
    wrapped = workflow.forward_session(
        FakeSession(
            [
                {"type": "measurement", "source_id": "source-17", "sequence": 1, "rtf": 0.2},
                {"type": "measurement", "source_id": "source-17", "sequence": 0, "rtf": 0.1},
                {"type": "transcript", "source_id": "source-17", "sequence": 0, "text": "สวัสดี สวัสดี"},
                {"type": "transcript", "source_id": "source-17", "sequence": 1, "text": "ลาก่อน"},
                {"type": "completed", "source_id": "source-17", "status": "stopped"},
            ]
        ),
        forwarder,
        WordMatchingConfig(keywords=("สวัสดี", "ลาก่อน", "สวัสดี ลาก่อน")),
    )

    wrapped.wait(timeout=1)
    events = drain_events(wrapped)

    assert [record["transcript"] for record in forwarder.records] == [
        "สวัสดี สวัสดี", "ลาก่อน"
    ]
    assert [record["matches"] for record in forwarder.records] == [
        [
            {"keyword": "สวัสดี", "count": 2},
            {"keyword": "ลาก่อน", "count": 0},
            {"keyword": "สวัสดี ลาก่อน", "count": 0},
        ],
        [
            {"keyword": "สวัสดี", "count": 0},
            {"keyword": "ลาก่อน", "count": 1},
            {"keyword": "สวัสดี ลาก่อน", "count": 0},
        ],
    ]
    assert [record["event_id"] for record in forwarder.records] == ["source-17:chunk:1", "source-17:chunk:2"]
    assert [record["source_id"] for record in forwarder.records] == [
        "source-17", "source-17"
    ]
    assert [record["rtf"] for record in forwarder.records] == [0.1, 0.2]
    snapshots = [event for event in events if event["type"] == "match_results"]
    assert [snapshot["matches"] for snapshot in snapshots] == [
        [
            {"keyword": "สวัสดี", "count": 2},
            {"keyword": "ลาก่อน", "count": 0},
            {"keyword": "สวัสดี ลาก่อน", "count": 0},
        ],
        [
            {"keyword": "สวัสดี", "count": 2},
            {"keyword": "ลาก่อน", "count": 1},
            {"keyword": "สวัสดี ลาก่อน", "count": 1},
        ],
        [
            {"keyword": "สวัสดี", "count": 2},
            {"keyword": "ลาก่อน", "count": 1},
            {"keyword": "สวัสดี ลาก่อน", "count": 1},
        ],
    ]
    assert [event["type"] for event in events] == [
        "measurement", "measurement",
        "transcript", "match_results", "forwarded", "transcript", "match_results",
        "forwarded", "match_results", "completed",
    ]


def test_long_live_session_keeps_cumulative_counts_and_chunk_ids_bounded_by_chunk():
    class Forwarder:
        transport = "mqtt"

        def __init__(self):
            self.records = []

        def forward(self, record):
            self.records.append(record)

    chunks = 1000
    forwarder = Forwarder()
    source_events = [
        {"type": "transcript", "source_id": "source-17", "sequence": index, "text": "a"}
        for index in range(chunks)
    ]
    source_events.append(
        {"type": "completed", "source_id": "source-17", "status": "stopped"}
    )
    wrapped = workflow.forward_session(
        FakeSession(source_events),
        forwarder,
        WordMatchingConfig(keywords=("a a",)),
    )

    wrapped.wait(timeout=3)
    events = drain_events(wrapped)

    assert len(forwarder.records) == chunks
    assert [record["event_id"] for record in forwarder.records] == [
        f"source-17:chunk:{index}" for index in range(1, chunks + 1)
    ]
    assert [record["transcript"] for record in forwarder.records[:2]] == ["a", "a"]
    assert all(record["matches"] == [{"keyword": "a a", "count": 0}]
               for record in forwarder.records)
    final_snapshot = [event for event in events if event["type"] == "match_results"][-1]
    assert final_snapshot["matches"] == [{"keyword": "a a", "count": chunks - 1}]


def test_chunk_delivery_error_does_not_stop_matching_later_chunks():
    class FailingFirstMqttForwarder:
        transport = "mqtt"

        def __init__(self):
            self.records = []

        def forward(self, record):
            self.records.append(record)
            if len(self.records) == 1:
                raise RuntimeError("broker unavailable")
            return {"event_id": record["event_id"]}

    forwarder = FailingFirstMqttForwarder()
    wrapped = workflow.forward_session(
        FakeSession(
            [
                {"type": "transcript", "source_id": "source-17", "sequence": 0, "text": "สวัสดี"},
                {"type": "transcript", "source_id": "source-17", "sequence": 1, "text": " สวัสดี"},
                {"type": "completed", "source_id": "source-17", "status": "stopped"},
            ]
        ),
        forwarder,
        WordMatchingConfig(keywords=("สวัสดี",)),
    )

    wrapped.wait(timeout=1)
    events = drain_events(wrapped)

    assert [event["type"] for event in events].count("match_results") == 3
    assert [event["type"] for event in events].count("forwarding_error") == 1
    assert [record["event_id"] for record in forwarder.records] == ["source-17:chunk:1", "source-17:chunk:2"]
    assert events[-1]["type"] == "completed"


def test_live_records_use_null_rtf_when_measurements_are_unavailable():
    class FakeMqttForwarder:
        transport = "mqtt"

        def __init__(self):
            self.records = []

        def forward(self, record):
            self.records.append(record)

    forwarder = FakeMqttForwarder()
    wrapped = workflow.forward_session(
        FakeSession(
            [
                {"type": "transcript", "source_id": "source-17", "sequence": 0, "text": "สวัสดี"},
                {"type": "completed", "source_id": "source-17", "status": "stopped"},
            ]
        ),
        forwarder,
        WordMatchingConfig(keywords=("สวัสดี",)),
    )

    wrapped.wait(timeout=1)

    assert [record["rtf"] for record in forwarder.records] == [None]


def test_session_without_transcript_does_not_publish_an_aggregate():
    class FakeMqttForwarder:
        transport = "mqtt"

        def __init__(self):
            self.records = []

        def forward(self, record):
            self.records.append(record)

    forwarder = FakeMqttForwarder()
    wrapped = workflow.forward_session(
        FakeSession([{"type": "completed", "source_id": "source-17", "status": "stopped"}]),
        forwarder,
        WordMatchingConfig(keywords=("สวัสดี",)),
    )

    wrapped.wait(timeout=1)

    assert forwarder.records == []


def test_mqtt_clip_keeps_latest_inference_rtf_without_publishing(monkeypatch):
    class FakeMqttForwarder:
        transport = "mqtt"

        def __init__(self):
            self.records = []

        def forward(self, record):
            self.records.append(record)

    def transcribe_clip(_clip, _handle, _config, *, source_id, measurement_sink):
        measurement_sink({"sequence": 0, "rtf": 0.1})
        measurement_sink({"sequence": 1, "rtf": 0.25})
        return "สวัสดี"

    monkeypatch.setattr(api, "transcribe_clip", transcribe_clip)
    forwarder = FakeMqttForwarder()
    result = workflow.transcribe_clip_and_forward(
        b"clip", object(), forwarder, WordMatchingConfig(keywords=("สวัสดี",))
    )

    assert result.latest_rtf == 0.25
    assert result.transcript == "สวัสดี"
    assert [(item.keyword, item.count) for item in result.matches] == [("สวัสดี", 1)]
    assert result.forwarding_receipt is None
    assert result.forwarding_error is None
    assert forwarder.records == []


def test_session_reports_chunk_forwarding_failure_without_losing_matches():
    source_events = [
        {
            "type": "transcript",
            "source_id": "source-17",
            "sequence": 0,
            "text": "สวัสดี",
        },
        {"type": "completed", "source_id": "source-17", "status": "stopped"},
    ]

    def fail_forwarding(_record):
        raise RuntimeError("receiver unavailable")

    wrapped = workflow.forward_session(
        FakeSession(source_events),
        fail_forwarding,
        WordMatchingConfig(keywords=("สวัสดี",)),
    )

    wrapped.wait(timeout=1)
    events = drain_events(wrapped)

    assert [event["type"] for event in events] == [
        "transcript",
        "match_results",
        "forwarding_error",
        "match_results",
        "completed",
    ]
    assert events[1]["matches"] == [{"keyword": "สวัสดี", "count": 1}]
    assert events[2]["error"] == "receiver unavailable"
    assert events[-1] == source_events[-1]


def test_session_stop_delegates_and_waits_for_public_completion():
    source_events = [
        {"type": "completed", "source_id": "source-17", "status": "stopped"}
    ]
    session = FakeSession(source_events)
    wrapped = workflow.forward_session(
        session, None, WordMatchingConfig(keywords=("สวัสดี",))
    )

    wrapped.stop(timeout=2)
    wrapped.stop(timeout=2)

    assert session.stop_calls == [2]
    assert drain_events(wrapped)[-1] == source_events[-1]


def test_session_stop_failure_aborts_owned_resources_and_emits_failure():
    session = FakeSession([], source_id="source-18")
    cleanup_calls = []

    def fail_stop(*, timeout=None):
        raise TimeoutError("source did not stop")

    session.stop = fail_stop
    wrapped = workflow.forward_session(
        session,
        None,
        WordMatchingConfig(keywords=("สวัสดี",)),
        on_stop_error=lambda: cleanup_calls.append("aborted"),
    )

    wrapped.stop(timeout=2)

    events = drain_events(wrapped)
    assert cleanup_calls == ["aborted"]
    assert [event["type"] for event in events] == [
        "workflow_error",
        "match_results",
        "completed",
    ]
    assert events[0]["error"] == "source did not stop"
    assert events[-1]["status"] == "failed"


def test_microphone_start_and_discovery_are_exposed_through_workflow(monkeypatch):
    session = FakeSession(
        [{"type": "completed", "source_id": "mic-1", "status": "stopped"}],
        source_id="mic-1",
    )
    started = []
    devices = [{"name": "USB microphone"}]
    monkeypatch.setattr(
        api,
        "start_microphone_flow",
        lambda device, model, config: started.append((device, model, config))
        or session,
    )
    monkeypatch.setattr(api, "list_microphone_devices", lambda: devices)

    result = workflow.start_microphone_and_forward(
        "USB microphone",
        "model-handle",
        None,
        WordMatchingConfig(keywords=("สวัสดี",)),
        {"language": "th", "source_id": "mic-1"},
    )

    result.wait(timeout=1)

    assert started == [
        ("USB microphone", "model-handle", {"language": "th", "source_id": "mic-1"})
    ]
    assert result.source_id == "mic-1"
    assert [event["type"] for event in drain_events(result)] == [
        "match_results",
        "completed",
    ]
    assert workflow.list_available_microphones() == devices


def test_service_defaults_to_shared_microphone_mode(monkeypatch):
    from speech_to_text.workflows.transcribe_match_forward import service

    calls = []
    session = FakeSession(
        [{"type": "completed", "source_id": "shared-1", "status": "stopped"}],
        source_id="shared-1",
    )
    class ModelHandle:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    handle = ModelHandle()
    service_instance = service.TranscriptionService(model_config={"model": "turbo"})

    monkeypatch.setattr(service, "load_model", lambda _config: handle)

    def start(device, model, forwarder, matching, flow_config, *, on_finished=None):
        calls.append((device, model, forwarder, flow_config))
        return workflow.forward_session(
            session, forwarder, matching, on_finished=on_finished
        )

    monkeypatch.setattr(
        service,
        "start_microphone_and_forward",
        start,
    )

    result = service_instance.start_microphone(
        "USB mic", ["สวัสดี"], {"source_id": "shared-1", "language": "auto"}
    )
    result.wait(timeout=1)

    assert calls == [
        (
            "USB mic", handle, None,
            {
                "source_id": "shared-1",
                "language": "auto",
                "vad": {"enabled": False, "threshold": 0.5, "min_speech_duration_ms": 250},
            },
        )
    ]
    assert next(event for event in drain_events(result) if event["type"] == "match_results")["language"] == "auto"
    assert handle.close_calls == 1
    assert service_instance._model_handles == {}


@pytest.mark.parametrize("language", ["en", "auto"])
def test_service_clip_uses_selected_language_and_flow_snapshot(monkeypatch, language):
    from speech_to_text.workflows.transcribe_match_forward import service

    loaded = []
    forwarded = []

    class Handle:
        def close(self):
            pass

    handle = Handle()

    def load(config):
        loaded.append(dict(config))
        return handle

    def transcribe(clip, model_handle, forwarder, matching, flow_config, **kwargs):
        forwarded.append((clip, model_handle, matching.language, flow_config, kwargs))
        return workflow.PipelineResult("alpha", matching.keywords, "clip-1", None)

    monkeypatch.setattr(service, "load_model", load)
    monkeypatch.setattr(service, "transcribe_clip_and_forward", transcribe)
    instance = service.TranscriptionService()

    result = instance.transcribe_clip(
        "clip.wav",
        ["alpha"],
        {
            "language": language,
            "source_id": "clip-1",
            "silence_threshold": 0.2,
            "decoding_options": {"beam_size": 3},
            "vad": {"enabled": True, "threshold": 0.35},
        },
        model="tiny",
        runtime="ctranslate2",
    )

    assert result.transcript == "alpha"
    assert loaded == [
        {
            "model": "tiny",
            "runtime": "ctranslate2",
            "precision": "int8",
            "queue_capacity": 6,
            "enqueue_timeout_seconds": 30.0,
        }
    ]
    assert forwarded == [
        (
            "clip.wav",
            handle,
            language,
            {
                "language": language,
                "source_id": "clip-1",
                "silence_threshold": 0.2,
                "decoding_options": {"beam_size": 3},
                "vad": {
                    "enabled": True,
                    "threshold": 0.35,
                    "min_speech_duration_ms": 250,
                },
            },
            {"measurement_callback": None},
        )
    ]


@pytest.mark.parametrize(
    "value",
    [
        {"enabled": 1},
        {"threshold": True},
        {"threshold": 0},
        {"threshold": 1},
        {"threshold": float("nan")},
        {"min_speech_duration_ms": False},
        {"min_speech_duration_ms": 30001},
        {"extra": 1},
        [],
    ],
)
def test_workflow_vad_normalizer_rejects_invalid_settings(value):
    with pytest.raises(workflow.WorkflowConfigurationError):
        workflow.normalize_vad_options(value)


def test_workflow_vad_normalizer_returns_complete_detached_defaults():
    vad = workflow.normalize_vad_options({"enabled": True})
    assert vad == {
        "enabled": True,
        "threshold": 0.5,
        "min_speech_duration_ms": 250,
    }
    vad["threshold"] = 0.9
    assert workflow.normalize_vad_options()["threshold"] == 0.5


@pytest.mark.parametrize("execution_mode", ["processes", []])
def test_service_rejects_unknown_microphone_execution_mode(execution_mode):
    from speech_to_text.workflows.transcribe_match_forward import service

    service_instance = service.TranscriptionService()

    with pytest.raises(service.WorkflowConfigurationError, match="execution_mode"):
        service_instance.start_microphone(
            None, ["สวัสดี"], execution_mode=execution_mode
        )


def test_per_workflow_process_mode_owns_group_through_natural_completion(monkeypatch):
    from speech_to_text.workflows.transcribe_match_forward import service

    class FakeGroup:
        def __init__(self, session):
            self.sessions = [session]
            self.stop_calls = []
            self.abort_calls = []

        def stop(self, *, timeout=30):
            self.stop_calls.append(timeout)

        def abort(self, *, timeout=5):
            self.abort_calls.append(timeout)

    session = FakeSession(
        [
            {"type": "transcript", "source_id": "process-1", "sequence": 0, "text": "สวัสดี"},
            {"type": "completed", "source_id": "process-1", "status": "completed"},
        ],
        source_id="process-1",
    )
    group = FakeGroup(session)
    starts = []
    monkeypatch.setattr(
        service,
        "start_multiprocess_microphone_flows",
        lambda **kwargs: starts.append(kwargs) or group,
        raising=False,
    )
    service_instance = service.TranscriptionService(model_config={"model": "turbo"})

    wrapped = service_instance.start_microphone(
        "USB mic",
        ["สวัสดี"],
        {"source_id": "process-1", "language": "en"},
        execution_mode="per_workflow_process",
        model="small",
        runtime="ctranslate2",
    )
    wrapped.wait(timeout=1)

    assert starts == [
        {
            "devices": ["USB mic"],
            "model_config": {
                "model": "small",
                "runtime": "ctranslate2",
                "precision": "int8",
                "queue_capacity": 6,
                "enqueue_timeout_seconds": 30.0,
            },
            "topology": "per-input-model",
            "flow_configs": [{
                "source_id": "process-1",
                "language": "en",
                "vad": {"enabled": False, "threshold": 0.5, "min_speech_duration_ms": 250},
            }],
        }
    ]
    events = drain_events(wrapped)
    assert [event["type"] for event in events] == [
        "transcript",
        "match_results",
        "match_results",
        "completed",
    ]
    assert all(
        event["language"] == "en"
        for event in events
        if event["type"] == "match_results"
    )
    assert group.stop_calls == [30]
    assert group.abort_calls == []
    assert service_instance._model_handle is None


def test_runtime_override_preserves_explicit_precision(monkeypatch):
    from speech_to_text.workflows.transcribe_match_forward import service

    instance = service.TranscriptionService(model_config={"precision": "source"})

    config = instance._effective_model_config("small", "ctranslate2")

    assert config["runtime"] == "ctranslate2"
    assert config["precision"] == "source"


def test_runtime_only_ctranslate2_override_uses_supported_precision(monkeypatch):
    from speech_to_text.workflows.transcribe_match_forward import service

    instance = service.TranscriptionService()

    config = instance._effective_model_config("small", "ctranslate2")

    assert config["runtime"] == "ctranslate2"
    assert config["precision"] == "int8"


def test_model_only_configuration_uses_effective_runtime_with_compatible_default_precision(monkeypatch):
    from speech_to_text.workflows.transcribe_match_forward import service

    monkeypatch.setenv("SPEECH_TO_TEXT_RUNTIME", "ctranslate2")
    instance = service.TranscriptionService()

    config = instance._effective_model_config("small")

    assert config["runtime"] == "ctranslate2"
    assert config["precision"] == "int8"


def test_validate_model_runtime_rejects_incompatible_pair_but_allows_unready(monkeypatch):
    from speech_to_text.workflows.transcribe_match_forward import service

    runtimes = [
        {"key": "openvino-gpu", "compatible": True, "ready": False},
        {"key": "ctranslate2", "compatible": False, "ready": False},
    ]
    monkeypatch.setattr(
        service,
        "list_available_models",
        lambda: [{"key": "tiny", "runtimes": runtimes}],
    )
    instance = service.TranscriptionService()

    assert instance.validate_model_runtime("tiny", "openvino-gpu") == "openvino-gpu"
    with pytest.raises(service.WorkflowConfigurationError, match="not supported"):
        instance.validate_model_runtime("tiny", "ctranslate2")


def test_model_catalog_reports_runtime_default_and_installed_precisions(monkeypatch):
    from speech_to_text.workflows.transcribe_match_forward import service

    monkeypatch.delenv("SPEECH_TO_TEXT_PRECISION", raising=False)
    monkeypatch.setenv("SPEECH_TO_TEXT_RUNTIME", "ctranslate2")
    monkeypatch.setattr(
        service,
        "list_available_models",
        lambda: [{
            "key": "tiny",
            "display_name": "Tiny",
            "repository": "org/whisper-tiny",
            "revision": "abc123def",
            "installed": True,
            "runtimes": [{
                "key": "ctranslate2",
                "ready": True,
                "precision_options": ["int8", "float32"],
                "installed_precisions": ["float32"],
            }],
        }],
    )

    catalog = service.TranscriptionService().list_models()
    runtime = catalog["models"][0]["runtimes"][0]
    assert catalog["default_precision"] == "int8"
    assert catalog["models"][0]["repository"] == "org/whisper-tiny"
    assert catalog["models"][0]["revision"] == "abc123def"
    assert runtime["default_precision"] == "int8"
    assert runtime["installed_precisions"] == ["float32"]


def test_catalog_preserves_explicit_precision_in_environment(monkeypatch):
    from speech_to_text.workflows.transcribe_match_forward import service

    monkeypatch.setenv("SPEECH_TO_TEXT_RUNTIME", "ctranslate2")
    monkeypatch.setenv("SPEECH_TO_TEXT_PRECISION", "float32")
    monkeypatch.setattr(
        service,
        "list_available_models",
        lambda: [{
            "key": "tiny",
            "display_name": "Tiny",
            "installed": True,
            "runtimes": [{
                "key": "ctranslate2",
                "ready": True,
                "precision_options": ["int8", "float32"],
                "installed_precisions": ["float32"],
            }],
        }],
    )

    catalog = service.TranscriptionService().list_models()
    assert catalog["default_precision"] == "float32"
    assert catalog["models"][0]["runtimes"][0]["default_precision"] == "float32"


def test_shared_service_reuses_handle_per_effective_model_config_and_closes_all(monkeypatch):
    from speech_to_text.workflows.transcribe_match_forward import service

    handles = []

    class Handle:
        def __init__(self, config):
            self.config = config
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    def load(config):
        handle = Handle(config)
        handles.append(handle)
        return handle

    monkeypatch.setattr(service, "load_model", load)
    instance = service.TranscriptionService(model_config={"model": "turbo"})

    assert instance._get_model("small") is instance._get_model("small")
    assert instance._get_model("turbo") is instance._get_model()
    assert len(handles) == 2
    assert handles[0].config["model"] == "small"
    assert handles[1].config["model"] == "turbo"

    instance.close()

    assert [handle.close_calls for handle in handles] == [1, 1]


def test_per_workflow_process_startup_failure_maps_model_error(monkeypatch):
    from speech_to_text.features.model_deployment import ModelLoadError
    from speech_to_text.workflows.transcribe_match_forward import service

    def start_group(**kwargs):
        raise ModelLoadError("child model could not load")

    monkeypatch.setattr(
        service,
        "start_multiprocess_microphone_flows",
        start_group,
        raising=False,
    )
    service_instance = service.TranscriptionService()

    with pytest.raises(service.WorkflowUnavailableError, match="child model"):
        service_instance.start_microphone(
            None, ["สวัสดี"], execution_mode="per_workflow_process"
        )

    assert service_instance._model_handle is None


def test_per_workflow_process_startup_timeout_maps_unavailability(monkeypatch):
    from speech_to_text.workflows.transcribe_match_forward import service

    monkeypatch.setattr(
        service,
        "start_multiprocess_microphone_flows",
        lambda **kwargs: (_ for _ in ()).throw(queue.Empty()),
        raising=False,
    )
    service_instance = service.TranscriptionService()

    with pytest.raises(service.WorkflowUnavailableError, match="startup timeout"):
        service_instance.start_microphone(
            None, ["สวัสดี"], execution_mode="per_workflow_process"
        )


def test_service_close_stops_an_active_process_group(monkeypatch):
    from speech_to_text.workflows.transcribe_match_forward import service

    class FakeGroup:
        def __init__(self, session):
            self.sessions = [session]
            self.stop_calls = []
            self.abort_calls = []

        def stop(self, *, timeout=30):
            self.stop_calls.append(timeout)
            self.sessions[0].result_queue.put(
                {
                    "type": "completed",
                    "source_id": "active-process",
                    "status": "stopped",
                }
            )

        def abort(self, *, timeout=5):
            self.abort_calls.append(timeout)

    session = FakeSession([], source_id="active-process")
    group = FakeGroup(session)
    monkeypatch.setattr(
        service,
        "start_multiprocess_microphone_flows",
        lambda **kwargs: group,
        raising=False,
    )
    service_instance = service.TranscriptionService()

    wrapped = service_instance.start_microphone(
        None, ["สวัสดี"], execution_mode="per_workflow_process"
    )
    service_instance.close()
    wrapped.wait(timeout=1)

    assert group.stop_calls == [30]
    assert group.abort_calls == []
    assert service_instance._process_groups == set()


def test_process_group_is_aborted_if_workflow_setup_fails(monkeypatch):
    from speech_to_text.workflows.transcribe_match_forward import service

    class FakeGroup:
        sessions = [FakeSession([], source_id="setup-failure")]

        def __init__(self):
            self.abort_calls = []

        def abort(self, *, timeout=5):
            self.abort_calls.append(timeout)

    group = FakeGroup()
    monkeypatch.setattr(
        service,
        "start_multiprocess_microphone_flows",
        lambda **kwargs: group,
        raising=False,
    )

    def fail_workflow_setup(*args, **kwargs):
        raise RuntimeError("setup failed")

    monkeypatch.setattr(
        service,
        "forward_session",
        fail_workflow_setup,
    )
    service_instance = service.TranscriptionService()

    with pytest.raises(RuntimeError, match="setup failed"):
        service_instance.start_microphone(
            None, ["สวัสดี"], execution_mode="per_workflow_process"
        )

    assert group.abort_calls == [5]
    assert service_instance._process_groups == set()
