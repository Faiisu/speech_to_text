from __future__ import annotations

import queue

import pytest

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


def test_session_preserves_source_events_then_matches_and_forwards_partial_failure():
    source_events = [
        {
            "type": "transcript",
            "source_id": "source-17",
            "sequence": 1,
            "text": "ลาก่อน",
        },
        {"type": "error", "source_id": "source-17", "code": "CHUNK_INFERENCE_FAILED"},
        {
            "type": "transcript",
            "source_id": "source-17",
            "sequence": 0,
            "text": "สวัสดี",
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
        "error",
        "transcript",
        "match_results",
        "forwarded",
        "completed",
    ]
    assert events[0] == source_events[0]
    assert events[1] == source_events[1]
    assert events[2] == source_events[2]
    assert events[3] == {
        "type": "match_results",
        "source_id": "source-17",
        "language": "th",
        "matches": [
            {"keyword": "สวัสดี", "count": 1},
            {"keyword": "ลาก่อน", "count": 1},
            {"keyword": "ขอบคุณ", "count": 0},
        ],
    }
    assert events[-1] == source_events[-1]
    assert received[0]["transcript"] == "สวัสดี ลาก่อน"
    assert received[0]["status"] == "failed"
    assert events[4]["receipt"] == {"delivered": True}


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

    assert [event["type"] for event in drain_events(wrapped)] == [
        "transcript",
        "match_results",
        "completed",
    ]


def test_session_reports_forwarding_failure_without_losing_matches():
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
    service_instance = service.TranscriptionService(model_config={"model": "turbo"})
    monkeypatch.setattr(service_instance, "_get_model", lambda: "shared-model")
    monkeypatch.setattr(
        service,
        "start_microphone_and_forward",
        lambda device, model, forwarder, matching, flow_config: calls.append(
            (device, model, forwarder, flow_config)
        ) or workflow.forward_session(session, forwarder, matching),
    )

    result = service_instance.start_microphone(
        "USB mic", ["สวัสดี"], {"source_id": "shared-1"}
    )
    result.wait(timeout=1)

    assert calls == [("USB mic", "shared-model", None, {"source_id": "shared-1"})]


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
        {"source_id": "process-1"},
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
                "enqueue_timeout_seconds": 1.0,
            },
            "topology": "per-input-model",
            "flow_configs": [{"source_id": "process-1"}],
        }
    ]
    assert [event["type"] for event in drain_events(wrapped)] == [
        "transcript",
        "match_results",
        "completed",
    ]
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
