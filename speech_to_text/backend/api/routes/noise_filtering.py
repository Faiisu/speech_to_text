"""Noise filtering endpoints."""

from fastapi import APIRouter, HTTPException, Request

from speech_to_text.backend.api.schemas.noise_filtering import FilterRecordingRequest
from speech_to_text.workflows.audio_recording import (
    RecordingConflictError,
    RecordingInvalidAudioError,
    RecordingUnavailableError,
)
from speech_to_text.workflows.noise_filtering import filter_recording

router = APIRouter(prefix="/noise-filtering")


@router.post("/recordings", status_code=201)
def create_filtered_recording(payload: FilterRecordingRequest, request: Request):
    try:
        result = filter_recording(
            request.app.state.backend_runtime.recording_service,
            payload.recording_id,
            low_hz=payload.low_hz,
            high_hz=payload.high_hz,
            order=payload.order,
        )
    except RecordingConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RecordingInvalidAudioError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RecordingUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Unable to create filtered recording") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="recording not found")
    return result
