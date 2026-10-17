"""Microphone device discovery routes."""

from fastapi import APIRouter, HTTPException, Request

from speech_to_text.features.model_deployment import AudioInputDeviceError
from speech_to_text.workflows.audio_input_test import (
    AudioInputTestConflictError,
    AudioInputTestNotFoundError,
    AudioInputTestUnavailableError,
)
from speech_to_text.workflows.transcribe_match_forward import WorkflowUnavailableError

router = APIRouter()


@router.get("/microphones")
def list_microphones(request: Request):
    try:
        devices = request.app.state.backend_runtime.workflow_service.list_microphones()
    except WorkflowUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"devices": devices}


@router.post("/microphones/test", status_code=201)
def start_microphone_test(request: Request, payload: dict):
    if set(payload) != {"device"}:
        raise HTTPException(status_code=422, detail="request must contain only device")
    device = payload["device"]
    if device is not None and (not isinstance(device, str) or not device.strip()):
        raise HTTPException(status_code=422, detail="device must be a non-empty string or null")
    try:
        return request.app.state.backend_runtime.audio_input_test_service.start(device)
    except AudioInputTestConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AudioInputDeviceError as exc:
        raise HTTPException(
            status_code=422,
            detail="The selected microphone is unavailable or has an ambiguous name.",
        ) from exc
    except AudioInputTestUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/microphones/test/{test_id}")
def get_microphone_test(test_id: str, request: Request):
    try:
        return request.app.state.backend_runtime.audio_input_test_service.get(test_id)
    except AudioInputTestNotFoundError as exc:
        raise HTTPException(status_code=404, detail="microphone test not found") from exc


@router.post("/microphones/test/{test_id}/stop")
def stop_microphone_test(test_id: str, request: Request):
    try:
        return request.app.state.backend_runtime.audio_input_test_service.stop(test_id)
    except AudioInputTestNotFoundError as exc:
        raise HTTPException(status_code=404, detail="microphone test not found") from exc
