"""Shared HTTP lifecycle for starting and monitoring microphone workflows."""

import queue
import threading
import uuid

from fastapi import HTTPException

from speech_to_text.backend.dependencies import (
    MicrophoneLimitError,
    RunRecord,
    RunRegistryFullError,
)
from speech_to_text.workflows.transcribe_match_forward import (
    WorkflowConfigurationError,
    WorkflowInputError,
    WorkflowUnavailableError,
)


def validate_keywords(workflow_service, keywords):
    try:
        return workflow_service.validate_keywords(keywords)
    except WorkflowConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def start_microphone_workflow(
    request,
    *,
    keywords,
    device,
    execution_mode,
    silence_threshold=None,
    model=None,
    selected_runtime=None,
    profile_id=None,
    profile_name=None,
):
    """Start one microphone workflow and register its run for API monitoring."""
    runtime = request.app.state.backend_runtime
    effective_model = model
    if effective_model is None:
        effective_model = getattr(runtime.workflow_service, "default_model", None)
    effective_runtime = selected_runtime
    if effective_runtime is None:
        effective_runtime = getattr(runtime.workflow_service, "default_runtime", None)
    workflow_id = uuid.uuid4().hex
    record = RunRecord(
        workflow_id,
        "microphone",
        "starting",
        tuple(keywords),
        profile_id=profile_id,
        profile_name=profile_name,
        device=device,
        execution_mode=execution_mode,
        silence_threshold=silence_threshold,
        model=effective_model,
        runtime=effective_runtime,
    )
    try:
        runtime.add_run(record)
    except (RunRegistryFullError, MicrophoneLimitError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        flow_config = {"language": "th", "source_id": workflow_id}
        if silence_threshold is not None:
            flow_config["silence_threshold"] = silence_threshold
        start_options = {"execution_mode": execution_mode, "model": effective_model}
        if selected_runtime is not None:
            start_options["runtime"] = selected_runtime
        workflow = runtime.workflow_service.start_microphone(
            device, keywords, flow_config, **start_options
        )
    except WorkflowConfigurationError as exc:
        runtime.remove_run(workflow_id)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except WorkflowInputError as exc:
        runtime.remove_run(workflow_id)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except WorkflowUnavailableError as exc:
        runtime.remove_run(workflow_id)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        runtime.remove_run(workflow_id)
        raise HTTPException(
            status_code=500, detail="unable to start microphone workflow"
        ) from exc
    with record.lock:
        record.workflow = workflow
        record.status = "recording"
    runtime.append_event(
        record, {"type": "started", "workflow_id": workflow_id, "status": "recording"}
    )
    threading.Thread(
        target=_pump_microphone,
        args=(runtime, record, workflow),
        name=f"api-events-{workflow_id}",
        daemon=True,
    ).start()
    return {"workflow_id": workflow_id, "status": "recording"}


def _pump_microphone(runtime, record, workflow):
    transcript_events = []
    saw_error = False
    saw_completion = False
    while True:
        try:
            event = workflow.output_queue.get(timeout=0.25)
        except queue.Empty:
            try:
                workflow.wait(timeout=0)
            except TimeoutError:
                continue
            break
        if isinstance(event, dict):
            if event.get("type") == "transcript":
                transcript_events.append(event)
            if event.get("type") == "match_results":
                with record.lock:
                    record.matches = event.get("matches", [])
            if event.get("type") == "completed":
                saw_completion = True
                with record.lock:
                    record.status = event.get("status", "completed")
            if event.get("type") in {"workflow_error", "matching_error"}:
                saw_error = True
                with record.lock:
                    record.error = event.get("error")
            runtime.append_event(record, event)
            if event.get("type") == "completed":
                break
    transcript_events.sort(key=lambda event: event.get("sequence", 0))
    with record.lock:
        record.transcript = " ".join(
            event.get("text", "")
            for event in transcript_events
            if isinstance(event.get("text", ""), str)
        )
        if record.status in {"recording", "stopping"}:
            record.status = "failed"
            record.error = (
                record.error or "Microphone workflow ended without a completion event"
            )
        status = record.status
        error = record.error
    if status == "failed" and error and not saw_error:
        runtime.append_event(record, {"type": "workflow_error", "error": error})
    if status == "failed" and not saw_completion:
        runtime.append_event(
            record,
            {"type": "completed", "source_id": record.workflow_id, "status": "failed"},
        )
