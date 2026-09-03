import json
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Literal

import shutil
import subprocess

import sounddevice as sd
import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from runtimes import load_runtime, probe
from transcribe import (
    DEFAULT_BACKEND_URL,
    DEFAULT_SILENCE_RMS,
    MODEL_REPOS,
    SAMPLE_RATE,
    is_silent,
    load_audio,
    run_recording_session,
    run_replay_session,
)

STATIC_DIR = Path(__file__).parent / "static"
AUDIO_DIR = Path(__file__).parent / "audio"

app = FastAPI()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/help", response_class=HTMLResponse)
def help_page() -> str:
    return (STATIC_DIR / "help.html").read_text(encoding="utf-8")

_lock = threading.Lock()
_runtimes: dict[tuple[str, str], object] = {}  # (runtime, model) -> loaded runtime; loading is slow
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
    runtime: Literal[
        "pytorch", "openvino-gpu", "openvino-cpu", "ctranslate2", "whispercpp"
    ] = "pytorch"
    source: Literal["mic", "file"] = "mic"
    file: str | None = None  # filename inside audio/, when source is "file"
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


def _resolve_audio(name: str | None) -> Path:
    """Resolve a filename to a clip inside audio/, refusing anything outside it."""
    if not name:
        raise HTTPException(status_code=422, detail="No audio file chosen")
    path = (AUDIO_DIR / name).resolve()
    if not path.is_relative_to(AUDIO_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid file name")
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"No such audio file: {name}")
    return path


def _to_16k_mono(path: Path) -> None:
    """Rewrite a clip in place as 16kHz mono, which is all the models accept."""
    import soundfile as sf

    info = sf.info(str(path))
    if info.samplerate == SAMPLE_RATE and info.channels == 1:
        return

    if shutil.which("ffmpeg") is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{path.name} is {info.samplerate}Hz/{info.channels}ch but 16kHz mono is "
                "required, and ffmpeg isn't installed to convert it. Convert it yourself: "
                f"ffmpeg -i in.wav -ar 16000 -ac 1 {path.name}"
            ),
        )

    converted = path.with_suffix(".converted.wav")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-ar", str(SAMPLE_RATE), "-ac", "1", str(converted)],
        capture_output=True,
    )
    if result.returncode != 0:
        converted.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"Could not convert {path.name} to 16kHz mono")
    converted.replace(path)


@app.get("/audio-files")
def audio_files() -> dict:
    import soundfile as sf

    AUDIO_DIR.mkdir(exist_ok=True)
    files = []
    for path in sorted(AUDIO_DIR.glob("*.wav")):
        try:
            info = sf.info(str(path))
            files.append(
                {"name": path.name, "seconds": round(info.duration, 1), "samplerate": info.samplerate}
            )
        except Exception:
            continue  # unreadable file — just leave it out of the list
    return {"files": files}


@app.post("/audio-files")
async def upload_audio(file: UploadFile) -> dict:
    if not file.filename or not file.filename.lower().endswith(".wav"):
        raise HTTPException(status_code=422, detail="Only .wav files are accepted")

    AUDIO_DIR.mkdir(exist_ok=True)
    target = (AUDIO_DIR / Path(file.filename).name).resolve()
    if not target.is_relative_to(AUDIO_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid file name")

    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    try:
        _to_16k_mono(target)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise

    return {"name": target.name}


def _get_runtime(runtime_name: str, model_key: str):
    key = (runtime_name, model_key)
    if key not in _runtimes:
        _runtimes[key] = load_runtime(runtime_name, model_key)
    return _runtimes[key]


def _run_session(req: StartRequest) -> None:
    session_id = _state["session_id"]
    try:
        broadcast({"type": "session", "state": "loading", "session_id": session_id})
        runtime = _get_runtime(req.runtime, req.model)

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

        broadcast(
            {
                "type": "session",
                "state": "recording",
                "session_id": session_id,
                "source": req.source,
                "file": req.file,
            }
        )

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

        if req.source == "file":
            audio = run_replay_session(
                runtime,
                load_audio(str(_resolve_audio(req.file))),
                keywords,
                req.model,
                req.backend_url,
                req.silence_threshold,
                _state["stop_event"],
                session_id,
                on_event=on_event,
            )
        else:
            audio = run_recording_session(
                runtime,
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
            reference_transcript = runtime.transcribe(audio)

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
    # Check the runtime is usable before accepting the request, so an
    # unconverted or uninstalled backend fails immediately with the reason
    # rather than after the session has already gone into "loading".
    if (req.runtime, req.model) not in _runtimes:
        entry = next((e for e in probe(req.model) if e["name"] == req.runtime), None)
        if entry and not entry["available"]:
            raise HTTPException(
                status_code=409, detail=f"Runtime {req.runtime!r} unavailable: {entry['reason']}"
            )

    # same reasoning for the clip: fail now, not after the session starts
    if req.source == "file":
        _resolve_audio(req.file)

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


@app.get("/runtimes")
def runtimes(model: str = "turbo") -> dict:
    """Which execution backends are usable here, and why the others aren't.

    Availability depends on the machine (Intel GPU present?), what's installed
    (openvino, faster-whisper), and whether the weights have been converted for
    that runtime — so it's reported per model.
    """
    if model not in MODEL_REPOS:
        raise HTTPException(status_code=422, detail=f"Unknown model {model!r}")
    loaded = {name for name, key in _runtimes if key == model}
    entries = probe(model)
    for entry in entries:
        entry["loaded"] = entry["name"] in loaded
    return {"runtimes": entries}


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

    # On Linux several host APIs coexist (ALSA, and PulseAudio/PipeWire
    # surfacing through it), and which one a device comes from changes what
    # selecting it actually does — so report it. macOS only has Core Audio.
    hostapis = sd.query_hostapis()

    inputs = [
        {
            "index": index,
            "name": device["name"],
            "channels": device["max_input_channels"],
            "default": index == default_index,
            "hostapi": hostapis[device["hostapi"]]["name"]
            if device["hostapi"] < len(hostapis)
            else "unknown",
        }
        for index, device in enumerate(sd.query_devices())
        if device["max_input_channels"] > 0
    ]
    return {
        "devices": inputs,
        "default_index": default_index,
        "hostapi_count": len({d["hostapi"] for d in inputs}),
    }


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
