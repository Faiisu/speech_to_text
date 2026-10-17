import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from speech_to_text.backend.app import create_app
from speech_to_text.backend.dependencies import BackendRuntime, RunRecord, StressTestRecord
from speech_to_text.workflows.model_management import ModelManagementService


class CatalogService:
    def list_models(self):
        return {"default_model": "turbo", "default_runtime": "openvino-gpu", "models": [
            {"key": "turbo", "display_name": "Turbo", "repository": "org/turbo", "revision": "abc123", "installed": True, "ready": True, "runtimes": []}
        ]}

    def close(self):
        pass


def test_model_inspection_jobs_and_catalog_selection():
    gate = threading.Event()
    received_tokens = []

    def inspect(repository, *, revision, token):
        received_tokens.append(token)
        return {
            "repository": repository,
            "revision": "abc123",
            "compatible": True,
            "reasons": [],
            "model_type": "whisper",
            "runtimes": [],
        }

    def prepare(repository, model_key, **options):
        received_tokens.append(options["token"])
        gate.wait(timeout=2)
        return {"model_key": model_key, "repository": repository, "revision": "abc123",
                "downloaded": True, "converted": True, "verified": True,
                "runtime": options["runtime"], "precision": options["precision"], "reason": None}

    service = ModelManagementService(inspector=inspect, preparer=prepare)
    runtime = BackendRuntime(workflow_service=CatalogService(), model_management_service=service)
    with TestClient(create_app(runtime=runtime)) as client:
        secret = "hf_test_token"
        inspected = client.post("/api/v1/models/inspect", json={
            "repository": "org/whisper", "token": secret
        })
        assert inspected.status_code == 200
        assert inspected.json()["revision"] == "abc123"
        assert secret not in inspected.text

        started = client.post("/api/v1/models/jobs", json={
            "repository": "org/whisper", "model_key": "local-whisper", "token": secret,
            "operation": "convert",
        })
        assert started.status_code == 202
        job_id = started.json()["job_id"]
        assert started.json()["runtime"] == "ctranslate2"
        assert started.json()["precision"] == "int8"
        assert secret not in started.text
        assert client.post("/api/v1/models/jobs", json={
            "repository": "org/other", "model_key": "other"
        }).status_code == 409
        assert client.get("/api/v1/models/jobs/not-found").status_code == 404

        gate.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            response = client.get(f"/api/v1/models/jobs/{job_id}")
            if response.json()["status"] == "completed":
                break
            time.sleep(0.005)
        assert response.json()["result"]["verified"] is True
        catalog = client.get("/api/v1/models")
        assert catalog.status_code == 200
        assert catalog.json()["models"][0]["key"] == "turbo"
        assert catalog.json()["models"][0]["repository"] == "org/turbo"
        assert catalog.json()["models"][0]["revision"] == "abc123"
        assert received_tokens == [secret, secret]


def test_model_validation_never_echoes_secret_or_unknown_values():
    service = ModelManagementService(inspector=lambda *args, **kwargs: {})
    runtime = BackendRuntime(workflow_service=CatalogService(), model_management_service=service)
    with TestClient(create_app(runtime=runtime)) as client:
        secret = "hf_sensitive_validation_input"
        response = client.post("/api/v1/models/inspect", json={
            "repository": "org/whisper", "token": {"bad": secret}
        })
        assert response.status_code == 422
        assert secret not in response.text
        extra = client.post("/api/v1/models/inspect", json={
            "repository": "org/whisper", "unexpected": secret
        })
        assert extra.status_code == 422
        assert secret not in extra.text
        invalid_repository = client.post("/api/v1/models/jobs", json={
            "repository": f"https://huggingface.co/{secret}", "model_key": "candidate"
        })
        assert invalid_repository.status_code == 422
        assert secret not in invalid_repository.text
        assert client.get("/api/v1/models/jobs").json()["jobs"] == []


def test_inspection_dependency_failure_is_503():
    service = ModelManagementService(inspector=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    runtime = BackendRuntime(workflow_service=CatalogService(), model_management_service=service)
    with TestClient(create_app(runtime=runtime)) as client:
        response = client.post("/api/v1/models/inspect", json={"repository": "org/whisper"})
        assert response.status_code == 503
        assert response.json()["detail"] == "offline"


def _deletion_service(*, planner=None, deleter=None, preparer=None):
    plan = {
        "model_key": "candidate",
        "files": [{"path": "sources/candidate", "kind": "source", "bytes": 24}],
        "total_bytes": 24,
        "registered": True,
        "deletable": True,
        "reason": None,
    }
    return ModelManagementService(
        deletion_planner=planner or (lambda key: {**plan, "model_key": key}),
        deleter=deleter or (lambda key: {**plan, "model_key": key, "deleted": True}),
        preparer=preparer,
    )


def test_model_deletion_preview_and_success_preserve_terminal_history():
    service = _deletion_service()
    runtime = BackendRuntime(workflow_service=CatalogService(), model_management_service=service)
    old = RunRecord("finished-run", "clip", "completed", (), model="candidate")
    runtime.add_run(old)
    with TestClient(create_app(runtime=runtime)) as client:
        preview = client.get("/api/v1/models/candidate/deletion")
        assert preview.status_code == 200
        assert preview.json() == {
            "model_key": "candidate",
            "files": [{"path": "sources/candidate", "kind": "source", "bytes": 24}],
            "total_bytes": 24,
            "registered": True,
            "deletable": True,
            "reason": None,
        }
        deleted = client.delete("/api/v1/models/candidate")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert runtime.get_run("finished-run") is old


def test_model_deletion_returns_404_and_422_for_missing_and_invalid_keys():
    service = _deletion_service(planner=lambda key: {
        "model_key": key, "files": [], "total_bytes": 0, "registered": False,
        "deletable": False, "reason": "No local assets found",
    })
    runtime = BackendRuntime(workflow_service=CatalogService(), model_management_service=service)
    with TestClient(create_app(runtime=runtime)) as client:
        assert client.get("/api/v1/models/missing/deletion").status_code == 404
        assert client.delete("/api/v1/models/missing").status_code == 404
        assert client.delete("/api/v1/models/%2E%2E-outside").status_code == 422
        assert client.delete("/api/v1/models/foo%2Fbar").status_code == 422


def test_model_deletion_storage_failure_is_sanitized_503():
    service = _deletion_service(
        planner=lambda key: (_ for _ in ()).throw(RuntimeError("secret path /private/models"))
    )
    runtime = BackendRuntime(workflow_service=CatalogService(), model_management_service=service)
    with TestClient(create_app(runtime=runtime)) as client:
        response = client.get("/api/v1/models/candidate/deletion")
        assert response.status_code == 503
        assert response.json()["detail"] == "Model storage is unavailable"
        assert "/private/models" not in response.text


def test_model_deletion_is_blocked_by_preparation_job():
    gate = threading.Event()

    def prepare(*args, **kwargs):
        gate.wait(timeout=2)
        return {}

    service = _deletion_service(preparer=prepare)
    service.start(repository="org/candidate", model_key="candidate")
    runtime = BackendRuntime(workflow_service=CatalogService(), model_management_service=service)
    with TestClient(create_app(runtime=runtime)) as client:
        preview = client.get("/api/v1/models/candidate/deletion")
        assert preview.status_code == 200
        assert preview.json()["deletable"] is False
        assert "preparation job" in preview.json()["reason"]
        assert client.delete("/api/v1/models/candidate").status_code == 409
        gate.set()


def test_model_deletion_is_blocked_by_active_run_stress_and_saved_profile():
    for blocker in ("run", "stress", "profile"):
        service = _deletion_service()
        runtime = BackendRuntime(workflow_service=CatalogService(), model_management_service=service)
        if blocker == "run":
            runtime.add_run(RunRecord("active-run", "microphone", "recovering", (), model="candidate"))
        elif blocker == "stress":
            runtime.add_stress_test(StressTestRecord("active-stress", "running", "candidate", "ctranslate2", "int8"))
        else:
            runtime.profile_store.list_profiles = lambda: [{"model": "candidate"}]
        with TestClient(create_app(runtime=runtime)) as client:
            preview = client.get("/api/v1/models/candidate/deletion")
            assert preview.status_code == 200
            assert preview.json()["deletable"] is False
            assert preview.json()["reason"]
            assert client.delete("/api/v1/models/candidate").status_code == 409


def test_model_deletion_blocks_default_model_runs_and_profiles():
    for blocker in ("run", "profile"):
        service = _deletion_service()
        runtime = BackendRuntime(workflow_service=CatalogService(), model_management_service=service)
        runtime.workflow_service.default_model = "candidate"
        if blocker == "run":
            runtime.add_run(RunRecord("default-run", "microphone", "recording", (), model=None))
        else:
            runtime.profile_store.list_profiles = lambda: [{"model": None}]
        with TestClient(create_app(runtime=runtime)) as client:
            preview = client.get("/api/v1/models/candidate/deletion")
            assert preview.status_code == 200
            assert preview.json()["deletable"] is False
            assert client.delete("/api/v1/models/candidate").status_code == 409


def test_delete_holds_run_admission_lock_until_filesystem_removal_finishes():
    deletion_started = threading.Event()
    allow_delete = threading.Event()

    def delete(key):
        deletion_started.set()
        allow_delete.wait(timeout=2)
        return {"model_key": key, "files": [], "total_bytes": 0,
                "registered": True, "deletable": True, "reason": None,
                "deleted": True}

    service = _deletion_service(deleter=delete)
    runtime = BackendRuntime(workflow_service=CatalogService(), model_management_service=service)
    delete_result = {}
    with TestClient(create_app(runtime=runtime)) as client:
        def request_delete():
            delete_result["response"] = client.delete("/api/v1/models/candidate")

        request_thread = threading.Thread(target=request_delete)
        request_thread.start()
        assert deletion_started.wait(timeout=1)
        admission_started = threading.Event()
        admission_done = threading.Event()

        def admit_run():
            admission_started.set()
            runtime.add_run(RunRecord("after-delete", "clip", "queued", (), model="candidate"))
            admission_done.set()

        admission_thread = threading.Thread(target=admit_run)
        admission_thread.start()
        assert admission_started.wait(timeout=1)
        assert not admission_done.wait(timeout=0.05)
        allow_delete.set()
        request_thread.join(timeout=1)
        admission_thread.join(timeout=1)
        assert delete_result["response"].status_code == 200
        assert admission_done.is_set()
