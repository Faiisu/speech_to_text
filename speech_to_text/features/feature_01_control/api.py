"""HTTP and event-stream adapter for the Feature 01 public callable API."""

import asyncio
import json
import queue
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from ..model_deployment import (list_available_models, load_model, start_microphone_flow,
                                start_multiprocess_microphone_flows, transcribe_clip)
from ..model_deployment.capacity import run_benchmark
from ..model_deployment.telemetry import publish_service_event
from ..system_observability.sampler import ProcessDescriptor
from ..system_observability.writer import make_event


def observation_processes(state):
    """Return child process IDs explicitly owned by Feature 01 process groups."""
    descriptors = []
    for group_id, group in tuple(state.groups.items()):
        model_process = getattr(group, "_model_process", None)
        if model_process is not None and model_process.pid:
            descriptors.append(ProcessDescriptor("feature-01-model-deployment", "model-worker",
                                                 f"process-group:{group_id}", model_process.pid))
        for session in tuple(getattr(group, "sessions", ())):
            process = getattr(session, "process", None)
            if process is not None and process.pid:
                role = "capture-worker" if model_process is not None else "capture-model-worker"
                descriptors.append(ProcessDescriptor("feature-01-model-deployment", role,
                                                     f"source:{session.source_id}", process.pid))
    return descriptors


def persisted_measurements(state):
    """Drain records from explicitly owned Feature 01 process groups."""
    records = []
    for group in tuple(state.groups.values()):
        dropped_counter = getattr(group, "measurement_drop_counter", None)
        if dropped_counter is not None and state.measurement_writer is not None:
            dropped_now = int(dropped_counter.value)
            dropped_before = getattr(group, "_reported_measurement_drops", 0)
            if dropped_now > dropped_before:
                state.measurement_writer.publish_event(make_event(event_name="feature_measurement_queue_saturated",
                    severity="warning", feature_id="feature-01-model-deployment",
                    attributes={"dropped_count": dropped_now - dropped_before,
                                "role": getattr(group, "topology", "process-group")}))
                group._reported_measurement_drops = dropped_now
        source = getattr(group, "measurement_queue", None)
        if source is None:
            continue
        while True:
            try:
                records.append(source.get_nowait())
            except (queue.Empty, OSError, ValueError):
                break
    return records


class FeatureState:
    def __init__(self, *, runtime_factory=None, audio_source_factory=None, process_audio_source_factory=None,
                 measurement_writer=None):
        self.runtime_factory = runtime_factory
        self.audio_source_factory = audio_source_factory
        self.process_audio_source_factory = process_audio_source_factory
        self.measurement_writer = measurement_writer
        self.models = {}
        self.sessions = {}
        self.groups = {}
        self.recent_errors = []

    def close_all(self):
        for session in list(self.sessions.values()):
            try:
                session.stop(timeout=5)
            except Exception as exc:
                self.record_error(str(exc))
        for group_id, group in list(self.groups.items()):
            try:
                self.stop_process_group(group_id, timeout=5)
            except Exception as exc:
                try:
                    group.abort(timeout=2)
                except Exception as abort_exc:
                    self.record_error(str(abort_exc))
                self.record_error(str(exc))
        for handle_id, handle in list(self.models.items()):
            try:
                handle.close(timeout=5)
                self.publish_event("model_closed", source_id=handle_id,
                                   attributes={"state": "closed", "operation": "service_shutdown"})
            except Exception as exc:
                self.record_error(str(exc))

    def record_error(self, message):
        self.recent_errors.append({"at": time.time(), "message": message})
        self.recent_errors[:] = self.recent_errors[-20:]
        publish_service_event(self.measurement_writer, event_name="feature_operation_failed", severity="error",
                              attributes={"code": "operation_failed"})

    def publish_event(self, event_name, *, severity="info", source_id=None, attributes=None, pid=None):
        return publish_service_event(self.measurement_writer, event_name=event_name, severity=severity,
                                     source_id=source_id, attributes=attributes, pid=pid)

    def stop_process_group(self, group_id, *, timeout=30):
        group = self.groups[group_id]
        group.stop(timeout=timeout)
        if not getattr(group, "_telemetry_stopped", False):
            self.publish_event("process_group_stopped", source_id=group_id,
                attributes={"state": "stopped", "topology": getattr(group, "topology", "process-group")})
            group._telemetry_stopped = True
        return group

    def publish_measurement(self, record):
        if self.measurement_writer is None:
            return False
        published = self.measurement_writer.publish_measurement(record)
        if record.get("status") == "failed" and record.get("operation") == "finite-clip":
            self.measurement_writer.publish_event(make_event(event_name="inference_chunk_failed", severity="error",
                feature_id=record.get("feature_id", "feature-01-model-deployment"),
                pid=record.get("pid"), source_id=record.get("source_id"),
                attributes={"operation": record.get("operation", "inference")}))
        return published

    def session_summaries(self, feature_id):
        rows = []
        for kind, collection in (("microphone", self.sessions), ("process", self.groups)):
            for key, value in collection.items():
                items = getattr(value, "sessions", [value])
                for session in items:
                    if not getattr(session, "_terminal", False):
                        rows.append({"feature": feature_id, "kind": kind, "id": session.source_id, "state": "running"})
        return rows


def _http_error(exc):
    return HTTPException(status_code=400, detail={"type": type(exc).__name__, "message": str(exc)})


def create_router(*, state=None, runtime_factory=None, audio_source_factory=None,
                  process_audio_source_factory=None, measurement_writer=None):
    state = state or FeatureState(runtime_factory=runtime_factory, audio_source_factory=audio_source_factory,
                                  process_audio_source_factory=process_audio_source_factory,
                                  measurement_writer=measurement_writer)
    router = APIRouter()
    router.state = state

    @router.get("")
    def status():
        return {"id": "feature-01-model-deployment", "ready": True,
                "models": [{"id": key, "model": value.model, "runtime": value.runtime,
                            "precision": value.precision, "state": value.state}
                           for key, value in state.models.items()]}

    @router.get("/catalog")
    def catalog():
        try:
            return {"models": list_available_models()}
        except Exception as exc:
            state.record_error(str(exc))
            raise _http_error(exc)

    @router.get("/devices")
    def devices():
        try:
            import sounddevice
            result = []
            for device in sounddevice.query_devices():
                if device.get("max_input_channels", 0) > 0:
                    result.append({"name": device["name"], "channels": device["max_input_channels"], "default": False})
            try:
                default_index = sounddevice.default.device[0]
                if 0 <= default_index < len(sounddevice.query_devices()):
                    default_name = sounddevice.query_devices()[default_index]["name"]
                    for device in result:
                        device["default"] = device["name"] == default_name
            except Exception:
                pass
            return {"devices": result, "default": "OS selected input"}
        except Exception as exc:
            return {"devices": [], "default": "OS selected input", "unavailable_reason": str(exc)}

    @router.post("/models")
    def load(payload: dict):
        try:
            handle = load_model(payload, runtime_factory=state.runtime_factory)
            if state.measurement_writer is not None:
                handle._measurement_sink = state.publish_measurement
            handle._event_sink = state.measurement_writer
            handle_id = uuid.uuid4().hex
            state.models[handle_id] = handle
            state.publish_event("model_loaded", source_id=handle_id,
                                attributes={"state": "ready", "operation": "load_model"})
            return {"handle_id": handle_id, "model": handle.model, "runtime": handle.runtime,
                    "precision": handle.precision, "state": handle.state}
        except Exception as exc:
            state.publish_event("model_load_failed", severity="error",
                                attributes={"code": type(exc).__name__, "operation": "load_model"})
            state.record_error(str(exc))
            raise _http_error(exc)

    @router.delete("/models/{handle_id}")
    def close(handle_id: str):
        handle = state.models.get(handle_id)
        if handle is None:
            raise HTTPException(404, detail="Model handle was not found")
        try:
            handle.close()
            del state.models[handle_id]
            state.publish_event("model_closed", source_id=handle_id,
                                attributes={"state": "closed", "operation": "close_model"})
            return {"handle_id": handle_id, "state": "closed"}
        except Exception as exc:
            state.record_error(str(exc))
            raise _http_error(exc)

    @router.post("/clips")
    async def clip(request: Request, file: UploadFile = File(...)):
        form = await request.form()
        handle = state.models.get(str(form.get("handle_id", "")))
        if handle is None:
            raise HTTPException(400, detail="Load a model before transcribing a clip")
        raw = await file.read()
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav") as temp:
                temp.write(raw)
                temp.flush()
                started = time.perf_counter()
                source_id = uuid.uuid4().hex
                transcript = transcribe_clip(Path(temp.name), handle, _flow_form(form),
                    measurement_sink=state.publish_measurement if state.measurement_writer else None,
                    source_id=source_id)
                elapsed = time.perf_counter() - started
            result = {"transcript": transcript, "elapsed_seconds": elapsed,
                      "source_id": source_id,
                      "configuration": {"model": handle.model, "runtime": handle.runtime,
                                         "precision": handle.precision, "language": form.get("language", "th"),
                                         "chunk_seconds": float(form.get("chunk_seconds", 5)),
                                         "silence_threshold": float(form.get("silence_threshold", 0.05))}}
            return result
        except Exception as exc:
            state.record_error(str(exc))
            raise _http_error(exc)
        finally:
            await file.close()

    @router.post("/microphones")
    def start_microphone(payload: dict):
        handle = state.models.get(payload.get("handle_id"))
        if handle is None:
            raise HTTPException(400, detail="Load a model before starting a microphone")
        try:
            session = start_microphone_flow(payload.get("device") or None, handle, payload.get("flow_config", {}),
                                            audio_source_factory=state.audio_source_factory)
            state.sessions[session.source_id] = session
            return {"session_id": session.source_id, "source_id": session.source_id, "state": "running"}
        except Exception as exc:
            state.record_error(str(exc))
            raise _http_error(exc)

    @router.post("/process-groups")
    def start_process_group(payload: dict):
        try:
            group = start_multiprocess_microphone_flows(
                payload.get("devices", []), payload.get("model_config", {}), payload.get("flow_config", {}),
                topology=payload.get("topology", "shared-model"),
                runtime_factory=state.runtime_factory,
                audio_source_factory=state.process_audio_source_factory,
                flow_configs=payload.get("flow_configs"), event_sink=state.measurement_writer)
            group_id = uuid.uuid4().hex
            state.groups[group_id] = group
            state.publish_event("process_group_started", source_id=group_id,
                                attributes={"state": "running", "topology": group.topology})
            for session in group.sessions:
                state.publish_event("microphone_session_started", source_id=session.source_id,
                    attributes={"state": "running", "topology": group.topology})
            return {"group_id": group_id, "topology": payload.get("topology", "shared-model"),
                    "sessions": [{"source_id": session.source_id, "state": "running"} for session in group.sessions]}
        except Exception as exc:
            state.record_error(str(exc))
            raise _http_error(exc)

    @router.post("/capacity")
    async def capacity(payload: dict):
        args = SimpleNamespace(
            device=payload.get("devices", []), topology=payload.get("topology", "shared-model"),
            duration_seconds=payload.get("duration_seconds", 60), stop_timeout=payload.get("stop_timeout", 30),
            model=payload.get("model", "turbo"), runtime=payload.get("runtime", "openvino-gpu"),
            precision=payload.get("precision", "source"), language=payload.get("language", "th"),
            chunk_seconds=payload.get("chunk_seconds", 5), silence_threshold=payload.get("silence_threshold", 0.05),
            queue_capacity=payload.get("queue_capacity", 6), enqueue_timeout=payload.get("enqueue_timeout", 1))
        try:
            return await asyncio.to_thread(run_benchmark, args, runtime_factory=state.runtime_factory,
                                           audio_source_factory=state.process_audio_source_factory,
                                           flow_configs=payload.get("flow_configs"))
        except Exception as exc:
            state.record_error(str(exc))
            raise _http_error(exc)

    @router.get("/events/{source_id}")
    async def events(source_id: str):
        session = state.sessions.get(source_id)
        if session is None:
            session = next((s for group in state.groups.values() for s in group.sessions if s.source_id == source_id), None)
        if session is None:
            raise HTTPException(404, detail="Session was not found")

        async def stream():
            sequence = 0
            while True:
                try:
                    event = await asyncio.to_thread(session.result_queue.get, True, 0.5)
                except queue.Empty:
                    if getattr(session, "_terminal", False):
                        break
                    yield ": keep-alive\n\n"
                    continue
                event = dict(event)
                event.setdefault("source_id", source_id)
                event["event_sequence"] = sequence
                sequence += 1
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") == "completed":
                    break

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @router.post("/sessions/{source_id}/stop")
    def stop_session(source_id: str):
        session = state.sessions.get(source_id)
        if session is None:
            session = next((s for group in state.groups.values() for s in group.sessions if s.source_id == source_id), None)
        if session is None:
            raise HTTPException(404, detail="Session was not found")
        try:
            state.publish_event("microphone_session_stop_requested", source_id=source_id,
                                attributes={"state": "stopping"})
            session.stop()
            return {"source_id": source_id, "state": "stopped"}
        except Exception as exc:
            state.record_error(str(exc))
            raise _http_error(exc)

    @router.post("/process-groups/{group_id}/stop")
    def stop_group(group_id: str):
        group = state.groups.get(group_id)
        if group is None:
            raise HTTPException(404, detail="Process group was not found")
        try:
            state.publish_event("process_group_stop_requested",
                                source_id=group_id,
                                attributes={"state": "stopping", "topology": group.topology})
            state.stop_process_group(group_id)
            return {"group_id": group_id, "state": "stopped"}
        except Exception as exc:
            state.record_error(str(exc))
            raise _http_error(exc)

    return router


def _flow_form(form):
    result = {"language": form.get("language", "th"),
              "chunk_seconds": float(form.get("chunk_seconds", 5)),
              "silence_threshold": float(form.get("silence_threshold", 0.05))}
    options = {}
    for key, cast in (("beam_size", int), ("temperature", float)):
        if form.get(key) not in (None, ""):
            options[key] = cast(form[key])
    for key in ("condition_on_previous_text",):
        if form.get(key) not in (None, ""):
            options[key] = form[key].lower() == "true"
    if options:
        result["decoding_options"] = options
    return result
