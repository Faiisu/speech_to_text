"""CRUD and run-start routes for saved microphone workflow profiles."""

import sqlite3
import uuid

from fastapi import APIRouter, HTTPException, Request, Response, status

from speech_to_text.backend.api.microphone_lifecycle import (
    start_microphone_workflow,
    validate_keywords,
)
from speech_to_text.backend.api.schemas.transcription import MicrophoneProfileDefinition
from speech_to_text.backend.profile_store import (
    ProfileNameConflictError,
    UnknownDestinationError,
)
from speech_to_text.workflows.transcribe_match_forward import (
    WorkflowConfigurationError,
    WorkflowUnavailableError,
    normalize_profile_decoding_options,
    prepare_rtsp_source,
)
from speech_to_text.workflows.transcribe_match_forward.destinations import (
    DestinationConfigurationError,
    DestinationUnavailableError,
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
    if body.source_type == "microphone":
        if body.rtsp_url is not None or body.rtsp_url_secret_ref is not None:
            raise HTTPException(status_code=422, detail="microphone profiles cannot include RTSP fields")
    else:
        if device is not None:
            raise HTTPException(status_code=422, detail="RTSP profiles cannot include device")
        try:
            prepare_rtsp_source(body.rtsp_url, body.rtsp_url_secret_ref)
        except WorkflowConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except WorkflowUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
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
    destination_id = body.destination_id
    if destination_id is not None:
        if runtime.destination_registry.get_config(destination_id) is None:
            raise HTTPException(status_code=422, detail="unknown destination_id")
    try:
        validate_decoding_options = getattr(
            runtime.workflow_service, "validate_decoding_options", None
        )
        decoding_options = (
            validate_decoding_options(body.decoding_options.dict())
            if callable(validate_decoding_options)
            else normalize_profile_decoding_options(body.decoding_options.dict())
        )
    except WorkflowConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "name": name,
        "source_type": body.source_type,
        "device": device,
        "rtsp_url": body.rtsp_url,
        "rtsp_url_secret_ref": body.rtsp_url_secret_ref,
        "execution_mode": body.execution_mode,
        "keywords": list(keywords),
        "silence_threshold": body.silence_threshold,
        "model": model,
        "runtime": selected_runtime,
        "destination_id": destination_id,
        "language": body.language,
        "decoding_options": decoding_options,
    }


def _store_call(callback):
    try:
        return callback()
    except ProfileNameConflictError as exc:
        raise HTTPException(
            status_code=409, detail="A profile with this name already exists."
        ) from exc
    except UnknownDestinationError as exc:
        raise HTTPException(status_code=422, detail="unknown destination_id") from exc
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
    source_factory = None
    if profile.get("source_type", "microphone") == "rtsp":
        try:
            source_factory = prepare_rtsp_source(
                profile.get("rtsp_url"), profile.get("rtsp_url_secret_ref")
            )
        except WorkflowConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except WorkflowUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    forwarder = None
    destination_id = profile.get("destination_id")
    if destination_id is not None:
        try:
            forwarder = runtime.destination_registry.build_forwarder(destination_id)
        except DestinationConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except DestinationUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
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
        destination_id=destination_id,
        forwarder=forwarder,
        decoding_options=profile.get("decoding_options"),
        language=profile.get("language", "th"),
        source_type=profile.get("source_type", "microphone"),
        rtsp_url=profile.get("rtsp_url"),
        rtsp_url_secret_ref=profile.get("rtsp_url_secret_ref"),
        audio_source_factory=source_factory,
    )
