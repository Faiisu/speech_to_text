"""Shared HTTP lifecycle for starting and monitoring microphone workflows."""

import queue
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from inspect import Parameter, signature

from fastapi import HTTPException

from speech_to_text.backend.dependencies import (
    MicrophoneLimitError,
    RunRecord,
    RunRegistryFullError,
    StressTestConflictError,
)
from speech_to_text.workflows.transcribe_match_forward import (
    WorkflowConfigurationError,
    WorkflowInputError,
    WorkflowUnavailableError,
    normalize_profile_decoding_options,
)


def _accepts_keyword_argument(callable_, name):
    try:
        parameters = signature(callable_).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        (parameter.name == name and parameter.kind in (
            Parameter.POSITIONAL_OR_KEYWORD,
            Parameter.KEYWORD_ONLY,
        ))
        or parameter.kind is Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def validate_keywords(workflow_service, keywords):
    try:
        return workflow_service.validate_keywords(keywords)
    except WorkflowConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    destination_id=None,
    forwarder=None,
    decoding_options=None,
    language="th",
    source_type="microphone",
    rtsp_url=None,
    rtsp_url_secret_ref=None,
    rtsp_username=None,
    rtsp_has_password=False,
    audio_source_factory=None,
):
    """Start one microphone workflow and register its run for API monitoring."""
    runtime = request.app.state.backend_runtime
    if profile_id is not None:
        with runtime.profile_run_start_lock:
            existing = runtime.get_active_profile_run(profile_id)
            if existing is not None:
                with existing.lock:
                    if existing.status in {"starting", "recording", "stopping"}:
                        return {
                            "workflow_id": existing.workflow_id,
                            "status": existing.status,
                            "first_queued_at": existing.first_queued_at,
                            "last_response_at": existing.last_response_at,
                        }
            return _start_microphone_workflow(
                request,
                keywords=keywords,
                device=device,
                execution_mode=execution_mode,
                silence_threshold=silence_threshold,
                model=model,
                selected_runtime=selected_runtime,
                profile_id=profile_id,
                profile_name=profile_name,
                destination_id=destination_id,
                forwarder=forwarder,
                decoding_options=decoding_options,
                language=language,
                source_type=source_type,
                rtsp_url=rtsp_url,
                rtsp_url_secret_ref=rtsp_url_secret_ref,
                rtsp_username=rtsp_username,
                rtsp_has_password=rtsp_has_password,
                audio_source_factory=audio_source_factory,
            )
    return _start_microphone_workflow(
        request,
        keywords=keywords,
        device=device,
        execution_mode=execution_mode,
        silence_threshold=silence_threshold,
        model=model,
        selected_runtime=selected_runtime,
        profile_id=profile_id,
        profile_name=profile_name,
        destination_id=destination_id,
        forwarder=forwarder,
        decoding_options=decoding_options,
        language=language,
        source_type=source_type,
        rtsp_url=rtsp_url,
        rtsp_url_secret_ref=rtsp_url_secret_ref,
        rtsp_username=rtsp_username,
        rtsp_has_password=rtsp_has_password,
        audio_source_factory=audio_source_factory,
    )


def _start_microphone_workflow(
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
    destination_id=None,
    forwarder=None,
    decoding_options=None,
    language="th",
    source_type="microphone",
    rtsp_url=None,
    rtsp_url_secret_ref=None,
    rtsp_username=None,
    rtsp_has_password=False,
    audio_source_factory=None,
):
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
        language=language,
        profile_id=profile_id,
        profile_name=profile_name,
        device=device,
        source_type=source_type,
        rtsp_url=rtsp_url,
        rtsp_url_secret_ref=rtsp_url_secret_ref,
        rtsp_username=rtsp_username,
        rtsp_has_password=rtsp_has_password,
        audio_source_factory=audio_source_factory,
        execution_mode=execution_mode,
        silence_threshold=silence_threshold,
        model=effective_model,
        runtime=effective_runtime,
        decoding_options=deepcopy(
            normalize_profile_decoding_options(decoding_options)
        ),
        destination_id=destination_id,
        forwarder=forwarder,
        delivery={
            "status": "pending" if destination_id is not None else "disabled",
            "destination_id": destination_id,
            "event_id": workflow_id,
            "attempts": 0,
            "http_status": None,
            "transport": getattr(forwarder, "transport", "http") if destination_id else None,
            "mqtt_topic": getattr(getattr(forwarder, "config", None), "topic", None),
            "mqtt_qos": getattr(getattr(forwarder, "config", None), "qos", None),
            "last_attempt_at": None,
            "error_code": None,
        },
    )
    try:
        runtime.add_run(record)
    except (RunRegistryFullError, MicrophoneLimitError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except StressTestConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        flow_config = {
            "language": language,
            "source_id": workflow_id,
            "overload_policy": "drop",
        }
        if silence_threshold is not None:
            flow_config["silence_threshold"] = silence_threshold
        if decoding_options is not None:
            flow_config["decoding_options"] = deepcopy(record.decoding_options)
        start_options = {"execution_mode": execution_mode, "model": effective_model}
        if audio_source_factory is not None:
            if not _accepts_keyword_argument(
                runtime.workflow_service.start_microphone, "audio_source_factory"
            ):
                raise WorkflowUnavailableError(
                    "The configured workflow service cannot start an RTSP audio source"
                )
            start_options["audio_source_factory"] = audio_source_factory
        if effective_runtime is not None and _accepts_keyword_argument(
            runtime.workflow_service.start_microphone, "runtime"
        ):
            start_options["runtime"] = effective_runtime
        if _accepts_keyword_argument(
            runtime.workflow_service.start_microphone, "forwarder"
        ):
            start_options["forwarder"] = forwarder
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
    controller = threading.Thread(
        target=_pump_microphone,
        args=(runtime, record, workflow),
        name=f"api-events-{workflow_id}",
        daemon=True,
    )
    with record.lock:
        record.controller_thread = controller
    controller.start()
    with record.lock:
        first_queued_at = record.first_queued_at
        last_response_at = record.last_response_at
    return {
        "workflow_id": workflow_id,
        "status": "recording",
        "first_queued_at": first_queued_at,
        "last_response_at": last_response_at,
    }


def _pump_microphone(runtime, record, workflow):
    transcript_parts = []
    transcript_events = []
    restart_count = 0
    retry_delay = runtime.recovery_initial_delay
    recovery_reason = None
    while True:
        ended_reason = None
        received_completion = False
        try:
            while True:
                try:
                    event = workflow.output_queue.get(timeout=0.25)
                except queue.Empty:
                    try:
                        workflow.wait(timeout=0)
                    except TimeoutError:
                        continue
                    if record.recovery_cancel.is_set() and not record.stop_complete.is_set():
                        record.stop_complete.wait(timeout=0.1)
                        continue
                    ended_reason = "Microphone workflow ended without a completion event"
                    break
                if not isinstance(event, dict):
                    continue
                event_type = event.get("type")
                if event_type == "transcript":
                    transcript_events.append(event)
                    _update_microphone_transcript(record, transcript_parts, transcript_events)
                if event_type == "match_results":
                    with record.lock:
                        record.matches = event.get("matches", [])
                if event_type == "measurement":
                    with record.lock:
                        record.latest_rtf = event.get("rtf")
                        completed_at = event.get("completed_at")
                        record.last_response_at = (
                            completed_at[:-6] + "Z"
                            if isinstance(completed_at, str)
                            and completed_at.endswith("+00:00")
                            else completed_at
                        )
                if event_type == "audio_queued":
                    with record.lock:
                        if record.first_queued_at is None:
                            record.first_queued_at = event.get("first_queued_at")
                if event_type == "forwarded":
                    receipt = event.get("receipt")
                    with record.lock:
                        record.delivery.update(
                            {
                                "status": "delivered",
                                "attempts": getattr(receipt, "attempts", 1),
                                "http_status": getattr(receipt, "status_code", None),
                                "last_attempt_at": _utc_now(),
                                "error_code": None,
                            }
                        )
                if event_type == "forwarding_error":
                    with record.lock:
                        record.delivery.update(
                            {
                                "status": "failed",
                                "attempts": event.get("attempts") or 1,
                                "http_status": event.get("status_code"),
                                "last_attempt_at": _utc_now(),
                                "error_code": event.get("error_code", "delivery_failed"),
                            }
                        )
                if event_type in {"workflow_error", "matching_error"} or (
                    event_type == "error" and event.get("fatal") is True
                ):
                    with record.lock:
                        record.error = event.get("error") or event.get("message")
                if event_type == "completed":
                    received_completion = True
                    completion_status = event.get("status", "completed")
                    if record.recovery_cancel.is_set() and completion_status == "failed":
                        with record.lock:
                            record.status = "failed"
                            record.error = record.error or event.get("error")
                    ended_reason = (
                        record.error
                        or event.get("error")
                        or f"Microphone workflow ended with status {completion_status}"
                    )
                    # A microphone completion is unexpected unless Stop cancelled this run.
                    break
                runtime.append_event(record, event)
        except Exception as exc:
            ended_reason = str(exc).strip() or type(exc).__name__
            runtime.append_event(
                record,
                {"type": "workflow_error", "source_id": record.workflow_id, "error": ended_reason},
            )
            with record.lock:
                record.error = ended_reason

        if received_completion and not record.recovery_cancel.is_set():
            # SessionWorkflow publishes completion before its model/process cleanup callback.
            try:
                workflow.wait(timeout=None)
            except Exception as exc:
                ended_reason = str(exc).strip() or type(exc).__name__
        elif ended_reason is not None and not record.recovery_cancel.is_set():
            # A wrapper can finish without a terminal event while its capture source survives.
            try:
                workflow.stop(timeout=30)
            except Exception as exc:
                cleanup_error = str(exc).strip() or type(exc).__name__
                runtime.append_event(
                    record,
                    {
                        "type": "workflow_error",
                        "source_id": record.workflow_id,
                        "error": cleanup_error,
                    },
                )
                with record.lock:
                    record.error = cleanup_error

        _update_microphone_transcript(record, transcript_parts, transcript_events)
        if record.recovery_cancel.is_set():
            record.stop_complete.wait()
            with record.lock:
                if record.status == "stopping":
                    record.status = "stopped"
                if record.destination_id is not None and record.delivery["status"] == "pending":
                    record.delivery.update({"status": "failed", "error_code": "not_attempted"})
                final_status = record.status
            if final_status in {"stopped", "failed"}:
                runtime.append_event(
                    record,
                    {"type": "completed", "source_id": record.workflow_id, "status": final_status},
                )
            return

        recovery_reason = ended_reason or "Microphone workflow ended unexpectedly"
        with record.lock:
            cancelling = record.recovery_cancel.is_set() or runtime.shutdown_event.is_set()
            if not cancelling:
                record.status = "recording"
        if cancelling:
            record.recovery_cancel.set()
            continue
        transcript_parts.append(_transcript_text(transcript_events))
        transcript_events = []
        replacement = None
        while True:
            restart_count += 1
            delay = retry_delay
            runtime.append_event(
                record,
                {
                    "type": "recovering",
                    "attempt": restart_count,
                    "reason": recovery_reason,
                    "delay_seconds": delay,
                },
            )
            if record.recovery_cancel.wait(delay) or runtime.shutdown_event.is_set():
                record.recovery_cancel.set()
                break
            flow_config = {
                "language": record.language,
                "source_id": f"{record.workflow_id}:restart:{restart_count}",
                "overload_policy": "drop",
            }
            if record.silence_threshold is not None:
                flow_config["silence_threshold"] = record.silence_threshold
            if record.profile_id is not None:
                flow_config["decoding_options"] = deepcopy(record.decoding_options)
            start_options = {"execution_mode": record.execution_mode, "model": record.model}
            if record.audio_source_factory is not None:
                start_options["audio_source_factory"] = record.audio_source_factory
            try:
                if record.audio_source_factory is not None and not _accepts_keyword_argument(
                    runtime.workflow_service.start_microphone, "audio_source_factory"
                ):
                    raise WorkflowUnavailableError(
                        "The configured workflow service cannot restart an RTSP audio source"
                    )
                if record.runtime is not None and _accepts_keyword_argument(
                    runtime.workflow_service.start_microphone, "runtime"
                ):
                    start_options["runtime"] = record.runtime
                if _accepts_keyword_argument(runtime.workflow_service.start_microphone, "forwarder"):
                    start_options["forwarder"] = record.forwarder
                replacement = runtime.workflow_service.start_microphone(
                    record.device, record.keywords, flow_config, **start_options
                )
            except Exception as exc:
                reason = str(exc).strip() or type(exc).__name__
                retry_delay = min(retry_delay * 2, runtime.recovery_max_delay)
                runtime.append_event(
                    record,
                    {
                        "type": "recovery_failed",
                        "attempt": restart_count,
                        "reason": reason,
                        "delay_seconds": retry_delay,
                    },
                )
                with record.lock:
                    record.error = reason
                recovery_reason = reason
                if record.recovery_cancel.is_set() or runtime.shutdown_event.is_set():
                    record.recovery_cancel.set()
                    break
                continue
            break

        if replacement is None:
            continue
        with record.lock:
            cancelled = record.recovery_cancel.is_set() or runtime.shutdown_event.is_set()
            record.workflow = replacement
            if not cancelled:
                record.status = "recording"
                record.error = None
        if cancelled:
            try:
                replacement.stop(timeout=30)
            except Exception as exc:
                error = str(exc).strip() or type(exc).__name__
                with record.lock:
                    record.status = "failed"
                    record.error = error
                runtime.append_event(
                    record,
                    {
                        "type": "workflow_error",
                        "workflow_id": record.workflow_id,
                        "source_id": record.workflow_id,
                        "error": error,
                    },
                )
                runtime.append_event(
                    record,
                    {
                        "type": "completed",
                        "source_id": record.workflow_id,
                        "status": "failed",
                    },
                )
                record.stop_complete.set()
                return
            record.recovery_cancel.set()
        else:
            retry_delay = runtime.recovery_initial_delay
            runtime.append_event(
                record,
                {
                    "type": "recovered",
                    "attempt": restart_count,
                    "reason": recovery_reason,
                    "delay_seconds": delay,
                },
            )
        workflow = replacement


def _transcript_text(transcript_events):
    transcript_events.sort(key=lambda event: event.get("sequence", 0))
    return " ".join(
        event.get("text", "")
        for event in transcript_events
        if isinstance(event.get("text", ""), str)
    )


def _update_microphone_transcript(record, transcript_parts, transcript_events):
    with record.lock:
        record.transcript = " ".join(
            part for part in (*transcript_parts, _transcript_text(transcript_events)) if part
        )
