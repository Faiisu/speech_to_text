"""Public FastAPI behavior using an injected transcription service."""

from __future__ import annotations

import queue
import multiprocessing
import os
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from fastapi.testclient import TestClient

from speech_to_text.features.word_matching import WordMatchingConfig
from speech_to_text.backend.app import create_app
from speech_to_text.backend.dependencies import BackendRuntime
from speech_to_text.workflows.transcribe_match_forward.api import forward_session
from speech_to_text.workflows.transcribe_match_forward import (
    WorkflowConfigurationError,
    TranscriptionService,
    WorkflowUnavailableError,
)


class FakeMicrophoneWorkflow:
    def __init__(self, source_id):
        self.source_id = source_id
        self.output_queue = queue.Queue()
        self.stop_called = threading.Event()

    def stop(self, timeout=30):
        self.stop_called.set()
        self.output_queue.put(
            {
                "type": "completed",
                "source_id": self.source_id,
                "status": "stopped",
            }
        )

    def wait(self, timeout=0):
        return None


class FakeTranscriptionService:
    """Small stand-in at the workflow-service boundary, with no host dependencies."""

    def __init__(self):
        self.microphones = [
            {
                "name": "Studio Mic",
                "selectable": True,
                "max_input_channels": 2,
                "default_samplerate": 48000,
                "is_default": True,
            }
        ]
        self.list_error = None
        self.start_error = None
        self.transcription_started = threading.Event()
        self.transcription_gate = None
        self.microphone_workflow = None
        self.microphone_start_options = []
        self.clip_calls = []

    def validate_keywords(self, keywords):
        if not keywords or any(
            not isinstance(word, str) or not word.strip() for word in keywords
        ):
            raise WorkflowConfigurationError("keywords must contain non-empty strings")
        normalized = [
            unicodedata.normalize("NFC", word).casefold() for word in keywords
        ]
        if len(normalized) != len(set(normalized)):
            raise WorkflowConfigurationError("keywords must be unique")
        return tuple(word.strip() for word in keywords)

    def transcribe_clip(
        self, clip, keywords, flow_config=None, *, model=None, runtime=None
    ):
        self.clip_calls.append(
            (dict(flow_config or {}), model, runtime)
        )
        self.transcription_started.set()
        if self.transcription_gate is not None:
            self.transcription_gate.wait(timeout=10)
        with open(clip, "rb") as audio_file:
            transcript = audio_file.read().decode("ascii")
        return FakeTranscriptionResult(
            transcript=transcript,
            matches=[
                FakeMatch(keyword, transcript.count(keyword)) for keyword in keywords
            ],
        )

    def list_microphones(self):
        if self.list_error:
            raise self.list_error
        return self.microphones

    def start_microphone(
        self, device, keywords, flow_config=None, *, execution_mode="shared", model=None
    ):
        if self.start_error:
            raise self.start_error
        self.microphone_start_options.append(
            (device, tuple(keywords), flow_config, execution_mode, model)
        )
        self.microphone_workflow = FakeMicrophoneWorkflow(flow_config["source_id"])
        return self.microphone_workflow

    def close(self):
        pass


class FakeTranscriptionResult:
    def __init__(self, transcript, matches):
        self.transcript = transcript
        self.matches = matches


class FakeMatch:
    def __init__(self, keyword, count):
        self.keyword = keyword
        self.count = count


@pytest.fixture
def api():
    service = FakeTranscriptionService()
    runtime = BackendRuntime(workflow_service=service)
    app = create_app(runtime=runtime)
    with TestClient(app) as client:
        yield client, service


def upload_clip(client, content=b"alpha beta", *, filename="sample.wav", keywords=None):
    form = [
        ("keywords", (None, keyword)) for keyword in (keywords or ["alpha", "beta"])
    ]
    form.append(("file", (filename, content, "audio/wav")))
    return client.post(
        "/api/v1/transcriptions/clips",
        files=form,
    )


def wait_for_status(client, workflow_id, expected, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/transcriptions/{workflow_id}")
        if response.status_code == 200 and response.json()["status"] == expected:
            return response.json()
        time.sleep(0.01)
    pytest.fail(f"workflow {workflow_id} did not reach status {expected!r}")


def wait_for_event_type(client, workflow_id, event_type, timeout=10):
    deadline = time.monotonic() + timeout
    cursor = 0
    events = []
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/v1/transcriptions/{workflow_id}/events?after={cursor}"
        )
        assert response.status_code == 200
        page = response.json()
        events.extend(page["events"])
        cursor = page["next_cursor"]
        match = next((event for event in events if event["type"] == event_type), None)
        if match is not None:
            return match, events
        time.sleep(0.01)
    pytest.fail(
        f"workflow {workflow_id} did not emit {event_type!r}; events={events!r}"
    )


class ConcurrentProcessRuntime:
    def __init__(self, inference_barrier):
        self.inference_barrier = inference_barrier

    def transcribe(self, audio, *, language, decoding_options):
        self.inference_barrier.wait(timeout=10)
        return f"worker-{os.getpid()}"

    def close(self):
        pass


class ConcurrentProcessRuntimeFactory:
    def __init__(self, inference_barrier):
        self.inference_barrier = inference_barrier

    def __call__(self, config):
        return ConcurrentProcessRuntime(self.inference_barrier)


class ProcessTestAudioSource:
    sample_rate = 16000
    channels = 1

    def __init__(self, *, device, on_audio, on_error):
        self.on_audio = on_audio

    def start(self):
        # One full default 30-second inference chunk; the fake runtime blocks
        # until both independent model processes enter inference.
        self.on_audio(np.full(480000, 0.3, dtype=np.float32))

    def stop(self):
        pass


def test_repeated_multipart_keywords_create_async_run_with_events_and_status(api):
    client, _ = api

    created = upload_clip(
        client, content=b"alpha beta alpha", keywords=["alpha", "beta"]
    )

    assert created.status_code == 202
    workflow_id = created.json()["workflow_id"]
    assert created.json()["status"] == "queued"
    status = wait_for_status(client, workflow_id, "completed")
    assert status["keywords"] == ["alpha", "beta"]
    assert status["transcript"] == "alpha beta alpha"
    assert status["matches"] == [
        {"keyword": "alpha", "count": 2},
        {"keyword": "beta", "count": 1},
    ]

    events = client.get(f"/api/v1/transcriptions/{workflow_id}/events")
    assert events.status_code == 200
    assert [event["type"] for event in events.json()["events"]] == [
        "queued",
        "started",
        "transcript",
        "match_results",
        "completed",
    ]
    assert events.json()["workflow_id"] == workflow_id


def test_clip_settings_are_snapshotted_and_passed_to_workflow(api):
    client, service = api
    form = [
        ("keywords", (None, "alpha")),
        ("language", (None, "en")),
        ("model", (None, "tiny")),
        ("runtime", (None, "ctranslate2")),
        ("silence_threshold", (None, "0.2")),
        ("decoding_options", (None, '{"beam_size": 3}')),
        ("file", ("sample.wav", b"alpha alpha", "audio/wav")),
    ]

    created = client.post("/api/v1/transcriptions/clips", files=form)

    assert created.status_code == 202
    workflow_id = created.json()["workflow_id"]
    assert created.json()["model"] == "tiny"
    assert created.json()["runtime"] == "ctranslate2"
    assert created.json()["silence_threshold"] == 0.2
    assert created.json()["decoding_options"]["beam_size"] == 3
    status = wait_for_status(client, workflow_id, "completed")
    assert status["model"] == "tiny"
    assert status["runtime"] == "ctranslate2"
    assert status["silence_threshold"] == 0.2
    assert status["decoding_options"]["beam_size"] == 3
    assert service.clip_calls == [
        (
            {
                "language": "en",
                "source_id": workflow_id,
                "silence_threshold": 0.2,
                "decoding_options": {
                    "beam_size": 3,
                    "temperature": 0.0,
                    "condition_on_previous_text": False,
                    "no_repeat_ngram_size": 3,
                    "repetition_penalty": 1.0,
                },
            },
            "tiny",
            "ctranslate2",
        )
    ]
    assert status["language"] == "en"
    match_event = next(
        event for event in client.get(f"/api/v1/transcriptions/{workflow_id}/events").json()["events"]
        if event["type"] == "match_results"
    )
    assert match_event["language"] == "en"


@pytest.mark.parametrize("kind", ["clip", "microphone"])
def test_invalid_language_is_rejected_before_workflow_starts(api, kind):
    client, service = api

    if kind == "clip":
        response = client.post(
            "/api/v1/transcriptions/clips",
            files=[
                ("keywords", (None, "hello")),
                ("language", (None, "fr")),
                ("file", ("sample.wav", b"hello", "audio/wav")),
            ],
        )
        assert not service.clip_calls
    else:
        response = client.post(
            "/api/v1/transcriptions/microphones",
            json={"keywords": ["hello"], "language": "fr"},
        )
        assert not service.microphone_start_options

    assert response.status_code == 422


@pytest.mark.parametrize(
    "settings",
    [
        {"silence_threshold": "1"},
        {"silence_threshold": "nan"},
        {"decoding_options": "[]"},
        {"decoding_options": '{"beam_size": true}'},
    ],
)
def test_invalid_clip_settings_fail_before_run_is_queued(api, settings):
    client, service = api
    form = [("keywords", (None, "alpha"))]
    form.extend((name, (None, value)) for name, value in settings.items())
    form.append(("file", ("sample.wav", b"alpha", "audio/wav")))

    response = client.post("/api/v1/transcriptions/clips", files=form)

    assert response.status_code == 422
    assert client.get("/api/v1/transcriptions").json()["transcriptions"] == []
    assert service.clip_calls == []


@pytest.mark.parametrize(
    ("filename", "content", "keywords"),
    [
        ("sample.mp3", b"audio", ["alpha"]),
        ("sample.wav", b"", ["alpha"]),
        ("sample.wav", b"audio", [""]),
        ("sample.wav", b"audio", ["alpha", "ALPHA"]),
    ],
)
def test_invalid_clip_inputs_are_rejected(api, filename, content, keywords):
    client, _ = api

    response = upload_clip(
        client, content=content, filename=filename, keywords=keywords
    )

    assert response.status_code == 422


def test_microphone_listing_returns_devices_and_maps_unavailability(api):
    client, service = api

    listed = client.get("/api/v1/microphones")
    assert listed.status_code == 200
    assert listed.json() == {"devices": service.microphones}

    service.list_error = WorkflowUnavailableError("audio host is unavailable")
    unavailable = client.get("/api/v1/microphones")
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == "audio host is unavailable"


def test_microphone_can_start_and_stop_through_api(api):
    client, service = api

    started = client.post(
        "/api/v1/transcriptions/microphones",
        json={"keywords": ["alpha"], "device": "Studio Mic"},
    )

    assert started.status_code == 202
    workflow_id = started.json()["workflow_id"]
    assert started.json()["status"] == "recording"
    stop_requested = client.post(f"/api/v1/transcriptions/{workflow_id}/stop")
    assert stop_requested.status_code == 202
    assert service.microphone_workflow.stop_called.wait(timeout=1)
    status = wait_for_status(client, workflow_id, "stopped")
    assert status["kind"] == "microphone"
    event_types = [
        event["type"]
        for event in client.get(f"/api/v1/transcriptions/{workflow_id}/events").json()[
            "events"
        ]
    ]
    assert "started" in event_types
    assert "stopping" in event_types
    assert "completed" in event_types


def test_failed_microphone_stop_does_not_leave_workflow_stopping(api):
    client, service = api
    started = client.post(
        "/api/v1/transcriptions/microphones",
        json={"keywords": ["alpha"], "device": "Studio Mic"},
    )
    workflow_id = started.json()["workflow_id"]
    workflow = service.microphone_workflow
    stop_finished = threading.Event()

    def wait_while_capture_is_active(timeout=0):
        raise TimeoutError("workflow is still active")

    def fail_to_stop(timeout=30):
        stop_finished.set()
        raise RuntimeError("capture device refused to stop")

    workflow.wait = wait_while_capture_is_active
    workflow.stop = fail_to_stop

    response = client.post(f"/api/v1/transcriptions/{workflow_id}/stop")
    assert response.status_code == 202
    assert stop_finished.wait(timeout=1)

    status = wait_for_status(client, workflow_id, "failed")
    assert status["error"] == "capture device refused to stop"
    completed, _ = wait_for_event_type(client, workflow_id, "completed")
    assert completed["status"] == "failed"


def test_microphone_api_exposes_cumulative_matches_before_stop(api, monkeypatch):
    client, service = api

    class LiveSession:
        def __init__(self):
            self.source_id = None
            self.result_queue = queue.Queue()

        def stop(self, *, timeout=None):
            self.result_queue.put(
                {
                    "type": "completed",
                    "source_id": self.source_id,
                    "status": "stopped",
                }
            )

    session = LiveSession()

    def start_microphone(device, keywords, flow_config=None, **_options):
        session.source_id = flow_config["source_id"]
        return forward_session(
            session, None, WordMatchingConfig(keywords=tuple(keywords))
        )

    monkeypatch.setattr(service, "start_microphone", start_microphone)
    started = client.post(
        "/api/v1/transcriptions/microphones",
        json={"keywords": ["alpha", "beta"], "device": "Studio Mic"},
    )
    assert started.status_code == 202
    workflow_id = started.json()["workflow_id"]

    session.result_queue.put(
        {"type": "transcript", "source_id": workflow_id, "sequence": 0, "text": "alpha"}
    )
    first = wait_for_status(client, workflow_id, "recording")
    deadline = time.monotonic() + 2
    while first["matches"] != [
        {"keyword": "alpha", "count": 1}, {"keyword": "beta", "count": 0}
    ] and time.monotonic() < deadline:
        time.sleep(0.01)
        first = client.get(f"/api/v1/transcriptions/{workflow_id}").json()
    assert first["matches"] == [
        {"keyword": "alpha", "count": 1}, {"keyword": "beta", "count": 0}
    ]
    assert first["transcript"] is None

    session.result_queue.put(
        {"type": "transcript", "source_id": workflow_id, "sequence": 1, "text": " alpha"}
    )
    deadline = time.monotonic() + 2
    current = client.get(f"/api/v1/transcriptions/{workflow_id}").json()
    while current["matches"][0]["count"] != 2 and time.monotonic() < deadline:
        time.sleep(0.01)
        current = client.get(f"/api/v1/transcriptions/{workflow_id}").json()
    assert current["matches"] == [
        {"keyword": "alpha", "count": 2}, {"keyword": "beta", "count": 0}
    ]
    assert current["transcript"] is None
    transcript_events = [
        event for event in client.get(
            f"/api/v1/transcriptions/{workflow_id}/events"
        ).json()["events"] if event["type"] == "transcript"
    ]
    assert len(transcript_events) == 2
    assert all("text" not in event for event in transcript_events)

    stopped = client.post(f"/api/v1/transcriptions/{workflow_id}/stop")
    assert stopped.status_code == 202
    final = wait_for_status(client, workflow_id, "stopped")
    assert final["matches"] == current["matches"]


def test_microphone_api_selects_per_workflow_process_mode(api):
    client, service = api

    started = client.post(
        "/api/v1/transcriptions/microphones",
        json={
            "keywords": ["alpha"],
            "device": "Studio Mic",
            "execution_mode": "per_workflow_process",
        },
    )

    assert started.status_code == 202
    assert service.microphone_start_options[0][0:2] == (
        "Studio Mic",
        ("alpha",),
    )
    assert service.microphone_start_options[0][3] == "per_workflow_process"


def test_microphone_api_defaults_to_shared_model_mode(api):
    client, service = api

    started = client.post(
        "/api/v1/transcriptions/microphones",
        json={"keywords": ["alpha"], "device": "Studio Mic"},
    )

    assert started.status_code == 202
    assert service.microphone_start_options[0][3] == "shared"


def test_microphone_api_rejects_unknown_execution_mode(api):
    client, service = api

    response = client.post(
        "/api/v1/transcriptions/microphones",
        json={"keywords": ["alpha"], "execution_mode": "parallel_magic"},
    )

    assert response.status_code == 422
    assert service.microphone_start_options == []


def test_two_per_workflow_process_microphones_infer_concurrently(monkeypatch):
    from speech_to_text.features.model_deployment import process_topology
    from speech_to_text.workflows.transcribe_match_forward import service as workflow_service

    inference_barrier = multiprocessing.get_context("spawn").Barrier(2)
    real_start = process_topology.start_multiprocess_microphone_flows

    def start_with_test_boundaries(**kwargs):
        return real_start(
            **kwargs,
            runtime_factory=ConcurrentProcessRuntimeFactory(inference_barrier),
            audio_source_factory=ProcessTestAudioSource,
        )

    monkeypatch.setattr(
        workflow_service, "start_multiprocess_microphone_flows", start_with_test_boundaries
    )
    runtime = BackendRuntime(workflow_service=TranscriptionService())
    app = create_app(runtime=runtime)
    workflow_ids = []

    with TestClient(app) as client:
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                requests = [
                    executor.submit(
                        client.post,
                        "/api/v1/transcriptions/microphones",
                        json={
                            "keywords": ["worker"],
                            "device": device,
                            "execution_mode": "per_workflow_process",
                        },
                    )
                    for device in ("Virtual Mic A", "Virtual Mic B")
                ]
                responses = [request.result(timeout=20) for request in requests]

            assert all(response.status_code == 202 for response in responses)
            workflow_ids = [response.json()["workflow_id"] for response in responses]
            assert len(set(workflow_ids)) == 2

            observed = [
                wait_for_event_type(client, workflow_id, "transcript")[0]
                for workflow_id in workflow_ids
            ]
            assert all("text" not in event for event in observed)
            assert len({event["pid"] for event in observed}) == 2
            assert all(event["pid"] != os.getpid() for event in observed)
        finally:
            for workflow_id in workflow_ids:
                client.post(f"/api/v1/transcriptions/{workflow_id}/stop")
            for workflow_id in workflow_ids:
                wait_for_status(client, workflow_id, "stopped", timeout=15)


def test_concurrent_clip_runs_have_distinct_ids_and_independent_event_cursors(api):
    client, service = api
    service.transcription_gate = threading.Event()

    first = upload_clip(client, content=b"first alpha", keywords=["alpha"])
    second = upload_clip(client, content=b"second beta", keywords=["beta"])

    assert first.status_code == second.status_code == 202
    first_id = first.json()["workflow_id"]
    second_id = second.json()["workflow_id"]
    assert first_id != second_id
    assert service.transcription_started.wait(timeout=1)
    first_events = client.get(f"/api/v1/transcriptions/{first_id}/events?after=1")
    second_events = client.get(f"/api/v1/transcriptions/{second_id}/events?after=1")
    assert first_events.status_code == second_events.status_code == 200
    assert first_events.json()["workflow_id"] == first_id
    assert second_events.json()["workflow_id"] == second_id
    assert first_events.json()["events"][0]["workflow_id"] == first_id
    assert second_events.json()["events"][0]["workflow_id"] == second_id
    assert first_events.json()["events"][0]["cursor"] == 2
    assert second_events.json()["events"][0]["cursor"] == 2
    assert (
        client.get(f"/api/v1/transcriptions/{first_id}/events?after=2").json()["events"]
        == []
    )
    assert (
        client.get(f"/api/v1/transcriptions/{second_id}/events?after=2").json()[
            "events"
        ]
        == []
    )

    service.transcription_gate.set()
    first_status = wait_for_status(client, first_id, "completed")
    second_status = wait_for_status(client, second_id, "completed")
    assert first_status["transcript"] == "first alpha"
    assert second_status["transcript"] == "second beta"
    assert (
        client.get(f"/api/v1/transcriptions/{first_id}/events?after=1").json()[
            "events"
        ][0]["type"]
        == "started"
    )
    assert (
        client.get(f"/api/v1/transcriptions/{second_id}/events?after=1").json()[
            "events"
        ][0]["type"]
        == "started"
    )


def test_unknown_runs_and_invalid_event_cursors_have_http_errors(api):
    client, _ = api

    unknown = "missing-run"
    assert client.get(f"/api/v1/transcriptions/{unknown}").status_code == 404
    assert client.get(f"/api/v1/transcriptions/{unknown}/events").status_code == 404

    created = upload_clip(client)
    workflow_id = created.json()["workflow_id"]
    wait_for_status(client, workflow_id, "completed")
    ahead = client.get(f"/api/v1/transcriptions/{workflow_id}/events?after=99")
    assert ahead.status_code == 422
