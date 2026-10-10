"""Read-only model catalog endpoint."""

from fastapi import APIRouter, HTTPException, Request

from speech_to_text.workflows.transcribe_match_forward import WorkflowUnavailableError

router = APIRouter()


@router.get("/models")
def list_models(request: Request):
    try:
        models = request.app.state.backend_runtime.workflow_service.list_models()
    except WorkflowUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return models
