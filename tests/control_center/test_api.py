"""Local API tests invoke Feature 01 itself and inject only runtime/capture edges."""

from fastapi.testclient import TestClient
import numpy as np
import subprocess
import sys
import time
import pytest

from speech_to_text.control_center import create_app
from speech_to_text.features.model_deployment import ChunkInferenceWarning
from speech_to_text.control_center.registry import FeatureContribution, FeatureRegistry
from tests.feature_01.support import ManualAudioSource, RuntimeFactory, ScriptedRuntime, wav_bytes


class CollectingTelemetryWriter:
    def __init__(self, *, fail=False):
        self.records = []
        self.fail = fail
        self.dropped_count = 0
        self.write_failures = 0
        self.last_error = None
        self.queue_depth = 0

    def start(self):
        pass

    def close(self):
        pass

    def publish_event(self, record):
        if self.fail and record.get("feature_id") == "feature-01-model-deployment":
            raise OSError("injected writer failure")
        self.records.append(dict(record))
        return True

    def publish_measurement(self, record):
        if self.fail:
            raise OSError("injected writer failure")
        self.records.append(dict(record))
        return True

    def publish_sample(self, _kind, _record):
        return True


def app_with(runtime_factory=None, audio_source_factory=None):
    return create_app(feature_options={"runtime_factory": runtime_factory,
                                       "audio_source_factory": audio_source_factory})


class ProcessBoundaryRuntime:
    def transcribe(self, audio, *, language, decoding_options):
        return f"{language}:{len(audio)}"

    def close(self):
        pass


class ProcessBoundaryRuntimeFactory:
    def __call__(self, _config):
        return ProcessBoundaryRuntime()


class FailingCapacityRuntime(ProcessBoundaryRuntime):
    def transcribe(self, audio, *, language, decoding_options):
        raise RuntimeError("injected decode failure")


class FailingCapacityRuntimeFactory:
    def __call__(self, _config):
        return FailingCapacityRuntime()


class UnavailableCapacityRuntimeFactory:
    def __call__(self, _config):
        raise RuntimeError("injected runtime unavailable")


class ProcessBoundaryAudioSource:
    sample_rate = 16000
    channels = 1

    def __init__(self, *, device, on_audio, on_error):
        self.device, self.on_audio, self.on_error = device, on_audio, on_error

    def start(self):
        self.on_audio(np.full(2000, 0.4, dtype=np.float32))

    def stop(self):
        pass


def test_registered_feature_and_static_page_are_served_same_origin():
    with TestClient(create_app()) as client:
        assert client.get("/").status_code == 200
        assert "feature-page" in client.get("/").text
        features = client.get("/api/features").json()
        assert [feature["id"] for feature in features] == ["feature-01-model-deployment"]
        assert client.get("/api/features/system-observation").status_code == 404
        assert client.get("/api/features/system-observation/latest").status_code == 404
        assert client.get("/assets/features/system-observation/page.html").status_code == 404
        assert features[0]["implementation_status"] == "implemented"
        assert "hardware-pending" in features[0]["verification_status"]
        assert client.get("/api/features/feature-01-model-deployment").status_code == 200
        assert client.get("/api/features/unregistered").status_code == 404
        feature = features[0]
        template = client.get(feature["page_template"])
        module = client.get(feature["page_module"])
        assert "signal-spine" in template.text
        assert "export async function mount" in module.text
        assert client.get(feature["page_stylesheet"]).status_code == 200


def test_unregistered_feature_is_not_exposed():
    with TestClient(create_app(registry=FeatureRegistry())) as client:
        assert client.get("/api/features").json() == []
        assert client.get("/api/features/feature-01-model-deployment/catalog").status_code == 404


def test_registered_second_contribution_supplies_its_own_page_assets():
    feature = FeatureContribution(
        id="fixture-feature-two", name="Fixture feature two", summary="An independently mounted test page.",
        spec=".scratch/feature-test-console/spec.md", page_module="/assets/features/registry-test/page.js",
        page_template="/assets/features/registry-test/page.html",
        page_stylesheet="/assets/features/registry-test/page.css", router_factory=lambda: __import__("fastapi").APIRouter())
    registry = FeatureRegistry([feature])
    with TestClient(create_app(registry=registry)) as client:
        registered, = client.get("/api/features").json()
        assert registered["id"] == "fixture-feature-two"
        assert registered["page_module"] == feature.page_module
        assert registered["page_template"] == feature.page_template
        assert "belongs to its own feature contribution" in client.get(registered["page_template"]).text
        assert "export function mount" in client.get(registered["page_module"]).text
        shell = client.get("/assets/control-center.js").text
        assert "feature.page_template" in shell and "feature.page_module" in shell
        assert "feature-01-model-deployment" not in shell


def test_cli_rejects_non_loopback_host_before_starting_server():
    result = subprocess.run([sys.executable, "-m", "speech_to_text.control_center",
                             "--host", "0.0.0.0"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "loopback bind addresses" in result.stderr


def test_catalog_model_lifecycle_and_wav_clip_use_feature_public_api():
    runtime = ScriptedRuntime(["recognized speech"])
    factory = RuntimeFactory(runtime)
    with TestClient(app_with(factory)) as client:
        catalog = client.get("/api/features/feature-01-model-deployment/catalog")
        assert catalog.status_code == 200
        assert any(item["key"] == "turbo" for item in catalog.json()["models"])
        loaded = client.post("/api/features/feature-01-model-deployment/models", json={
            "model": "turbo", "runtime": "ctranslate2", "precision": "int8"})
        assert loaded.status_code == 200, loaded.text
        handle_id = loaded.json()["handle_id"]
        assert loaded.json()["state"] == "ready"
        audio = wav_bytes(np.full(1600, 10000, dtype=np.int16))
        result = client.post("/api/features/feature-01-model-deployment/clips", data={
            "handle_id": handle_id, "language": "en", "chunk_seconds": "0.1", "silence_threshold": "0.05"},
            files={"file": ("input.wav", audio, "audio/wav")})
        assert result.status_code == 200, result.text
        assert result.json()["transcript"] == "recognized speech"
        assert result.json()["configuration"]["runtime"] == "ctranslate2"
        assert len(factory.configs) == 1
        closed = client.delete(f"/api/features/feature-01-model-deployment/models/{handle_id}")
        assert closed.json()["state"] == "closed"
        assert runtime.closed


def test_telemetry_events_persist_lifecycle_and_terminal_without_sse_reads():
    writer = CollectingTelemetryWriter()
    runtime = ScriptedRuntime(["private transcript"])
    source = ManualAudioSource()
    app = create_app(feature_options={"runtime_factory": RuntimeFactory(runtime),
        "audio_source_factory": source}, telemetry_writer=writer)
    with TestClient(app) as client:
        prefix = "/api/features/feature-01-model-deployment"
        model = client.post(f"{prefix}/models", json={"model": "turbo", "runtime": "ctranslate2",
            "precision": "int8"}).json()
        started = client.post(f"{prefix}/microphones", json={"handle_id": model["handle_id"],
            "flow_config": {"source_id": "persisted-session", "language": "en", "chunk_seconds": .1}})
        assert started.status_code == 200
        source.push(np.full(1600, .4, dtype=np.float32))
        stopped = client.post(f"{prefix}/sessions/persisted-session/stop")
        assert stopped.status_code == 200
        client.delete(f"{prefix}/models/{model['handle_id']}")

    events = [record for record in writer.records if record.get("kind") == "event"]
    names = [record["event_name"] for record in events]
    assert names.count("model_loaded") == 1
    assert names.count("model_closed") == 1
    assert names.count("microphone_session_started") == 1
    assert names.count("microphone_session_completed") == 1
    assert any(record.get("source_id") == "persisted-session" for record in events)
    assert "private transcript" not in repr(writer.records)
    assert sum(record["event_name"] == "microphone_session_completed" for record in events) == 1


def test_microphone_failure_persists_stable_error_code_without_exception_text():
    writer = CollectingTelemetryWriter()
    source = ManualAudioSource()
    app = create_app(feature_options={"runtime_factory": RuntimeFactory(ScriptedRuntime([])),
        "audio_source_factory": source}, telemetry_writer=writer)
    with TestClient(app) as client:
        prefix = "/api/features/feature-01-model-deployment"
        model = client.post(f"{prefix}/models", json={"model": "turbo", "runtime": "ctranslate2",
            "precision": "int8"}).json()
        started = client.post(f"{prefix}/microphones", json={"handle_id": model["handle_id"],
            "flow_config": {"source_id": "capture-error-source", "language": "en"}})
        assert started.status_code == 200
        source.on_error(RuntimeError("private device diagnostic"))
        session = app.state.feature_state["feature-01-model-deployment"].sessions["capture-error-source"]
        deadline = time.monotonic() + 2
        while not session._terminal and time.monotonic() < deadline:
            time.sleep(.01)
        assert session._terminal

    events = [record for record in writer.records if record.get("kind") == "event"]
    errors = [record for record in events if record["event_name"] == "microphone_session_error"]
    assert len(errors) == 1
    assert errors[0]["source_id"] == "capture-error-source"
    assert errors[0]["severity"] == "error"
    assert errors[0]["attributes"]["code"] == "CAPTURE_FAILED"
    assert "private device diagnostic" not in repr(writer.records)
    assert sum(record["event_name"] == "microphone_session_completed" for record in events) == 1


def test_disabled_or_failing_telemetry_never_changes_transcription_or_shutdown():
    runtime = ScriptedRuntime(["still transcribes"])
    source = ManualAudioSource()
    writer = CollectingTelemetryWriter(fail=True)
    app = create_app(feature_options={"runtime_factory": RuntimeFactory(runtime),
        "audio_source_factory": source}, telemetry_writer=writer)
    with TestClient(app) as client:
        prefix = "/api/features/feature-01-model-deployment"
        loaded = client.post(f"{prefix}/models", json={"model": "turbo", "runtime": "ctranslate2",
            "precision": "int8"})
        assert loaded.status_code == 200, loaded.text
        session = client.post(f"{prefix}/microphones", json={"handle_id": loaded.json()["handle_id"],
            "flow_config": {"source_id": "writer-failure-session", "language": "en", "chunk_seconds": .1}})
        assert session.status_code == 200, session.text
        source.push(np.full(1600, .4, dtype=np.float32))
        assert client.post(f"{prefix}/sessions/writer-failure-session/stop").status_code == 200
    assert runtime.closed
    assert len(runtime.audio) == 1


def test_failing_telemetry_does_not_change_finite_clip_result():
    runtime = ScriptedRuntime([RuntimeError("decode failed")])
    writer = CollectingTelemetryWriter(fail=True)
    app = create_app(feature_options={"runtime_factory": RuntimeFactory(runtime)}, telemetry_writer=writer)
    with TestClient(app) as client:
        prefix = "/api/features/feature-01-model-deployment"
        model = client.post(f"{prefix}/models", json={"model": "turbo", "runtime": "ctranslate2",
            "precision": "int8"}).json()
        with pytest.warns(ChunkInferenceWarning, match="decode failed"):
            response = client.post(f"{prefix}/clips", data={"handle_id": model["handle_id"], "language": "en",
                "chunk_seconds": ".1", "silence_threshold": "0"},
                files={"file": ("input.wav", wav_bytes(np.full(1600, 10000, dtype=np.int16)), "audio/wav")})
        assert response.status_code == 200, response.text
        assert response.json()["transcript"] == ""


@pytest.mark.parametrize("topology", ["shared-model", "per-input-model"])
def test_process_group_terminal_events_persist_once_without_result_consumer(topology):
    writer = CollectingTelemetryWriter()
    app = create_app(feature_options={"runtime_factory": ProcessBoundaryRuntimeFactory(),
        "process_audio_source_factory": ProcessBoundaryAudioSource}, telemetry_writer=writer)
    with TestClient(app) as client:
        prefix = "/api/features/feature-01-model-deployment"
        started = client.post(f"{prefix}/process-groups", json={"devices": ["event-source"],
            "topology": topology, "model_config": {"model": "turbo", "runtime": "ctranslate2",
            "precision": "int8"}, "flow_config": {"source_id": "group-event-source", "language": "en",
            "chunk_seconds": .1, "silence_threshold": 0}})
        assert started.status_code == 200, started.text
        group_id = started.json()["group_id"]
        assert client.post(f"{prefix}/process-groups/{group_id}/stop").status_code == 200
        assert client.post(f"{prefix}/process-groups/{group_id}/stop").status_code == 200

    events = [record for record in writer.records if record.get("kind") == "event"]
    completed = [record for record in events if record["event_name"] == "microphone_session_completed"]
    assert len(completed) == 1
    assert completed[0]["source_id"] == "group-event-source"
    assert completed[0]["attributes"]["topology"] == topology
    assert completed[0]["pid"] != __import__("os").getpid()
    assert sum(record["event_name"] == "process_group_started" for record in events) == 1
    assert sum(record["event_name"] == "process_group_stopped" for record in events) == 1
    measurements = [record for record in writer.records if record.get("feature_id") == "feature-01-model-deployment"
                    and "inference_seconds" in record]
    assert measurements
    assert all(record["source_id"] == "group-event-source" for record in measurements)
    assert all(record["pid"] != __import__("os").getpid() for record in measurements)
    assert all(record["rtf"] is not None for record in measurements)


def test_shutdown_persists_model_and_group_closure_without_explicit_stop_calls():
    writer = CollectingTelemetryWriter()
    app = create_app(feature_options={"runtime_factory": ProcessBoundaryRuntimeFactory(),
        "process_audio_source_factory": ProcessBoundaryAudioSource}, telemetry_writer=writer)
    with TestClient(app) as client:
        prefix = "/api/features/feature-01-model-deployment"
        model = client.post(f"{prefix}/models", json={"model": "turbo", "runtime": "ctranslate2",
            "precision": "int8"}).json()
        group = client.post(f"{prefix}/process-groups", json={"devices": ["shutdown-source"],
            "topology": "shared-model", "model_config": {"model": "turbo", "runtime": "ctranslate2",
            "precision": "int8"}, "flow_config": {"source_id": "shutdown-group-source", "language": "en",
            "chunk_seconds": .1, "silence_threshold": 0}}).json()

    events = [record for record in writer.records if record.get("kind") == "event"]
    assert sum(record["event_name"] == "model_closed" and record["source_id"] == model["handle_id"]
               for record in events) == 1
    assert sum(record["event_name"] == "process_group_stopped" and record["source_id"] == group["group_id"]
               for record in events) == 1


def test_microphone_api_streams_ordered_feature_events_and_stops_cleanly():
    runtime = ScriptedRuntime(["first chunk", "tail"])
    factory = RuntimeFactory(runtime)
    source = ManualAudioSource()
    with TestClient(app_with(factory, source)) as client:
        prefix = "/api/features/feature-01-model-deployment"
        loaded = client.post(f"{prefix}/models", json={"model": "turbo", "runtime": "ctranslate2", "precision": "int8"}).json()
        started = client.post(f"{prefix}/microphones", json={"handle_id": loaded["handle_id"],
            "flow_config": {"source_id": "api-test-source", "language": "en", "chunk_seconds": 0.1}})
        assert started.status_code == 200, started.text
        source.push(np.full(1600, 0.4, dtype=np.float32))
        source.push(np.full(800, 0.4, dtype=np.float32))
        stopped = client.post(f"{prefix}/sessions/api-test-source/stop")
        assert stopped.status_code == 200, stopped.text
        response = client.get(f"{prefix}/events/api-test-source")
        events = [__import__("json").loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert [event["event_sequence"] for event in events] == list(range(len(events)))
        assert [event["type"] for event in events] == ["transcript", "transcript", "completed"]
        assert [event["text"] for event in events[:2]] == ["first chunk", "tail"]
        assert events[-1]["status"] == "stopped"
        assert source.stopped


def test_model_load_error_remains_actionable_and_never_claims_ready():
    def fail_load(_config):
        raise RuntimeError("runtime package missing")
    with TestClient(app_with(fail_load)) as client:
        response = client.post("/api/features/feature-01-model-deployment/models", json={"model": "turbo"})
        assert response.status_code == 400
        assert "runtime package missing" in response.json()["detail"]["message"]


def test_application_shutdown_stops_sessions_and_closes_owned_models():
    runtime = ScriptedRuntime([])
    source = ManualAudioSource()
    app = app_with(RuntimeFactory(runtime), source)
    with TestClient(app) as client:
        prefix = "/api/features/feature-01-model-deployment"
        model = client.post(f"{prefix}/models", json={"model": "turbo", "runtime": "ctranslate2", "precision": "int8"}).json()
        started = client.post(f"{prefix}/microphones", json={"handle_id": model["handle_id"],
            "flow_config": {"source_id": "shutdown-test", "language": "en"}})
        assert started.status_code == 200
    assert source.stopped
    assert runtime.closed
    state = app.state.feature_state["feature-01-model-deployment"]
    assert state.models[model["handle_id"]].state == "closed"


def test_process_group_routes_source_events_through_both_feature_operations():
    app = create_app(feature_options={"runtime_factory": ProcessBoundaryRuntimeFactory(),
        "process_audio_source_factory": ProcessBoundaryAudioSource})
    with TestClient(app) as client:
        prefix = "/api/features/feature-01-model-deployment"
        started = client.post(f"{prefix}/process-groups", json={
            "devices": ["thai-input", "english-input"], "topology": "shared-model",
            "model_config": {"model": "turbo", "runtime": "ctranslate2", "precision": "int8"},
            "flow_config": {"language": "en", "chunk_seconds": 0.1, "silence_threshold": 0},
            "flow_configs": [
                {"source_id": "api-thai", "language": "th", "chunk_seconds": 0.1},
                {"source_id": "api-english", "language": "en", "chunk_seconds": 0.2}]})
        assert started.status_code == 200, started.text
        group = started.json()
        assert [session["source_id"] for session in group["sessions"]] == ["api-thai", "api-english"]
        stopped = client.post(f"{prefix}/process-groups/{group['group_id']}/stop")
        assert stopped.status_code == 200, stopped.text
        import json
        events_by_source = {}
        for session in group["sessions"]:
            response = client.get(f"{prefix}/events/{session['source_id']}")
            events_by_source[session["source_id"]] = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert [event["text"] for event in events_by_source["api-thai"] if event["type"] == "transcript"] == ["th:1600", "th:400"]
        assert [event["text"] for event in events_by_source["api-english"] if event["type"] == "transcript"] == ["en:2000"]
        assert all(events[-1]["type"] == "completed" for events in events_by_source.values())


def test_capacity_api_reports_injected_measurements_and_inconclusive_workload():
    app = create_app(feature_options={"runtime_factory": ProcessBoundaryRuntimeFactory(),
        "process_audio_source_factory": ProcessBoundaryAudioSource})
    with TestClient(app) as client:
        result = client.post("/api/features/feature-01-model-deployment/capacity", json={
            "devices": ["injected-input"], "topology": "shared-model", "duration_seconds": 0.1,
            "model": "turbo", "runtime": "ctranslate2", "precision": "int8", "language": "en",
            "chunk_seconds": 0.1, "silence_threshold": 0, "flow_configs": [
                {"language": "en", "chunk_seconds": 0.1, "silence_threshold": 0}]})
        assert result.status_code == 200, result.text
        report = result.json()
        assert report["status"] == "completed"
        assert report["realtime_verdict"] == "inconclusive-insufficient-workload"
        assert report["eligible_audio_seconds_by_source"]
        assert report["queue_depth_measurement"] == "shared-model-input-queue"


def test_capacity_api_distinguishes_failure_from_unavailable_prerequisites():
    payload = {"devices": ["injected-input"], "topology": "shared-model", "duration_seconds": 0.1,
        "model": "turbo", "runtime": "ctranslate2", "precision": "int8", "language": "en",
        "chunk_seconds": 0.1, "silence_threshold": 0}
    failing = create_app(feature_options={"runtime_factory": FailingCapacityRuntimeFactory(),
        "process_audio_source_factory": ProcessBoundaryAudioSource})
    with TestClient(failing) as client:
        report = client.post("/api/features/feature-01-model-deployment/capacity", json=payload)
        assert report.status_code == 200, report.text
        assert report.json()["status"] == "completed"
        assert report.json()["realtime_verdict"] == "fail"
        assert report.json()["failed_inference_chunks"] > 0
    unavailable = create_app(feature_options={"runtime_factory": UnavailableCapacityRuntimeFactory(),
        "process_audio_source_factory": ProcessBoundaryAudioSource})
    with TestClient(unavailable) as client:
        report = client.post("/api/features/feature-01-model-deployment/capacity", json=payload)
        assert report.status_code == 200, report.text
        assert report.json()["status"] == "unavailable"
        assert report.json()["measurements"] is None
        assert "injected runtime unavailable" in report.json()["prerequisite_error"]


def test_browser_shell_explains_audio_boundaries_and_has_keyboard_focus_styles():
    with TestClient(create_app()) as client:
        page = client.get("/assets/features/feature-01/page.html").text
        shell = client.get("/assets/control-center.js").text
        style = client.get("/assets/control-center.css").text
        assert 'id="source-configs"' in page and 'id="run-capacity"' in page
        assert "feature.page_template" in shell and "feature.page_module" in shell
        assert "Browser microphone permission is not used" in page
        assert "signal-spine" in page and "reference-verified" in page
        assert ":focus-visible" in style and "prefers-reduced-motion" in style
