"""HTTP and event-stream adapter for the Feature 01 public callable API."""

import asyncio
import hmac
import json
import os
import queue
import secrets
import tempfile
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from ..model_deployment import (
    list_available_models,
    load_model,
    start_microphone_flow,
    start_multiprocess_microphone_flows,
    transcribe_clip,
)
from ..model_deployment.capacity import run_benchmark
from ..model_deployment.errors import AudioInputError
from ..model_deployment.remote_audio import RemoteAudioSource


class FeatureState:
    def __init__(
        self,
        *,
        runtime_factory=None,
        audio_source_factory=None,
        process_audio_source_factory=None,
    ):
        self.runtime_factory = runtime_factory
        self.audio_source_factory = audio_source_factory
        self.process_audio_source_factory = process_audio_source_factory
        self.models = {}
        self.sessions = {}
        self.session_start_lock = threading.Lock()
        self.groups = {}
        self.recent_errors = []
        self.host_bridge_enabled = os.environ.get(
            "SPEECH_TO_TEXT_HOST_MICROPHONE_BRIDGE", ""
        ).lower() in {"1", "true", "yes"}
        self.host_bridge_url = os.environ.get(
            "SPEECH_TO_TEXT_HOST_MICROPHONE_BRIDGE_URL", "http://127.0.0.1:18767/api"
        ).rstrip("/")
        bridge_url = urlsplit(self.host_bridge_url)
        if (
            bridge_url.scheme != "http"
            or bridge_url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or bridge_url.username
            or bridge_url.password
            or bridge_url.path != "/api"
        ):
            raise ValueError(
                "SPEECH_TO_TEXT_HOST_MICROPHONE_BRIDGE_URL must use local HTTP at /api"
            )

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
                except Exception as abort_exc:  # noqa: BLE001
                    self.record_error(str(abort_exc))
                self.record_error(str(exc))
        for handle_id, handle in list(self.models.items()):
            try:
                handle.close(timeout=5)
            except Exception as exc:  # noqa: BLE001
                self.record_error(str(exc))

    def record_error(self, message):
        self.recent_errors.append({"at": time.time(), "message": message})
        self.recent_errors[:] = self.recent_errors[-20:]

    def stop_process_group(self, group_id, *, timeout=30):
        group = self.groups[group_id]
        group.stop(timeout=timeout)
        return group

    def session_summaries(self, feature_id):
        rows = []
        for kind, collection in (
            ("microphone", self.sessions),
            ("process", self.groups),
        ):
            for key, value in collection.items():
                items = getattr(value, "sessions", [value])
                for session in items:
                    if not getattr(session, "_terminal", False):
                        rows.append(
                            {
                                "feature": feature_id,
                                "kind": kind,
                                "id": session.source_id,
                                "state": "running",
                            }
                        )
        return rows


def _http_error(exc):
    return HTTPException(
        status_code=400, detail={"type": type(exc).__name__, "message": str(exc)}
    )


def create_router(
    *,
    state=None,
    runtime_factory=None,
    audio_source_factory=None,
    process_audio_source_factory=None,
):
    state = state or FeatureState(
        runtime_factory=runtime_factory,
        audio_source_factory=audio_source_factory,
        process_audio_source_factory=process_audio_source_factory,
    )
    router = APIRouter()
    router.state = state

    @router.get("")
    def status():
        return {
            "id": "feature-01-model-deployment",
            "ready": True,
            "models": [
                {
                    "id": key,
                    "model": value.model,
                    "runtime": value.runtime,
                    "precision": value.precision,
                    "state": value.state,
                }
                for key, value in state.models.items()
            ],
        }

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
                    result.append(
                        {
                            "name": device["name"],
                            "channels": device["max_input_channels"],
                            "default": False,
                        }
                    )
            try:
                default_index = sounddevice.default.device[0]
                if 0 <= default_index < len(sounddevice.query_devices()):
                    default_name = sounddevice.query_devices()[default_index]["name"]
                    for device in result:
                        device["default"] = device["name"] == default_name
            except Exception:
                pass
            return {
                "devices": result,
                "default": "OS selected input",
                "capture_mode": "native",
            }
        except Exception as exc:
            return {
                "devices": [],
                "default": "OS selected input",
                "capture_mode": "native",
                "unavailable_reason": str(exc),
            }

    @router.post("/models")
    def load(payload: dict):
        try:
            handle = load_model(payload, runtime_factory=state.runtime_factory)
            handle_id = uuid.uuid4().hex
            state.models[handle_id] = handle
            return {
                "handle_id": handle_id,
                "model": handle.model,
                "runtime": handle.runtime,
                "precision": handle.precision,
                "state": handle.state,
            }
        except Exception as exc:
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
                measurements = []
                transcript = transcribe_clip(
                    Path(temp.name),
                    handle,
                    _flow_form(form),
                    measurement_sink=measurements.append,
                    source_id=source_id,
                )
                elapsed = time.perf_counter() - started
            result = {
                "transcript": transcript,
                "elapsed_seconds": elapsed,
                "source_id": source_id,
                "measurements": measurements,
                "configuration": {
                    "model": handle.model,
                    "runtime": handle.runtime,
                    "precision": handle.precision,
                    "language": form.get("language", "th"),
                    "chunk_seconds": float(form.get("chunk_seconds", 5)),
                    "silence_threshold": float(form.get("silence_threshold", 0.05)),
                },
            }
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
            bridge = payload.get("capture_mode") == "host-bridge"
            if bridge and not state.host_bridge_enabled:
                raise ValueError("Host microphone bridge capture is disabled")
            sample_rate = payload.get("sample_rate")
            if bridge and (
                isinstance(sample_rate, bool) or not isinstance(sample_rate, int)
            ):
                raise ValueError("Host bridge capture requires an integer sample_rate")
            source_factory = (
                (lambda **kwargs: RemoteAudioSource(sample_rate=sample_rate, **kwargs))
                if bridge
                else state.audio_source_factory
            )
            flow = payload.get("flow_config", {})
            requested_source_id = (
                flow.get("source_id") if isinstance(flow, dict) else None
            )
            with state.session_start_lock:
                existing = (
                    state.sessions.get(requested_source_id)
                    if requested_source_id
                    else None
                )
                if existing is not None and not existing._terminal:
                    raise ValueError(
                        f"Microphone source_id {requested_source_id!r} is already active"
                    )
                session = start_microphone_flow(
                    payload.get("device") or None,
                    handle,
                    flow,
                    audio_source_factory=source_factory,
                )
                state.sessions[session.source_id] = session
            if bridge:
                session.source.ingest_token = secrets.token_urlsafe(32)
            return {
                "session_id": session.source_id,
                "source_id": session.source_id,
                "state": "running",
                **({"ingest_token": session.source.ingest_token} if bridge else {}),
            }
        except Exception as exc:
            state.record_error(str(exc))
            raise _http_error(exc)

    @router.get("/capture-capabilities")
    def capture_capabilities():
        return {
            "host_bridge": state.host_bridge_enabled,
            "host_bridge_url": state.host_bridge_url,
        }

    @router.post("/sessions/{source_id}/audio")
    async def push_session_audio(source_id: str, request: Request):
        session = state.sessions.get(source_id)
        if session is None or not isinstance(
            getattr(session, "source", None), RemoteAudioSource
        ):
            raise HTTPException(404, detail="Remote microphone session was not found")
        expected = session.source.ingest_token
        supplied = request.headers.get("authorization", "")
        if not supplied.startswith("Bearer ") or not hmac.compare_digest(
            supplied[7:], expected
        ):
            raise HTTPException(403, detail="Audio ingest token is invalid")
        length = request.headers.get("content-length")
        if (
            length is None
            or not length.isdigit()
            or not 0 < int(length) <= session.source.sample_rate * 4
        ):
            raise HTTPException(
                413, detail="Audio batch size must be between 1 sample and one second"
            )
        try:
            session.source.feed(await request.body())
            return {"accepted": True}
        except Exception as exc:
            session.source._on_error(exc)
            state.record_error(str(exc))
            raise _http_error(exc)

    @router.post("/sessions/{source_id}/capture-error")
    async def fail_remote_session(source_id: str, request: Request):
        session = state.sessions.get(source_id)
        if session is None or not isinstance(
            getattr(session, "source", None), RemoteAudioSource
        ):
            raise HTTPException(404, detail="Remote microphone session was not found")
        supplied = request.headers.get("authorization", "")
        if not supplied.startswith("Bearer ") or not hmac.compare_digest(
            supplied[7:], session.source.ingest_token
        ):
            raise HTTPException(403, detail="Audio ingest token is invalid")
        length = request.headers.get("content-length")
        if length is None or not length.isdigit() or not 0 < int(length) <= 1024:
            raise HTTPException(
                413, detail="Capture failure body must not exceed 1 KiB"
            )
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(
                400, detail="Capture failure must be a JSON object"
            ) from exc
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, str) or not message.strip() or len(message) > 500:
            raise HTTPException(
                400, detail="Capture failure message must contain 1 to 500 characters"
            )
        session.source._on_error(AudioInputError(message))
        return {"accepted": True}

    @router.post("/process-groups")
    def start_process_group(payload: dict):
        try:
            group = start_multiprocess_microphone_flows(
                payload.get("devices", []),
                payload.get("model_config", {}),
                payload.get("flow_config", {}),
                topology=payload.get("topology", "shared-model"),
                runtime_factory=state.runtime_factory,
                audio_source_factory=state.process_audio_source_factory,
                flow_configs=payload.get("flow_configs"),
            )
            group_id = uuid.uuid4().hex
            state.groups[group_id] = group
            return {
                "group_id": group_id,
                "topology": payload.get("topology", "shared-model"),
                "sessions": [
                    {"source_id": session.source_id, "state": "running"}
                    for session in group.sessions
                ],
            }
        except Exception as exc:
            state.record_error(str(exc))
            raise _http_error(exc)

    @router.post("/capacity")
    async def capacity(payload: dict):
        args = SimpleNamespace(
            device=payload.get("devices", []),
            topology=payload.get("topology", "shared-model"),
            duration_seconds=payload.get("duration_seconds", 60),
            stop_timeout=payload.get("stop_timeout", 30),
            model=payload.get("model", "turbo"),
            runtime=payload.get("runtime", "openvino-gpu"),
            precision=payload.get("precision", "source"),
            language=payload.get("language", "th"),
            chunk_seconds=payload.get("chunk_seconds", 5),
            silence_threshold=payload.get("silence_threshold", 0.05),
            queue_capacity=payload.get("queue_capacity", 6),
            enqueue_timeout=payload.get("enqueue_timeout", 1),
        )
        try:
            return await asyncio.to_thread(
                run_benchmark,
                args,
                runtime_factory=state.runtime_factory,
                audio_source_factory=state.process_audio_source_factory,
                flow_configs=payload.get("flow_configs"),
            )
        except Exception as exc:
            state.record_error(str(exc))
            raise _http_error(exc)

    @router.get("/events/{source_id}")
    async def events(source_id: str):
        session = state.sessions.get(source_id)
        if session is None:
            session = next(
                (
                    s
                    for group in state.groups.values()
                    for s in group.sessions
                    if s.source_id == source_id
                ),
                None,
            )
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

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @router.post("/sessions/{source_id}/stop")
    def stop_session(source_id: str):
        session = state.sessions.get(source_id)
        if session is None:
            session = next(
                (
                    s
                    for group in state.groups.values()
                    for s in group.sessions
                    if s.source_id == source_id
                ),
                None,
            )
        if session is None:
            raise HTTPException(404, detail="Session was not found")
        try:
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
            state.stop_process_group(group_id)
            return {"group_id": group_id, "state": "stopped"}
        except Exception as exc:
            state.record_error(str(exc))
            raise _http_error(exc)

    return router


def _flow_form(form):
    result = {
        "language": form.get("language", "th"),
        "chunk_seconds": float(form.get("chunk_seconds", 5)),
        "silence_threshold": float(form.get("silence_threshold", 0.05)),
    }
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
