"""Model catalog, inspection, and preparation job endpoints."""

from fastapi import APIRouter, HTTPException, Request, status
import sqlite3

from speech_to_text.backend.api.schemas.models import (
    ModelInspectionRequest,
    ModelPreparationRequest,
)
from speech_to_text.workflows.transcribe_match_forward import WorkflowUnavailableError
from speech_to_text.workflows.model_management import (
    ModelManagementConflictError,
    ModelManagementInputError,
    ModelManagementNotFoundError,
    ModelManagementUnavailableError,
)

router = APIRouter(prefix="/models")


@router.get("")
def list_models(request: Request):
    try:
        models = request.app.state.backend_runtime.workflow_service.list_models()
    except WorkflowUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return models


@router.post("/inspect")
def inspect_model(request: Request, body: ModelInspectionRequest):
    service = request.app.state.backend_runtime.model_management_service
    try:
        return service.inspect(
            body.repository,
            revision=body.revision,
            token=body.token.get_secret_value() if body.token else None,
        )
    except ModelManagementInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ModelManagementUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
def start_model_job(request: Request, body: ModelPreparationRequest):
    service = request.app.state.backend_runtime.model_management_service
    try:
        return service.start(
            repository=body.repository,
            model_key=body.model_key,
            revision=body.revision,
            token=body.token.get_secret_value() if body.token else None,
            operation=body.operation,
            runtime=body.runtime,
            precision=body.precision,
        )
    except ModelManagementInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ModelManagementConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ModelManagementUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/jobs")
def list_model_jobs(request: Request):
    return {"jobs": request.app.state.backend_runtime.model_management_service.list_jobs()}


@router.get("/jobs/{job_id}")
def get_model_job(job_id: str, request: Request):
    job = request.app.state.backend_runtime.model_management_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="model job not found")
    return job


def _model_use_blockers(runtime, model_key):
    blockers = []
    default_model = getattr(runtime.workflow_service, "default_model", None)
    terminal_runs = {"completed", "stopped", "failed"}
    for record in runtime.runs.values():
        with record.lock:
            model = record.model if record.model is not None else default_model
            if record.status not in terminal_runs and model == model_key:
                blockers.append("An active workflow run uses this model.")
                break

    for record in runtime.stress_runs.values():
        with record.lock:
            if record.status in {"queued", "running"} and record.model == model_key:
                blockers.append("A stress test uses this model.")
                break

    try:
        profiles = runtime.profile_store.list_profiles()
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="profile store unavailable") from exc
    if any(
        (profile.get("model") if profile.get("model") is not None else default_model)
        == model_key
        for profile in profiles
    ):
        blockers.append("A saved profile references this model.")
    return blockers


def _deletion_preview(runtime, model_key):
    plan = runtime.model_management_service.preview_deletion(model_key)
    blockers = _model_use_blockers(runtime, model_key)
    reason_parts = [part for part in (plan.get("reason"), *blockers) if part]
    return {
        **plan,
        "deletable": bool(plan.get("deletable")) and not blockers,
        "reason": "; ".join(reason_parts) or None,
    }


@router.get("/{model_key:path}/deletion")
def preview_model_deletion(model_key: str, request: Request):
    runtime = request.app.state.backend_runtime
    try:
        with runtime.registry_lock:
            return _deletion_preview(runtime, model_key)
    except ModelManagementInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ModelManagementNotFoundError as exc:
        raise HTTPException(status_code=404, detail="no local model assets found") from exc
    except ModelManagementUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/{model_key:path}")
def delete_model(model_key: str, request: Request):
    runtime = request.app.state.backend_runtime
    try:
        with runtime.registry_lock:
            plan = _deletion_preview(runtime, model_key)
            if not plan["deletable"]:
                raise HTTPException(status_code=409, detail=plan["reason"])
            return runtime.model_management_service.delete_local_model(model_key)
    except ModelManagementInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ModelManagementNotFoundError as exc:
        raise HTTPException(status_code=404, detail="no local model assets found") from exc
    except ModelManagementConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ModelManagementUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
