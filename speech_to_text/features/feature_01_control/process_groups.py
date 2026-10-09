"""HTTP routes for process groups and capacity measurements."""

import asyncio
import uuid
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException

from ..model_deployment import start_multiprocess_microphone_flows
from ..model_deployment.capacity import run_benchmark
from .helpers import http_error


def create_process_group_router(state):
    router = APIRouter()

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
            raise http_error(exc)

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
            raise http_error(exc)

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
            raise http_error(exc)

    return router
