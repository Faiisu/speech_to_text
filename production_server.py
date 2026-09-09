"""The production service: configure stations, watch them, keep them running.

Deliberately a separate app from server.py. That one is the testing panel — it
loads arbitrary runtimes, runs benchmarks that saturate the machine, and
assumes one session at a time. This one runs three microphones for weeks. They
should not be able to break each other, so they don't share a process.
"""

from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from benchmark_parallel import MEDIA_SUFFIXES, Options, Run, collect_clips, summarise
from model_catalog import discover
from runtimes import probe, requires_single_worker
from stations import config as station_config
from stations.capture import DeviceError, input_devices, resolve_device
from stations.config import ConfigError, Settings, Station
from stations.replay import MediaError, media_duration
from stations.supervisor import Supervisor

STATIC_DIR = Path(__file__).parent / "static"
AUDIO_DIR = Path(__file__).parent / "audio"

# A measurement shorter than this lets the post-run drain window absorb the
# backlog, so a run that is actually falling behind reports as sustained.
# Four chunks is the floor at which the queue has to show its true state.
MIN_DURATION_CHUNKS = 4
MIN_DURATION_SECONDS = 20.0

app = FastAPI(title="Typhoon Whisper Stations")

_lock = threading.Lock()
_supervisor: Supervisor | None = None
# One benchmark at a time. Two concurrent runs would contend for the same
# hardware and silently corrupt the very timings the benchmark exists to
# produce — the same bug the PoC panel hit (issue 14).
_benchmark_lock = threading.Lock()
_benchmark_running = False
_subscribers: list[queue.Queue] = []
_subscribers_lock = threading.Lock()
# The operator UI is opened after the fact, so it needs the recent past as well
# as the live feed — a blank page on a service that has been running for a week
# tells you nothing.
_recent: dict[str, list] = {}
RECENT_PER_STATION = 50


def broadcast(event: dict) -> None:
    if event.get("type") in ("chunk", "keyword") and event.get("station_id"):
        history = _recent.setdefault(event["station_id"], [])
        history.append(event)
        del history[:-RECENT_PER_STATION]
    with _subscribers_lock:
        targets = list(_subscribers)
    for subscriber in targets:
        try:
            subscriber.put_nowait(event)
        except queue.Full:
            # A subscriber that can't keep up loses events rather than
            # slowing the inference worker down. The health endpoint, not the
            # feed, is the source of truth for what happened.
            pass


# -- configuration ---------------------------------------------------------


class StationBody(BaseModel):
    id: str
    label: str
    device: str
    keywords: list[str] = Field(default_factory=list)
    language: str = "th"
    enabled: bool = True
    silence_threshold: float = 0.02
    chunk_seconds: float = 5.0
    overlap_seconds: float = 1.0


class ConfigBody(BaseModel):
    model: str = station_config.DEFAULT_MODEL
    runtime: str = station_config.DEFAULT_RUNTIME
    backend_url: str = "http://localhost:8000"
    queue_size: int = station_config.DEFAULT_QUEUE_SIZE
    workers: int = 1
    stations: list[StationBody] = Field(default_factory=list)


def _load_settings() -> Settings:
    try:
        return station_config.load()
    except ConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None


@app.get("/api/config")
def get_config() -> dict:
    settings = _load_settings()
    return json.loads(json.dumps(settings, default=lambda o: o.__dict__))


@app.put("/api/config")
def put_config(body: ConfigBody) -> dict:
    try:
        settings = Settings(
            model=body.model,
            runtime=body.runtime,
            backend_url=body.backend_url,
            queue_size=body.queue_size,
            workers=body.workers,
            stations=[Station(**s.model_dump()) for s in body.stations],
        )
    except ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    # Check every configured microphone before saving. Saving a config whose
    # devices don't exist would leave the operator with three stations that
    # each fail at start for a reason they'd have to go read the logs to find.
    missing = []
    for station in settings.stations:
        if not station.enabled:
            continue
        try:
            resolve_device(station.device)
        except DeviceError as exc:
            missing.append(f"{station.label}: {exc}")
    if missing:
        raise HTTPException(status_code=422, detail=" | ".join(missing))

    station_config.save(settings)
    with _lock:
        running = _supervisor is not None
    return {"status": "saved", "restart_required": running}


@app.get("/api/models")
def models() -> dict:
    """Models this machine can offer, rediscovered on every call.

    Rediscovered rather than cached so a model downloaded or converted while
    the service runs shows up without a restart.
    """
    return {
        "models": [
            {"key": key, "repo": entry["repo"], "converted": entry["converted"],
             "multilingual": entry["multilingual"]}
            for key, entry in sorted(discover().items())
        ]
    }


@app.get("/api/runtimes")
def runtimes(model: str = station_config.DEFAULT_MODEL) -> dict:
    """Which runtimes can actually run here, and why the others can't.

    Availability is per model — a runtime needs that model's weights converted
    for it — so the answer changes when the model does. The panel offered
    openvino-npu while the door rejected it precisely because this wasn't
    asked; the production UI asks.
    """
    if model not in discover():
        raise HTTPException(status_code=422, detail=f"Unknown model {model!r}")
    return {"runtimes": probe(model)}


@app.get("/api/devices")
def devices() -> dict:
    """Input devices, so the operator picks a name instead of typing one."""
    return {"devices": input_devices()}


# -- service lifecycle -----------------------------------------------------


@app.post("/api/service/start")
def start_service() -> dict:
    global _supervisor
    with _lock:
        if _supervisor is not None:
            raise HTTPException(status_code=409, detail="Service is already running")
        settings = _load_settings()
        if not [s for s in settings.stations if s.enabled]:
            raise HTTPException(status_code=422, detail="No enabled stations configured")
        supervisor = Supervisor(settings, on_event=broadcast)
        _supervisor = supervisor

    def run() -> None:
        global _supervisor
        try:
            supervisor.start()
        except Exception as exc:  # noqa: BLE001 - report instead of dying silently
            broadcast({"type": "service", "state": "error", "message": str(exc)})
            with _lock:
                _supervisor = None

    # Loading the model takes long enough that doing it inline would time the
    # request out and leave the operator unsure whether it started.
    threading.Thread(target=run, name="service-start", daemon=True).start()
    return {"status": "starting"}


@app.post("/api/service/stop")
def stop_service() -> dict:
    global _supervisor
    with _lock:
        supervisor, _supervisor = _supervisor, None
    if supervisor is None:
        raise HTTPException(status_code=409, detail="Service is not running")
    supervisor.stop()
    return {"status": "stopped"}


@app.post("/api/stations/{station_id}/start")
def start_station(station_id: str) -> dict:
    supervisor = _require_running()
    station = supervisor.settings.station(station_id)
    if station is None:
        raise HTTPException(status_code=404, detail=f"Unknown station {station_id!r}")
    try:
        resolve_device(station.device)
    except DeviceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    supervisor.start_station(station)
    return {"status": "started"}


@app.post("/api/stations/{station_id}/stop")
def stop_station(station_id: str) -> dict:
    supervisor = _require_running()
    if supervisor.settings.station(station_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown station {station_id!r}")
    supervisor.stop_station(station_id)
    return {"status": "stopped"}


def _require_running() -> Supervisor:
    with _lock:
        if _supervisor is None:
            raise HTTPException(status_code=409, detail="Service is not running")
        return _supervisor


# -- monitoring ------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    with _lock:
        supervisor = _supervisor
    if supervisor is None:
        settings = _load_settings()
        return {
            "running": False,
            "model": settings.model,
            "runtime": settings.runtime,
            "stations": [
                {"id": s.id, "label": s.label, "device": s.device, "enabled": s.enabled,
                 "keywords": s.keywords, "language": s.language, "running": False}
                for s in settings.stations
            ],
        }
    return {"running": True, **supervisor.health()}


@app.get("/api/recent")
def recent(station_id: str | None = None) -> dict:
    """What the feed missed, for a page opened mid-run."""
    if station_id:
        return {"events": _recent.get(station_id, [])}
    merged = [event for events in _recent.values() for event in events]
    merged.sort(key=lambda e: e.get("time", 0))
    return {"events": merged}


@app.get("/api/events")
def events(station: str | None = None, word: str | None = None, limit: int = 200) -> dict:
    """History from TimescaleDB, proxied so the UI has a single origin."""
    settings = _load_settings()
    params = {"limit": limit}
    if station:
        params["station"] = station
    if word:
        params["word"] = word
    try:
        response = requests.get(f"{settings.backend_url}/events", params=params, timeout=5)
        response.raise_for_status()
    except requests.RequestException as exc:
        # The DB being down must not blank the live monitor: the UI shows the
        # history panel as unavailable and keeps streaming.
        raise HTTPException(status_code=503, detail=f"Backend unreachable: {exc}") from None
    return {"events": response.json()}


@app.get("/api/stream")
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
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with _subscribers_lock:
                if subscriber in _subscribers:
                    _subscribers.remove(subscriber)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# -- benchmark -------------------------------------------------------------


class BenchmarkBody(BaseModel):
    """A capacity measurement, single or parallel."""

    clips: list[str] = Field(default_factory=list)
    # [1] is a single-stream run; [1,2,3] sweeps to find where it stops.
    streams: list[int] = Field(default_factory=lambda: [1])
    duration: float = 60.0
    model: str = station_config.DEFAULT_MODEL
    runtime: str = station_config.DEFAULT_RUNTIME
    language: str = "th"
    chunk_seconds: float = 5.0
    overlap_seconds: float = 1.0
    silence_threshold: float = 0.02
    queue_size: int = 6
    workers: int = 1
    realtime: bool = True


@app.get("/api/clips")
def clips() -> dict:
    """Audio and video in audio/ that can be used as benchmark material."""
    if not AUDIO_DIR.exists():
        return {"clips": []}
    found = []
    for path in sorted(AUDIO_DIR.iterdir()):
        if path.suffix.lower() in MEDIA_SUFFIXES:
            duration = media_duration(path)
            found.append({
                "name": path.name,
                # Length is what tells the operator whether a clip is long
                # enough for the run; file size tells them nothing useful.
                "duration": round(duration, 1) if duration is not None else None,
            })
    return {"clips": found}


def _resolve_clip(name: str) -> Path:
    """Contain clip names to audio/ — the panel had this exact hole (issue 14)."""
    candidate = (AUDIO_DIR / name).resolve()
    if not str(candidate).startswith(str(AUDIO_DIR.resolve()) + "/") or not candidate.exists():
        raise HTTPException(status_code=422, detail=f"No such clip: {name}")
    return candidate


@app.post("/api/benchmark")
def benchmark(body: BenchmarkBody) -> StreamingResponse:
    """Stream a capacity measurement as it runs.

    Refuses while stations are live: the benchmark deliberately saturates the
    machine, and running it against microphones that are recording would both
    corrupt the timings and starve the real work.
    """
    global _benchmark_running

    with _lock:
        if _supervisor is not None:
            raise HTTPException(
                status_code=409,
                detail="Stop the station service first — a benchmark saturates the "
                       "machine and would starve the live microphones.",
            )
    if not body.clips:
        raise HTTPException(status_code=422, detail="Pick at least one clip")
    counts = sorted({n for n in body.streams if n > 0})
    if not counts:
        raise HTTPException(status_code=422, detail="Give at least one stream count")
    if max(counts) > 16:
        raise HTTPException(status_code=422, detail="16 streams is the ceiling here")

    if body.workers > 1 and requires_single_worker(body.runtime):
        # Caught here rather than when Run builds its Settings, so it fails
        # before the model loads instead of a minute into the stream.
        raise HTTPException(
            status_code=422,
            detail=f"{body.runtime} runs on a single exclusive accelerator and must use "
                   "1 worker. Extra workers contend for the same execution units.",
        )

    floor = max(MIN_DURATION_SECONDS, body.chunk_seconds * MIN_DURATION_CHUNKS)
    if body.duration < floor:
        raise HTTPException(
            status_code=422,
            detail=f"Duration must be at least {floor:g}s for a {body.chunk_seconds:g}s chunk. "
                   "A shorter run lets the drain window absorb the backlog, so a run that is "
                   "actually falling behind reports as sustained.",
        )

    paths = [str(_resolve_clip(name)) for name in body.clips]
    try:
        resolved = collect_clips(paths)
    except (SystemExit, MediaError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    with _benchmark_lock:
        if _benchmark_running:
            raise HTTPException(status_code=409, detail="A benchmark is already running")
        _benchmark_running = True

    options = Options(
        duration=body.duration,
        model=body.model,
        runtime=body.runtime,
        language=body.language,
        chunk_seconds=body.chunk_seconds,
        overlap_seconds=body.overlap_seconds,
        silence_threshold=body.silence_threshold,
        queue_size=body.queue_size,
        workers=body.workers,
        realtime=body.realtime,
    )

    def event_source():
        global _benchmark_running
        feed: queue.Queue = queue.Queue()
        results: list[dict] = []

        def run() -> None:
            try:
                for count in counts:
                    results.append(Run(count, options, resolved, on_event=feed.put).execute())
                feed.put({
                    "type": "complete",
                    "results": results,
                    "summary": summarise(results, body.chunk_seconds, body.runtime),
                })
            except Exception as exc:  # noqa: BLE001 - surface it in the stream
                feed.put({"type": "failed", "message": str(exc)})
            finally:
                feed.put(None)

        worker = threading.Thread(target=run, name="benchmark", daemon=True)
        worker.start()
        try:
            yield f"data: {json.dumps({'type': 'init', 'streams': counts, 'clips': [c.name for c in resolved], 'model': body.model, 'runtime': body.runtime, 'duration': body.duration, 'chunk_seconds': body.chunk_seconds, 'realtime': body.realtime}, ensure_ascii=False)}\n\n"
            while True:
                event = feed.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            # Released however this ends, including the operator closing the
            # tab mid-run — otherwise an abandoned run blocks every later one.
            with _benchmark_lock:
                _benchmark_running = False

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "monitor.html").read_text(encoding="utf-8")
