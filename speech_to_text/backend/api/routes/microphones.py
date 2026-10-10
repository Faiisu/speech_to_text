"""Microphone device discovery routes."""

from fastapi import APIRouter, HTTPException, Request

from speech_to_text.workflows.transcribe_match_forward import WorkflowUnavailableError

router = APIRouter()


@router.get("/microphones")
def list_microphones(request: Request):
    try:
        devices = request.app.state.backend_runtime.workflow_service.list_microphones()
    except WorkflowUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"devices": devices}
