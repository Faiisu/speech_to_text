"""Starts, stops, and watches over the stations.

The failure this exists for: a USB microphone unplugged at 3am. One station's
stream dying must not stop the other two, must be visible rather than silent,
and must recover on its own when the mic comes back.
"""

from __future__ import annotations

import threading
import time

from stations.capture import DeviceError, StationCapture, resolve_device
from stations.config import Settings, Station
from stations.engine import Engine

# How often the watchdog checks whether a capture thread is still alive, and
# how long it waits before retrying one that failed. A dead USB mic usually
# comes back within seconds of being replugged; retrying faster than this just
# fills the log with the same error.
WATCH_INTERVAL_SECONDS = 5.0
RETRY_BACKOFF_SECONDS = 15.0


class Supervisor:
    """Owns the running stations and the shared engine."""

    def __init__(self, settings: Settings, on_event=None) -> None:
        self.settings = settings
        self._on_event = on_event
        self.engine = Engine(settings, on_event=on_event)
        self._captures: dict[str, StationCapture] = {}
        self._failed_at: dict[str, float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._watchdog: threading.Thread | None = None
        self.started_at: float | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Load the model, then bring up every enabled station."""
        # Loading first means a model that can't load fails once, here, rather
        # than as three identical errors from three capture threads.
        self._emit({"type": "service", "state": "loading", "model": self.settings.model,
                    "runtime": self.settings.runtime})
        self.engine.load()
        self.engine.start()
        self._stop.clear()
        self.started_at = time.time()

        for station in self.settings.stations:
            if station.enabled:
                self.start_station(station)

        self._watchdog = threading.Thread(target=self._watch, name="watchdog", daemon=True)
        self._watchdog.start()
        self._emit({"type": "service", "state": "running", "stations": self.station_ids})

    def stop(self) -> None:
        self._stop.set()
        if self._watchdog:
            self._watchdog.join(timeout=WATCH_INTERVAL_SECONDS * 2)
        with self._lock:
            captures = list(self._captures.values())
            self._captures.clear()
        for capture in captures:
            capture.stop()
        self.engine.stop()
        self._emit({"type": "service", "state": "stopped"})

    def start_station(self, station: Station) -> None:
        with self._lock:
            existing = self._captures.get(station.id)
            if existing and existing.running:
                return
            self.engine.register(station)
            capture = StationCapture(
                station, sink=self.engine.submit, on_error=self._on_capture_error
            )
            self._captures[station.id] = capture
        capture.start()
        self._emit({"type": "station", "state": "started", "station_id": station.id,
                    "label": station.label, "device": station.device})

    def stop_station(self, station_id: str) -> None:
        with self._lock:
            capture = self._captures.pop(station_id, None)
        if capture:
            capture.stop()
            self._emit({"type": "station", "state": "stopped", "station_id": station_id,
                        "label": capture.station.label})

    # -- health ------------------------------------------------------------

    @property
    def station_ids(self) -> list[str]:
        with self._lock:
            return list(self._captures)

    def health(self) -> dict:
        with self._lock:
            captures = dict(self._captures)
        stations = []
        for station in self.settings.stations:
            capture = captures.get(station.id)
            entry = {
                "id": station.id,
                "label": station.label,
                "device": station.device,
                "enabled": station.enabled,
                "keywords": station.keywords,
                "language": station.language,
                "running": bool(capture and capture.running),
                "buffered_seconds": round(capture.buffered_seconds, 1) if capture else 0.0,
                "stream_errors": capture.stream_errors if capture else 0,
            }
            health = self.engine.health.get(station.id)
            if health:
                entry.update(health.as_dict())
            stations.append(entry)
        return {
            "model": self.settings.model,
            "runtime": self.settings.runtime,
            "workers": self.settings.workers,
            "queue_depth": self.engine.queue.depth,
            "queue_size": self.settings.queue_size,
            "uptime_seconds": round(time.time() - self.started_at) if self.started_at else 0,
            "stations": stations,
        }

    # -- watchdog ----------------------------------------------------------

    def _on_capture_error(self, station: Station, exc: Exception) -> None:
        self._failed_at[station.id] = time.time()
        health = self.engine.health.get(station.id)
        if health:
            health.last_error = str(exc)
        # A missing device is the operator's problem to fix and says so
        # plainly; anything else is reported as-is rather than guessed at.
        kind = "device" if isinstance(exc, DeviceError) else "capture"
        self._emit({"type": "station", "state": "error", "station_id": station.id,
                    "label": station.label, "kind": kind, "message": str(exc)})

    def _watch(self) -> None:
        while not self._stop.wait(WATCH_INTERVAL_SECONDS):
            for station in self.settings.stations:
                if not station.enabled:
                    continue
                with self._lock:
                    capture = self._captures.get(station.id)
                if capture and capture.running:
                    continue
                failed_at = self._failed_at.get(station.id, 0.0)
                if time.time() - failed_at < RETRY_BACKOFF_SECONDS:
                    continue
                try:
                    resolve_device(station.device)
                except DeviceError:
                    # Still absent. Stay quiet until the backoff elapses again
                    # rather than logging the same line every five seconds.
                    self._failed_at[station.id] = time.time()
                    continue
                self._emit({"type": "station", "state": "restarting",
                            "station_id": station.id, "label": station.label})
                self.start_station(station)

    def _emit(self, event: dict) -> None:
        if self._on_event:
            self._on_event(event)
