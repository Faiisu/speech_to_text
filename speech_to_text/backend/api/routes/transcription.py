"""Transcription workflow routes and buffered monitoring endpoints."""

import json
import math
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from inspect import Parameter, signature
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile

from speech_to_text.backend.api.schemas.transcription import (
    MicrophoneTranscriptionRequest,
    RTSPTranscriptionRequest,
)
from speech_to_text.backend.api.microphone_lifecycle import (
    start_microphone_workflow,
    validate_keywords,
)
from speech_to_text.backend.dependencies import (
    RunRecord,
    RunRegistryFullError,
    events_after,
    run_snapshot,
)
from speech_to_text.workflows.transcribe_match_forward.destinations import (
    DestinationConfigurationError,
    DestinationUnavailableError,
)
from speech_to_text.workflows.transcribe_match_forward import (
    WorkflowConfigurationError,
    WorkflowUnavailableError,
    normalize_profile_decoding_options,
    prepare_rtsp_configuration,
)

router = APIRouter(prefix="/transcriptions")


def _accepts_keyword_argument(callable_, name):
    try:
        parameters = signature(callable_).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        (
            parameter.name == name
            and parameter.kind
            in (Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY)
        )
        or parameter.kind is Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _clip_worker(
    runtime, record, clip, flow_config, model, selected_runtime, forwarder=None
):
    with record.lock:
        record.status = "running"
    runtime.append_event(record, {"type": "started", "workflow_id": record.workflow_id})
    try:
        def record_measurement(measurement):
            with record.lock:
                record.latest_rtf = measurement.get("rtf")
                record.last_response_at = _as_utc_z(measurement.get("completed_at"))

        transcribe_clip = runtime.workflow_service.transcribe_clip
        transcription_options = {}
        if _accepts_keyword_argument(transcribe_clip, "measurement_callback"):
            transcription_options["measurement_callback"] = record_measurement
        if _accepts_keyword_argument(transcribe_clip, "forwarder"):
            transcription_options["forwarder"] = forwarder
        if _accepts_keyword_argument(transcribe_clip, "model"):
            transcription_options["model"] = model
        if _accepts_keyword_argument(transcribe_clip, "runtime"):
            transcription_options["runtime"] = selected_runtime
        result = transcribe_clip(
            clip,
            record.keywords,
            flow_config,
            **transcription_options,
        )
        matches = [
            {"keyword": item.keyword, "count": item.count} for item in result.matches
        ]
        with record.lock:
            record.transcript = result.transcript
            record.matches = matches
            record.latest_rtf = getattr(result, "latest_rtf", record.latest_rtf)
            record.last_response_at = _utc_now()
            record.status = "completed"
            result_receipt = getattr(result, "forwarding_receipt", None)
            result_error = getattr(result, "forwarding_error", None)
            if record.delivery["status"] == "pending":
                delivered = result_receipt is not None and result_error is None
                record.delivery.update(
                    {
                        "status": "delivered" if delivered else "failed",
                        "attempts": getattr(result_receipt or result_error, "attempts", 0),
                        "http_status": getattr(
                            result_receipt or result_error, "status_code", None
                        ),
                        "last_attempt_at": _utc_now()
                        if result_receipt is not None or result_error is not None
                        else None,
                        "error_code": (
                            getattr(result_error, "code", "delivery_failed")
                            if result_error is not None
                            else None if delivered else "no_receipt"
                        ),
                    }
                )
        runtime.append_event(
            record,
            {
                "type": "transcript",
                "source_id": record.workflow_id,
                "text": result.transcript,
            },
        )
        runtime.append_event(
            record,
            {
                "type": "match_results",
                "source_id": record.workflow_id,
                "language": record.language,
                "matches": matches,
            },
        )
        if record.delivery["status"] != "disabled":
            with record.lock:
                delivery = dict(record.delivery)
            runtime.append_event(
                record,
                {
                    "type": "forwarded"
                    if delivery["status"] == "delivered"
                    else "forwarding_error",
                    "source_id": record.workflow_id,
                    "destination_id": record.destination_id,
                    "receipt": result_receipt,
                    **delivery,
                    **(
                        {"error": "delivery failed"}
                        if delivery["status"] == "failed"
                        else {}
                    ),
                },
            )
        runtime.append_event(
            record,
            {
                "type": "completed",
                "source_id": record.workflow_id,
                "status": "completed",
            },
        )
    except Exception as exc:
        with record.lock:
            record.status = "failed"
            record.error = str(exc)
            record.last_response_at = _utc_now()
            if record.destination_id is not None and record.delivery["status"] == "pending":
                record.delivery.update(
                    {"status": "failed", "error_code": "transcription_failed"}
                )
        runtime.append_event(
            record,
            {
                "type": "workflow_error",
                "source_id": record.workflow_id,
                "error": str(exc),
            },
        )
        runtime.append_event(
            record,
            {"type": "completed", "source_id": record.workflow_id, "status": "failed"},
        )
    finally:
        Path(clip).unlink(missing_ok=True)


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_utc_z(value):
    if isinstance(value, str) and value.endswith("+00:00"):
        return value[:-6] + "Z"
    return value


@router.post("/clips", status_code=202)
async def transcribe_clip(
    request: Request,
    file: UploadFile = File(...),
    keywords: list[str] = Form(...),
    destination_id: str | None = Form(default=None),
    language: Literal["th", "en", "auto"] = Form(default="th"),
    model: str | None = Form(default=None),
    runtime_name: str | None = Form(default=None, alias="runtime"),
    silence_threshold: float | None = Form(default=None),
    decoding_options: str | None = Form(default=None),
):
    clip_path = None
    runtime = request.app.state.backend_runtime
    forwarder = _resolve_clip_forwarder(runtime, destination_id)
    try:
        if not file.filename or not file.filename.lower().endswith(".wav"):
            raise HTTPException(status_code=422, detail="file must be a WAV file")
        validated_keywords = validate_keywords(runtime.workflow_service, keywords)
        service = runtime.workflow_service
        try:
            effective_model = (
                getattr(service, "default_model", "turbo")
                if model is None
                else model
            )
            effective_runtime = (
                getattr(service, "default_runtime", "openvino-gpu")
                if runtime_name is None
                else runtime_name
            )
            resolve_model_config = getattr(service, "resolve_model_config", None)
            if callable(resolve_model_config):
                resolved = resolve_model_config(effective_model, effective_runtime)
                effective_model = resolved["model"]
                effective_runtime = resolved["runtime"]
            validate_model_runtime = getattr(service, "validate_model_runtime", None)
            if callable(validate_model_runtime):
                validate_model_runtime(effective_model, effective_runtime)
            if decoding_options is None:
                submitted_decoding_options = None
            else:
                try:
                    submitted_decoding_options = json.loads(decoding_options)
                except (json.JSONDecodeError, TypeError) as exc:
                    raise WorkflowConfigurationError(
                        "decoding_options must be a JSON object"
                    ) from exc
                if not isinstance(submitted_decoding_options, dict):
                    raise WorkflowConfigurationError(
                        "decoding_options must be a JSON object"
                    )
            validate_decoding_options = getattr(
                service, "validate_decoding_options", None
            )
            effective_decoding_options = (
                validate_decoding_options(submitted_decoding_options)
                if callable(validate_decoding_options)
                else normalize_profile_decoding_options(submitted_decoding_options)
            )
            effective_silence_threshold = (
                0.05 if silence_threshold is None else silence_threshold
            )
            if (
                isinstance(effective_silence_threshold, bool)
                or not math.isfinite(effective_silence_threshold)
                or not 0 <= effective_silence_threshold < 1
            ):
                raise WorkflowConfigurationError(
                    "silence_threshold must be finite and in [0, 1)"
                )
        except WorkflowConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except WorkflowUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as clip_file:
            clip_path = clip_file.name
            has_content = False
            while chunk := await file.read(1024 * 1024):
                has_content = True
                clip_file.write(chunk)
    except BaseException:
        if clip_path is not None:
            Path(clip_path).unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    if not has_content:
        Path(clip_path).unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="file must not be empty")
    workflow_id = uuid.uuid4().hex
    record = RunRecord(
        workflow_id,
        "clip",
        "queued",
        validated_keywords,
        language=language,
        silence_threshold=effective_silence_threshold,
        decoding_options=dict(effective_decoding_options),
        model=effective_model,
        runtime=effective_runtime,
        destination_id=destination_id,
        delivery={
            "status": "pending" if forwarder is not None else "disabled",
            "destination_id": destination_id,
            "event_id": workflow_id,
            "attempts": 0,
            "http_status": None,
            "transport": getattr(forwarder, "transport", "http") if forwarder is not None else None,
            "mqtt_topic": getattr(getattr(forwarder, "config", None), "topic", None),
            "mqtt_qos": getattr(getattr(forwarder, "config", None), "qos", None),
            "last_attempt_at": None,
            "error_code": None,
        },
    )
    try:
        runtime.add_run(record)
    except RunRegistryFullError as exc:
        Path(clip_path).unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    runtime.append_event(record, {"type": "queued", "workflow_id": workflow_id})
    try:
        with record.lock:
            flow_config = {
                "language": language,
                "source_id": workflow_id,
                "silence_threshold": effective_silence_threshold,
                "decoding_options": dict(effective_decoding_options),
            }
            runtime.executor.submit(
                _clip_worker,
                runtime,
                record,
                clip_path,
                flow_config,
                effective_model,
                effective_runtime,
                forwarder,
            )
            record.first_queued_at = _utc_now()
    except Exception:
        runtime.remove_run(workflow_id)
        Path(clip_path).unlink(missing_ok=True)
        raise
    return {
        "workflow_id": workflow_id,
        "status": "queued",
        "first_queued_at": record.first_queued_at,
        "last_response_at": record.last_response_at,
        "model": effective_model,
        "runtime": effective_runtime,
        "silence_threshold": effective_silence_threshold,
        "decoding_options": dict(effective_decoding_options),
    }


@router.post("/microphones", status_code=202)
def start_microphone(request: Request, body: MicrophoneTranscriptionRequest):
    runtime = request.app.state.backend_runtime
    keywords = validate_keywords(runtime.workflow_service, body.keywords)
    device = body.device
    if device is not None and not device.strip():
        raise HTTPException(
            status_code=422,
            detail="device must be an exact non-empty microphone name or omitted",
        )
    forwarder = _resolve_forwarder(runtime, body.destination_id)
    return start_microphone_workflow(
        request,
        keywords=keywords,
        device=device,
        execution_mode=body.execution_mode,
        destination_id=body.destination_id,
        forwarder=forwarder,
        language=body.language,
    )


@router.post("/rtsp", status_code=202)
def start_rtsp(request: Request, body: RTSPTranscriptionRequest):
    runtime = request.app.state.backend_runtime
    keywords = validate_keywords(runtime.workflow_service, body.keywords)
    try:
        source_config = prepare_rtsp_configuration(
            body.rtsp_url, body.rtsp_url_secret_ref, body.rtsp_username, body.rtsp_password
        )
    except WorkflowConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except WorkflowUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    forwarder = _resolve_forwarder(runtime, body.destination_id)
    return start_microphone_workflow(
        request,
        keywords=keywords,
        device=None,
        execution_mode=body.execution_mode,
        destination_id=body.destination_id,
        forwarder=forwarder,
        language=body.language,
        source_type="rtsp",
        rtsp_url=source_config.rtsp_url,
        rtsp_url_secret_ref=body.rtsp_url_secret_ref,
        rtsp_username=source_config.rtsp_username,
        rtsp_has_password=source_config.rtsp_has_password,
        audio_source_factory=source_config.audio_source_factory,
    )


def _resolve_forwarder(runtime, destination_id):
    if destination_id is None:
        return None
    if runtime.destination_registry.get_config(destination_id) is None:
        raise HTTPException(status_code=422, detail="unknown destination_id")
    try:
        return runtime.destination_registry.build_forwarder(destination_id)
    except DestinationConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DestinationUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _resolve_clip_forwarder(runtime, destination_id):
    if destination_id is None:
        return None
    config = runtime.destination_registry.get_config(destination_id)
    if config is None:
        raise HTTPException(status_code=422, detail="unknown destination_id")
    if config.protocol == "mqtt":
        return None
    try:
        return runtime.destination_registry.build_forwarder(config)
    except DestinationConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DestinationUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("")
def list_transcriptions(request: Request):
    return {"transcriptions": request.app.state.backend_runtime.list_runs()}


@router.get("/{workflow_id}")
def transcription_status(workflow_id: str, request: Request):
    record = request.app.state.backend_runtime.get_run(workflow_id)
    if record is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return run_snapshot(record)


@router.get("/{workflow_id}/events")
def transcription_events(
    workflow_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
):
    runtime = request.app.state.backend_runtime
    record = runtime.get_run(workflow_id)
    if record is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    with record.lock:
        if after > record.next_cursor - 1:
            raise HTTPException(
                status_code=422, detail="after cursor is ahead of this workflow"
            )
    result, oldest = events_after(record, after)
    if result is None:
        raise HTTPException(
            status_code=410,
            detail={"message": "event cursor expired", "oldest_cursor": oldest},
        )
    return result


@router.post("/{workflow_id}/stop", status_code=202)
def stop_transcription(workflow_id: str, request: Request):
    runtime = request.app.state.backend_runtime
    record = runtime.get_run(workflow_id)
    if record is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    if record.kind == "clip":
        raise HTTPException(status_code=409, detail="clip workflows cannot be stopped")
    with record.lock:
        if record.status not in {"completed", "stopped", "failed", "stopping"}:
            record.status = "stopping"
            record.recovery_cancel.set()
            runtime.append_event(
                record, {"type": "stopping", "workflow_id": workflow_id}
            )
            threading.Thread(
                target=_stop_microphone_workflow,
                args=(runtime, record),
                name=f"api-stop-{workflow_id}",
                daemon=True,
            ).start()
    return {"workflow_id": workflow_id, "status": record.status}


def _stop_microphone_workflow(runtime, record):
    """Run shutdown in the background and publish failures as terminal state."""
    with record.lock:
        workflow = record.workflow
    if workflow is None:
        record.stop_complete.set()
        return
    stop_status = "stopped"
    try:
        workflow.stop()
    except Exception as exc:
        error = str(exc).strip() or type(exc).__name__
        with record.lock:
            if record.status != "stopping":
                return
            record.status = "failed"
            record.error = error
            stop_status = "failed"
        workflow.output_queue.put(
            {
                "type": "workflow_error",
                "workflow_id": record.workflow_id,
                "source_id": record.workflow_id,
                "error": error,
            }
        )
    finally:
        with record.lock:
            if record.status == "failed":
                stop_status = "failed"
        record.stop_complete.set()
        workflow.output_queue.put(
            {
                "type": "completed",
                "source_id": record.workflow_id,
                "status": stop_status,
            }
        )
