"""HTTP routes for microphone sessions and their event streams."""

import asyncio
import hmac
import json
import queue
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..model_deployment import start_microphone_flow
from ..model_deployment.errors import AudioInputError
from ..model_deployment.remote_audio import RemoteAudioSource
from .helpers import http_error


def create_session_router(state):
    router = APIRouter()

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
            raise http_error(exc)

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
            raise http_error(exc)

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

    @router.get("/events/{source_id}")
    async def events(source_id: str):
        session = state.find_session(source_id)
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
        session = state.find_session(source_id)
        if session is None:
            raise HTTPException(404, detail="Session was not found")
        try:
            session.stop()
            return {"source_id": source_id, "state": "stopped"}
        except Exception as exc:
            state.record_error(str(exc))
            raise http_error(exc)

    return router
