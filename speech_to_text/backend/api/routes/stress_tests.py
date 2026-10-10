"""Asynchronous file-replay stress-test routes."""

from pathlib import Path
import uuid

from fastapi import APIRouter, HTTPException, Query, Request

from speech_to_text.backend.api.schemas.stress_tests import StressTestRequest
from speech_to_text.backend.dependencies import (
    RunRegistryFullError,
    StressTestConflictError,
    StressTestRecord,
    stress_events_after,
    stress_test_snapshot,
)
from speech_to_text.workflows.transcribe_match_forward import (
    WorkflowConfigurationError,
    WorkflowUnavailableError,
)

router = APIRouter(prefix="/stress-tests")

_DEFAULT_MODEL = "turbo"
_DEFAULT_RUNTIME = "openvino-gpu"
_OPENVINO_PRECISIONS = {"source", "bf16", "int8", "int4"}
_CTRANSLATE2_PRECISIONS = {"int8", "float32"}


def _resolve_model_config(runtime, body):
    workflow_service = runtime.workflow_service
    model_override = body.model if body and body.model is not None else None
    runtime_override = body.runtime if body and body.runtime is not None else None
    precision_override = body.precision if body and body.precision is not None else None
    resolve_config = getattr(workflow_service, "resolve_model_config", None)
    if callable(resolve_config):
        try:
            defaults = resolve_config()
            resolved = resolve_config(
                model=(
                    model_override
                    if model_override is not None
                    else defaults["model"]
                ),
                runtime=(
                    runtime_override
                    if runtime_override is not None
                    else defaults["runtime"]
                ),
            )
        except WorkflowConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except WorkflowUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        model = resolved["model"]
        selected_runtime = resolved["runtime"]
        precision = (
            precision_override
            if precision_override is not None
            else resolved["precision"]
        )
    else:
        model = (
            model_override
            if model_override is not None
            else getattr(workflow_service, "default_model", _DEFAULT_MODEL)
        )
        selected_runtime = (
            runtime_override
            if runtime_override is not None
            else getattr(workflow_service, "default_runtime", _DEFAULT_RUNTIME)
        )
        precision = precision_override
        if precision is None and selected_runtime == getattr(
            workflow_service, "default_runtime", _DEFAULT_RUNTIME
        ):
            precision = getattr(workflow_service, "default_precision", None)
    if not model.strip():
        raise HTTPException(status_code=422, detail="model must not be empty")
    if not selected_runtime.strip():
        raise HTTPException(status_code=422, detail="runtime must not be empty")
    validate_model_runtime = getattr(workflow_service, "validate_model_runtime", None)
    if callable(validate_model_runtime):
        try:
            validate_model_runtime(model, selected_runtime)
        except WorkflowConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except WorkflowUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    allowed_precisions = (
        _CTRANSLATE2_PRECISIONS
        if selected_runtime == "ctranslate2"
        else _OPENVINO_PRECISIONS
    )
    if precision is None:
        precision = "int8" if selected_runtime == "ctranslate2" else "source"
    if precision not in allowed_precisions:
        choices = ", ".join(sorted(allowed_precisions))
        raise HTTPException(
            status_code=422,
            detail=f"precision {precision!r} is not supported by {selected_runtime}; choose from {choices}",
        )
    return {"model": model, "runtime": selected_runtime, "precision": precision}


def _stress_worker(runtime, record, model_config, clip_path):
    with record.lock:
        record.status = "running"
    runtime.append_stress_event(
        record,
        {"type": "started", "stress_test_id": record.stress_test_id},
    )

    def publish_progress(progress):
        event = dict(progress)
        event_type = event.pop("event", event.get("type"))
        if event_type:
            event["type"] = event_type
        event["stress_test_id"] = record.stress_test_id
        runtime.append_stress_event(record, event)

    try:
        report = runtime.stress_workflow(
            clip_path=clip_path,
            model_config=model_config,
            progress_callback=publish_progress,
        )
    except Exception as exc:
        error = str(exc).strip() or type(exc).__name__
        with record.lock:
            record.status = "failed"
            record.error = error
        runtime.append_stress_event(
            record,
            {
                "type": "workflow_error",
                "stress_test_id": record.stress_test_id,
                "error": error,
            },
        )
        return

    with record.lock:
        record.report = report
        record.status = "completed"
    runtime.append_stress_event(
        record,
        {
            "type": "completed",
            "stress_test_id": record.stress_test_id,
            "status": "completed",
        },
    )


@router.post("", status_code=202)
def start_stress_test(request: Request, body: StressTestRequest | None = None):
    runtime = request.app.state.backend_runtime
    model_config = _resolve_model_config(runtime, body)
    stress_test_id = uuid.uuid4().hex
    record = StressTestRecord(
        stress_test_id=stress_test_id,
        status="queued",
        model=model_config["model"],
        runtime=model_config["runtime"],
        precision=model_config["precision"],
    )
    try:
        runtime.add_stress_test(record)
    except StressTestConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RunRegistryFullError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    runtime.append_stress_event(
        record,
        {"type": "queued", "stress_test_id": stress_test_id},
    )
    clip_path = str(Path(__file__).resolve().parents[4] / "audio" / "test-audio.wav")
    try:
        runtime.executor.submit(
            _stress_worker, runtime, record, model_config, clip_path
        )
    except Exception as exc:
        runtime.remove_stress_test(stress_test_id)
        raise HTTPException(
            status_code=503, detail="unable to queue stress test"
        ) from exc
    return {"stress_test_id": stress_test_id, "status": "queued"}


@router.get("/{stress_test_id}")
def stress_test_status(stress_test_id: str, request: Request):
    record = request.app.state.backend_runtime.get_stress_test(stress_test_id)
    if record is None:
        raise HTTPException(status_code=404, detail="stress test not found")
    return stress_test_snapshot(record)


@router.get("/{stress_test_id}/events")
def stress_test_events(
    stress_test_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
):
    runtime = request.app.state.backend_runtime
    record = runtime.get_stress_test(stress_test_id)
    if record is None:
        raise HTTPException(status_code=404, detail="stress test not found")
    with record.lock:
        if after > record.next_cursor - 1:
            raise HTTPException(
                status_code=422, detail="after cursor is ahead of this stress test"
            )
    result, oldest = stress_events_after(record, after)
    if result is None:
        raise HTTPException(
            status_code=410,
            detail={"message": "event cursor expired", "oldest_cursor": oldest},
        )
    return result
