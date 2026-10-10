"""CRUD and run-start routes for saved microphone workflow profiles."""

import sqlite3
import uuid

from fastapi import APIRouter, HTTPException, Request, Response, status

from speech_to_text.backend.api.microphone_lifecycle import (
    start_microphone_workflow,
    validate_keywords,
)
from speech_to_text.backend.api.schemas.transcription import MicrophoneProfileDefinition
from speech_to_text.backend.profile_store import ProfileNameConflictError
from speech_to_text.workflows.transcribe_match_forward import (
    WorkflowConfigurationError,
    WorkflowUnavailableError,
)

router = APIRouter(prefix="/profiles")


def _profile_data(request: Request, body: MicrophoneProfileDefinition):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    device = body.device
    if device is not None and not device.strip():
        raise HTTPException(
            status_code=422,
            detail="device must be an exact non-empty microphone name or null",
        )
    runtime = request.app.state.backend_runtime
    keywords = validate_keywords(runtime.workflow_service, body.keywords)
    model = body.model or runtime.workflow_service.default_model
    selected_runtime = body.runtime or runtime.workflow_service.default_runtime
    try:
        runtime.workflow_service.validate_model_runtime(model, selected_runtime)
    except WorkflowConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except WorkflowUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "name": name,
        "device": device,
        "execution_mode": body.execution_mode,
        "keywords": list(keywords),
        "silence_threshold": body.silence_threshold,
        "model": model,
        "runtime": selected_runtime,
    }


def _store_call(callback):
    try:
        return callback()
    except ProfileNameConflictError as exc:
        raise HTTPException(
            status_code=409, detail="A profile with this name already exists."
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="profile database unavailable") from exc


@router.get("")
def list_profiles(request: Request):
    profiles = _store_call(request.app.state.backend_runtime.profile_store.list_profiles)
    return {"profiles": profiles}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_profile(
    request: Request, body: MicrophoneProfileDefinition
):
    profile = _profile_data(request, body)
    profile["profile_id"] = uuid.uuid4().hex
    result = _store_call(
        lambda: request.app.state.backend_runtime.profile_store.create_profile(profile)
    )
    return result


@router.get("/{profile_id}")
def get_profile(profile_id: str, request: Request):
    profile = _store_call(
        lambda: request.app.state.backend_runtime.profile_store.get_profile(profile_id)
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return profile


@router.put("/{profile_id}")
def replace_profile(
    profile_id: str, request: Request, body: MicrophoneProfileDefinition
):
    definition = _profile_data(request, body)
    profile = _store_call(
        lambda: request.app.state.backend_runtime.profile_store.update_profile(
            profile_id, definition
        )
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return profile


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(profile_id: str, request: Request):
    deleted = _store_call(
        lambda: request.app.state.backend_runtime.profile_store.delete_profile(profile_id)
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="profile not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{profile_id}/runs", status_code=status.HTTP_202_ACCEPTED)
def start_profile_run(profile_id: str, request: Request):
    runtime = request.app.state.backend_runtime
    profile = _store_call(lambda: runtime.profile_store.get_profile(profile_id))
    if profile is None:
        raise HTTPException(status_code=404, detail="profile not found")
    keywords = validate_keywords(runtime.workflow_service, profile["keywords"])
    return start_microphone_workflow(
        request,
        keywords=keywords,
        device=profile["device"],
        execution_mode=profile["execution_mode"],
        silence_threshold=profile["silence_threshold"],
        model=profile["model"],
        selected_runtime=profile["runtime"],
        profile_id=profile["profile_id"],
        profile_name=profile["name"],
    )
