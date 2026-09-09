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

from stations import config as station_config
from stations.capture import DeviceError, input_devices, resolve_device
from stations.config import ConfigError, Settings, Station
from stations.supervisor import Supervisor

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Typhoon Whisper Stations")

_lock = threading.Lock()
_supervisor: Supervisor | None = None
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


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "monitor.html").read_text(encoding="utf-8")
