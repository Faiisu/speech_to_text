"""Host microphone recording lifecycle and WAV download endpoints."""

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse

from speech_to_text.workflows.audio_recording import (
    RecordingConflictError,
    RecordingConfigurationError,
    RecordingInvalidAudioError,
    RecordingTooLargeError,
    RecordingUnavailableError,
)

router = APIRouter(prefix="/recordings")


@router.post("/upload", status_code=201)
def upload_recording(request: Request, file: UploadFile = File(...)):
    try:
        return request.app.state.backend_runtime.recording_service.import_audio(
            file.file, file.filename
        )
    except RecordingTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except RecordingInvalidAudioError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RecordingUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Unable to store uploaded audio") from exc
    finally:
        file.file.close()


@router.post("", status_code=201)
def start_recording(request: Request, payload: dict):
    if set(payload) - {"device", "source_type", "rtsp_url", "rtsp_url_secret_ref", "rtsp_username", "rtsp_password"}:
        raise HTTPException(status_code=422, detail="request contains unsupported fields")
    if payload.get("source_type", "microphone") == "microphone" and (
        any(name in payload for name in ("rtsp_url", "rtsp_url_secret_ref", "rtsp_username", "rtsp_password"))
    ):
        raise HTTPException(status_code=422, detail="Microphone recordings cannot include RTSP fields")
    device = payload.get("device")
    if device is not None and (not isinstance(device, str) or not device.strip()):
        raise HTTPException(status_code=422, detail="device must be a non-empty string or null")
    try:
        return request.app.state.backend_runtime.recording_service.start(
            device,
            source_type=payload.get("source_type", "microphone"),
            rtsp_url=payload.get("rtsp_url"),
            rtsp_url_secret_ref=payload.get("rtsp_url_secret_ref"),
            rtsp_username=payload.get("rtsp_username"),
            rtsp_password=payload.get("rtsp_password"),
        )
    except RecordingConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RecordingConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RecordingUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("")
def list_recordings(request: Request):
    try:
        return {"recordings": request.app.state.backend_runtime.recording_service.list()}
    except RecordingUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Unable to read recording storage") from exc


@router.get("/{recording_id}")
def get_recording(recording_id: str, request: Request):
    try:
        snapshot = request.app.state.backend_runtime.recording_service.get(recording_id)
    except RecordingUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Unable to read recording storage") from exc
    if snapshot is None:
        raise HTTPException(status_code=404, detail="recording not found")
    return snapshot


@router.post("/{recording_id}/stop")
def stop_recording(recording_id: str, request: Request):
    try:
        snapshot = request.app.state.backend_runtime.recording_service.stop(recording_id)
    except RecordingUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Unable to stop microphone recording") from exc
    if snapshot is None:
        raise HTTPException(status_code=404, detail="recording not found")
    return snapshot


@router.get("/{recording_id}/audio")
def download_recording(recording_id: str, request: Request):
    service = request.app.state.backend_runtime.recording_service
    try:
        path = service.audio_path(recording_id)
    except RecordingConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RecordingUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Unable to read recording storage") from exc
    if path is None:
        raise HTTPException(status_code=404, detail="recording audio not found")
    return FileResponse(
        Path(path),
        media_type="audio/wav",
        filename=f"recording-{recording_id}.wav",
    )


@router.delete("/{recording_id}", status_code=204)
def delete_recording(recording_id: str, request: Request):
    try:
        deleted = request.app.state.backend_runtime.recording_service.delete(recording_id)
    except RecordingConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RecordingUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Unable to delete recording") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="recording not found")
    return Response(status_code=204)
