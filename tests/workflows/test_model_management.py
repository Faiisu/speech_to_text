import threading
import time

import pytest

from speech_to_text.workflows.model_management import (
    ModelManagementConflictError,
    ModelManagementInputError,
    ModelManagementNotFoundError,
    ModelManagementService,
)


def wait_for(service, job_id, status):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        snapshot = service.get_job(job_id)
        if snapshot["status"] == status:
            return snapshot
        time.sleep(0.005)
    raise AssertionError(f"job did not reach {status}")


def test_jobs_are_bounded_and_do_not_retain_or_return_tokens():
    gate = threading.Event()
    seen = []

    def prepare(repository, model_key, **options):
        seen.append(options["token"])
        options["progress"]("downloading source")
        gate.wait(timeout=2)
        return {
            "model_key": model_key,
            "repository": repository,
            "revision": "resolved-sha",
            "downloaded": True,
            "converted": False,
            "verified": False,
            "runtime": options["runtime"],
            "precision": options["precision"],
            "reason": None,
        }

    service = ModelManagementService(max_history=1, preparer=prepare)
    token = "hf_secret_value"
    first = service.start(repository="org/model", model_key="local-model", operation="download", token=token)
    assert "token" not in first
    assert token not in repr(service.list_jobs())
    with pytest.raises(ModelManagementConflictError):
        service.start(repository="org/other", model_key="other")

    gate.set()
    done = wait_for(service, first["job_id"], "completed")
    assert done["result"]["verified"] is False
    assert seen == [token]
    service.start(repository="org/model", model_key="local-model", operation="download")
    done_second = wait_for(service, service.list_jobs()[0]["job_id"], "completed")
    assert done_second["status"] == "completed"
    assert len(service.list_jobs()) == 1
    service.close()


def test_invalid_model_key_is_rejected_before_scheduling():
    service = ModelManagementService(preparer=lambda *args, **kwargs: {})
    with pytest.raises(ModelManagementInputError):
        service.start(repository="org/model", model_key="../outside")
    assert service.list_jobs() == []
    service.close()


def test_remote_failure_redacts_request_token():
    token = "hf_do_not_echo"
    service = ModelManagementService(
        inspector=lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError(f"failed for {token} at https://huggingface.co/x?token={token}")
        )
    )
    with pytest.raises(RuntimeError) as error:
        service.inspect("org/model", token=token)
    assert token not in str(error.value)
    assert "https://" not in str(error.value)
    service.close()


def test_deletion_preview_and_delete_are_serialized_with_job_start():
    deletion_started = threading.Event()
    finish_deletion = threading.Event()
    deletion_calls = []
    plan = {
        "model_key": "local-model",
        "files": [{"path": "sources/local-model", "kind": "source", "bytes": 12}],
        "total_bytes": 12,
        "registered": True,
        "deletable": True,
        "reason": None,
    }

    def delete(model_key):
        deletion_calls.append(model_key)
        deletion_started.set()
        finish_deletion.wait(timeout=2)
        return {**plan, "deleted": True}

    service = ModelManagementService(
        deletion_planner=lambda key: dict(plan),
        deleter=delete,
        preparer=lambda *args, **kwargs: {},
    )
    assert service.preview_deletion("local-model")["deletable"] is True
    delete_thread = threading.Thread(target=service.delete_local_model, args=("local-model",))
    delete_thread.start()
    assert deletion_started.wait(timeout=1)
    start_started = threading.Event()
    start_done = threading.Event()
    start_result = {}

    def start_job():
        start_started.set()
        start_result["job"] = service.start(repository="org/model", model_key="other-model")
        start_done.set()

    start_thread = threading.Thread(target=start_job)
    start_thread.start()
    assert start_started.wait(timeout=1)
    assert not start_done.wait(timeout=0.05)
    finish_deletion.set()
    delete_thread.join(timeout=1)
    start_thread.join(timeout=1)
    assert not delete_thread.is_alive()
    assert not start_thread.is_alive()
    assert start_result["job"]["model_key"] == "other-model"
    assert deletion_calls == ["local-model"]
    service.close()


def test_deletion_preview_rejects_invalid_and_unmanaged_keys():
    service = ModelManagementService(
        deletion_planner=lambda key: {
            "model_key": key,
            "files": [],
            "total_bytes": 0,
            "registered": False,
            "deletable": False,
            "reason": "No local assets found",
        }
    )
    with pytest.raises(ModelManagementInputError):
        service.preview_deletion("../outside")
    with pytest.raises(ModelManagementNotFoundError):
        service.preview_deletion("missing-model")
    service.close()
