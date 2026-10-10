"""Public HTTP behavior for saved microphone profiles and profile runs."""

from __future__ import annotations

import queue
import sqlite3
import unicodedata

import pytest
from fastapi.testclient import TestClient

from speech_to_text.backend.app import create_app
from speech_to_text.backend.dependencies import BackendRuntime
from speech_to_text.backend.profile_store import SQLiteProfileStore
from speech_to_text.workflows.transcribe_match_forward import WorkflowConfigurationError


class FakeMicrophoneWorkflow:
    def __init__(self):
        self.output_queue = queue.Queue()
        self.output_queue.put({"type": "completed", "status": "completed"})

    def wait(self, timeout=0):
        return None

    def stop(self, timeout=30):
        self.output_queue.put({"type": "completed", "status": "stopped"})


class FakeTranscriptionService:
    """Stand-in at the workflow-service boundary; it needs no audio devices."""

    def __init__(self):
        self.microphone_start_options = []
        self.default_model = "turbo"
        self.default_runtime = "openvino-gpu"
        self.models = ["turbo", "small"]
        self.runtime_options = [
            {"key": key, "compatible": True, "ready": key == "openvino-gpu", "precision_options": [], "reason": None if key == "openvino-gpu" else "runtime unavailable"}
            for key in ("openvino-gpu", "openvino-cpu", "ctranslate2")
        ]

    def validate_model_key(self, model):
        if model not in self.models:
            raise WorkflowConfigurationError(f"Unknown model key {model!r}")

    def list_models(self):
        return {
            "default_model": self.default_model,
            "default_runtime": self.default_runtime,
            "models": [
                {"key": key, "display_name": key.title(), "installed": True, "ready": True, "runtimes": self.runtime_options}
                for key in self.models
            ],
        }

    def validate_model_runtime(self, model, runtime):
        self.validate_model_key(model)
        if runtime not in {item["key"] for item in self.runtime_options}:
            raise WorkflowConfigurationError(f"Unsupported runtime {runtime!r}")

    def validate_keywords(self, keywords):
        if not keywords or any(
            not isinstance(word, str) or not word.strip() for word in keywords
        ):
            raise WorkflowConfigurationError("keywords must contain non-empty strings")
        normalized = [unicodedata.normalize("NFC", word).casefold() for word in keywords]
        if len(normalized) != len(set(normalized)):
            raise WorkflowConfigurationError("keywords must be unique")
        return tuple(word.strip() for word in keywords)

    def start_microphone(
        self, device, keywords, flow_config=None, *, execution_mode="shared", model=None, runtime=None
    ):
        self.microphone_start_options.append(
            (device, tuple(keywords), flow_config, execution_mode, model, runtime)
        )
        return FakeMicrophoneWorkflow()

    def close(self):
        pass


def make_client(database_path):
    service = FakeTranscriptionService()
    runtime = BackendRuntime(
        workflow_service=service,
        profile_store=SQLiteProfileStore(database_path),
    )
    return TestClient(create_app(runtime=runtime)), service


@pytest.fixture
def api(tmp_path):
    client, service = make_client(tmp_path / "profiles.sqlite3")
    with client:
        yield client, service


def profile_body(**overrides):
    body = {
        "name": "Thai vocabulary",
        "device": "Studio Mic",
        "execution_mode": "per_workflow_process",
        "keywords": ["สวัสดี", "ขอบคุณ"],
    }
    body.update(overrides)
    return body


def test_profiles_can_be_created_listed_replaced_and_deleted(api):
    client, _ = api

    assert client.get("/api/v1/profiles").json() == {"profiles": []}
    assert client.get("/api/v1/models").json()["default_model"] == "turbo"
    created = client.post("/api/v1/profiles", json=profile_body(name="  Thai vocabulary  "))

    assert created.status_code == 201
    profile = created.json()
    assert profile["profile_id"]
    assert profile["name"] == "Thai vocabulary"
    assert profile["device"] == "Studio Mic"
    assert profile["execution_mode"] == "per_workflow_process"
    assert profile["keywords"] == ["สวัสดี", "ขอบคุณ"]
    assert profile["silence_threshold"] == 0.0
    assert profile["model"] == "turbo"
    assert profile["runtime"] == "openvino-gpu"
    assert client.get("/api/v1/models").json()["models"][0]["runtimes"]
    assert profile["created_at"]
    assert profile["updated_at"] == profile["created_at"]
    assert client.get("/api/v1/profiles").json() == {"profiles": [profile]}
    assert client.get(f"/api/v1/profiles/{profile['profile_id']}").json() == profile

    replaced = client.put(
        f"/api/v1/profiles/{profile['profile_id']}",
        json=profile_body(
            name="Updated vocabulary",
            device=None,
            execution_mode="shared",
            keywords=["ไป", "มา"],
            silence_threshold=0.12,
            model="small",
            runtime="ctranslate2",
        ),
    )

    assert replaced.status_code == 200
    assert replaced.json()["profile_id"] == profile["profile_id"]
    assert replaced.json()["name"] == "Updated vocabulary"
    assert replaced.json()["device"] is None
    assert replaced.json()["execution_mode"] == "shared"
    assert replaced.json()["keywords"] == ["ไป", "มา"]
    assert replaced.json()["silence_threshold"] == 0.12
    assert replaced.json()["model"] == "small"
    assert replaced.json()["runtime"] == "ctranslate2"
    assert replaced.json()["updated_at"] >= profile["updated_at"]
    assert (
        client.get(f"/api/v1/profiles/{profile['profile_id']}").json()
        == replaced.json()
    )

    deleted = client.delete(f"/api/v1/profiles/{profile['profile_id']}")

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get("/api/v1/profiles").json() == {"profiles": []}


def test_profile_list_is_alphabetical(api):
    client, _ = api
    client.post("/api/v1/profiles", json=profile_body(name="Zulu"))
    client.post("/api/v1/profiles", json=profile_body(name="Alpha"))

    listed = client.get("/api/v1/profiles")

    assert [profile["name"] for profile in listed.json()["profiles"]] == [
        "Alpha",
        "Zulu",
    ]


@pytest.mark.parametrize(
    "body",
    [
        profile_body(name="   "),
        profile_body(device="  "),
        profile_body(execution_mode="unsupported"),
        profile_body(keywords=[]),
        profile_body(keywords=["same", "SAME"]),
        profile_body(silence_threshold=-0.01),
        profile_body(silence_threshold=1),
        profile_body(silence_threshold=False),
        profile_body(silence_threshold="NaN"),
        profile_body(model="unknown-model"),
        profile_body(runtime="runtime-typo"),
        profile_body(model="small", runtime="unsupported-model-runtime"),
        {**profile_body(), "unexpected": "field"},
    ],
)
def test_profile_creation_rejects_invalid_definitions(api, body):
    client, _ = api

    response = client.post("/api/v1/profiles", json=body)

    assert response.status_code == 422
    assert client.get("/api/v1/profiles").json() == {"profiles": []}


def test_profile_update_rejects_out_of_range_silence_threshold(api):
    client, _ = api
    created = client.post("/api/v1/profiles", json=profile_body())
    profile = created.json()

    response = client.put(
        f"/api/v1/profiles/{profile['profile_id']}",
        json=profile_body(silence_threshold=1),
    )

    assert response.status_code == 422
    assert (
        client.get(f"/api/v1/profiles/{profile['profile_id']}").json() == profile
    )


@pytest.mark.parametrize("method", ["get", "put", "delete", "run"])
def test_unknown_profile_ids_return_not_found(api, method):
    client, _ = api
    profile_id = "unknown-profile"

    if method == "get":
        response = client.get(f"/api/v1/profiles/{profile_id}")
    elif method == "put":
        response = client.put(
            f"/api/v1/profiles/{profile_id}", json=profile_body()
        )
    elif method == "delete":
        response = client.delete(f"/api/v1/profiles/{profile_id}")
    else:
        response = client.post(f"/api/v1/profiles/{profile_id}/runs")

    assert response.status_code == 404


@pytest.mark.parametrize("execution_mode", ["shared", "per_workflow_process"])
def test_profile_run_uses_saved_snapshot_after_profile_edit_and_delete(api, execution_mode):
    client, service = api
    created = client.post(
        "/api/v1/profiles", json=profile_body(execution_mode=execution_mode, silence_threshold=0.18, model="small", runtime="ctranslate2")
    )
    assert created.status_code == 201
    profile = created.json()

    started = client.post(f"/api/v1/profiles/{profile['profile_id']}/runs")

    assert started.status_code == 202
    run = started.json()
    assert run["status"] == "recording"
    assert service.microphone_start_options[0][0] == "Studio Mic"
    assert service.microphone_start_options[0][1] == ("สวัสดี", "ขอบคุณ")
    assert service.microphone_start_options[0][2] == {
        "language": "th",
        "source_id": run["workflow_id"],
        "silence_threshold": 0.18,
    }
    assert service.microphone_start_options[0][3] == execution_mode
    assert service.microphone_start_options[0][4] == "small"
    assert service.microphone_start_options[0][5] == "ctranslate2"

    edited = client.put(
        f"/api/v1/profiles/{profile['profile_id']}",
        json=profile_body(
            name="Changed profile",
            device="Other Mic",
            execution_mode="shared",
            keywords=["ใหม่"],
            silence_threshold=0.3,
        ),
    )
    assert edited.status_code == 200
    deleted = client.delete(f"/api/v1/profiles/{profile['profile_id']}")
    assert deleted.status_code == 204

    listed = client.get("/api/v1/transcriptions")

    assert listed.status_code == 200
    transcription = next(
        item
        for item in listed.json()["transcriptions"]
        if item["workflow_id"] == run["workflow_id"]
    )
    assert transcription["kind"] == "microphone"
    assert transcription["profile_id"] == profile["profile_id"]
    assert transcription["profile_name"] == profile["name"]
    assert transcription["device"] == profile["device"]
    assert transcription["execution_mode"] == profile["execution_mode"]
    assert transcription["keywords"] == profile["keywords"]
    assert transcription["silence_threshold"] == profile["silence_threshold"]
    assert transcription["model"] == "small"
    assert transcription["runtime"] == "ctranslate2"


def test_profile_survives_a_new_app_and_runtime_lifecycle(tmp_path):
    database_path = tmp_path / "profiles.sqlite3"
    first_client, _ = make_client(database_path)

    with first_client:
        created = first_client.post("/api/v1/profiles", json=profile_body())
        assert created.status_code == 201
        profile = created.json()

    second_client, _ = make_client(database_path)
    with second_client:
        retrieved = second_client.get(f"/api/v1/profiles/{profile['profile_id']}")
        listed = second_client.get("/api/v1/profiles")

    assert retrieved.status_code == 200
    assert retrieved.json() == profile
    assert listed.status_code == 200
    assert listed.json() == {"profiles": [profile]}


def test_profile_store_migrates_existing_table_without_losing_profiles(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "legacy-profiles.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.execute(
        """CREATE TABLE workflow_profiles (
            profile_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            device TEXT,
            execution_mode TEXT NOT NULL,
            keywords_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        "INSERT INTO workflow_profiles VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "existing-profile",
            "Existing",
            None,
            "shared",
            '["สวัสดี"]',
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        ),
    )
    connection.commit()
    connection.close()

    monkeypatch.setenv("SPEECH_TO_TEXT_MODEL", "small")
    monkeypatch.setenv("SPEECH_TO_TEXT_RUNTIME", "openvino-cpu")
    store = SQLiteProfileStore(database_path)
    profiles = store.list_profiles()
    columns = [
        row["name"]
        for row in store._db().execute("PRAGMA table_info(workflow_profiles)")
    ]
    store.close()

    assert columns.count("silence_threshold") == 1
    assert columns.count("model") == 1
    assert profiles[0]["profile_id"] == "existing-profile"
    assert profiles[0]["silence_threshold"] == 0.0
    assert profiles[0]["model"] == "small"
    assert profiles[0]["runtime"] == "openvino-cpu"

    migrated_store = SQLiteProfileStore(database_path)
    assert migrated_store.get_profile("existing-profile")["silence_threshold"] == 0.0
    assert migrated_store.get_profile("existing-profile")["model"] == "small"
    monkeypatch.setenv("SPEECH_TO_TEXT_RUNTIME", "ctranslate2")
    assert migrated_store.get_profile("existing-profile")["runtime"] == "openvino-cpu"
    migrated_store.close()
