import json
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Literal

import sounddevice as sd
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from transcribe import (
    DEFAULT_BACKEND_URL,
    DEFAULT_SILENCE_RMS,
    MODEL_REPOS,
    is_silent,
    load_pipeline,
    pick_device,
    run_recording_session,
    transcribe,
)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/help", response_class=HTMLResponse)
def help_page() -> str:
    return (STATIC_DIR / "help.html").read_text(encoding="utf-8")

_lock = threading.Lock()
_pipelines: dict[str, object] = {}  # model_key -> cached pipeline, loading is slow
_state: dict = {
    "status": "idle",  # idle | loading | recording | stopping
    "config": None,
    "session_id": None,
    "stop_event": None,
    "error": None,
    "reference_transcript": None,
}


class StartRequest(BaseModel):
    model: Literal["turbo", "large-v3"]
    keywords: str | None = None
    mic_device: str | None = None
    silence_threshold: float = DEFAULT_SILENCE_RMS
    backend_url: str = DEFAULT_BACKEND_URL


# Live event fan-out: every connected browser gets its own queue, so the feed
# isn't tied to one connection and a slow/dead client can't stall the recording
# thread (events are dropped for that client instead of blocking).
_subscribers: list[queue.Queue] = []
_subscribers_lock = threading.Lock()


def broadcast(event: dict) -> None:
    with _subscribers_lock:
        targets = list(_subscribers)
    for subscriber in targets:
        try:
            subscriber.put_nowait(event)
        except queue.Full:
            pass


@app.get("/stream")
def stream() -> StreamingResponse:
    subscriber: queue.Queue = queue.Queue(maxsize=1000)
    with _subscribers_lock:
        _subscribers.append(subscriber)

    def event_source():
        try:
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            while True:
                try:
                    event = subscriber.get(timeout=15)
                    yield f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            with _subscribers_lock:
                if subscriber in _subscribers:
                    _subscribers.remove(subscriber)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _get_pipeline(model_key: str):
    if model_key not in _pipelines:
        device = pick_device()
        _pipelines[model_key] = load_pipeline(model_key, device)
    return _pipelines[model_key]


def _run_session(req: StartRequest) -> None:
    session_id = _state["session_id"]
    try:
        broadcast({"type": "session", "state": "loading", "session_id": session_id})
        asr_pipeline = _get_pipeline(req.model)

        with _lock:
            if _state["status"] != "loading":
                return  # a stop (or another start) raced us before loading finished
            _state["status"] = "recording"

        keywords = [k.strip() for k in req.keywords.split(",") if k.strip()] if req.keywords else []
        mic_device: int | str | None = req.mic_device
        if mic_device is not None:
            try:
                mic_device = int(mic_device)
            except ValueError:
                pass

        broadcast({"type": "session", "state": "recording", "session_id": session_id})

        def on_event(event: dict) -> None:
            if event["type"] == "chunk":
                if event["silent"]:
                    print(f"[chunk @ {event['time']:.1f}s] (silence, skipped)")
                else:
                    print(
                        f"[chunk @ {event['time']:.1f}s] {event['text']} "
                        f"(latency {event['latency']:.2f}s, RTF {event['rtf']:.2f})"
                    )
            elif event["type"] == "keyword":
                print(f'[keyword detected] "{event["keyword"]}" at {event["time"]:.1f}s')
            elif event["type"] == "warning":
                print(f"[warning] {event['message']}")
            broadcast(event)

        audio = run_recording_session(
            asr_pipeline,
            keywords,
            req.model,
            req.backend_url,
            mic_device,
            req.silence_threshold,
            _state["stop_event"],
            session_id,
            on_event=on_event,
        )

        if is_silent(audio, req.silence_threshold):
            reference_transcript = "(silence, skipped)"
        else:
            reference_transcript = transcribe(asr_pipeline, audio)

        with _lock:
            _state["reference_transcript"] = reference_transcript
            _state["status"] = "idle"

        broadcast(
            {
                "type": "session",
                "state": "stopped",
                "session_id": session_id,
                "reference_transcript": reference_transcript,
            }
        )
    except Exception as exc:  # noqa: BLE001 - surface any failure via /status instead of losing it in a thread
        with _lock:
            _state["error"] = str(exc)
            _state["status"] = "idle"
        broadcast(
            {"type": "session", "state": "error", "session_id": session_id, "message": str(exc)}
        )


@app.post("/start")
def start(req: StartRequest) -> dict:
    with _lock:
        if _state["status"] in ("loading", "recording"):
            raise HTTPException(status_code=409, detail="A recording session is already active")
        _state["status"] = "loading"
        _state["config"] = req.model_dump()
        _state["session_id"] = str(uuid.uuid4())
        _state["stop_event"] = threading.Event()
        _state["error"] = None
        _state["reference_transcript"] = None

    threading.Thread(target=_run_session, args=(req,), daemon=True).start()
    return {"status": "starting", "session_id": _state["session_id"]}


@app.post("/stop")
def stop() -> dict:
    with _lock:
        if _state["status"] == "idle":
            raise HTTPException(status_code=409, detail="No active recording session")
        if _state["status"] == "loading":
            raise HTTPException(
                status_code=409, detail="Still loading the model, try again shortly"
            )
        _state["stop_event"].set()
        _state["status"] = "stopping"
    return {"status": "stopping"}


@app.get("/devices")
def devices(rescan: bool = False) -> dict:
    """Input devices available for recording.

    PortAudio snapshots the device list when it initialises, so a mic plugged
    in (or a headset paired) after the server started won't appear until it's
    re-initialised — hence `rescan`, which is refused mid-session because
    tearing PortAudio down would kill the running stream.
    """
    if rescan:
        with _lock:
            if _state["status"] != "idle":
                raise HTTPException(
                    status_code=409, detail="Can't rescan devices while a session is active"
                )
        try:
            sd._terminate()
            sd._initialize()
        except Exception as exc:  # noqa: BLE001 - report instead of 500ing the picker
            raise HTTPException(status_code=500, detail=f"Device rescan failed: {exc}") from exc

    # sd.default.device is a _InputOutputPair (indexable, but not a tuple and
    # not JSON-serialisable), so pull the input slot out by index.
    try:
        default_index = int(sd.default.device[0])
    except (TypeError, ValueError, IndexError):
        default_index = None

    inputs = [
        {
            "index": index,
            "name": device["name"],
            "channels": device["max_input_channels"],
            "default": index == default_index,
        }
        for index, device in enumerate(sd.query_devices())
        if device["max_input_channels"] > 0
    ]
    return {"devices": inputs, "default_index": default_index}


@app.get("/status")
def status() -> dict:
    with _lock:
        return {
            "status": _state["status"],
            "session_id": _state["session_id"],
            "config": _state["config"],
            "error": _state["error"],
            "reference_transcript": _state["reference_transcript"],
        }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5001)
