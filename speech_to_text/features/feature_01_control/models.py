"""HTTP routes for model lifecycle and finite audio clips."""

import tempfile
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from ..model_deployment import list_available_models, load_model, transcribe_clip
from .helpers import flow_form, http_error


def create_model_router(state):
    router = APIRouter()

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
            raise http_error(exc)

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
            raise http_error(exc)

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
            raise http_error(exc)

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
                    flow_form(form),
                    measurement_sink=measurements.append,
                    source_id=source_id,
                )
                elapsed = time.perf_counter() - started
            return {
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
        except Exception as exc:
            state.record_error(str(exc))
            raise http_error(exc)
        finally:
            await file.close()

    return router
