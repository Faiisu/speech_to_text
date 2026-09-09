"""One model, one queue, every station.

Three capture threads cannot each call the model: a single iGPU runs one
decode at a time, so three concurrent callers contend and all three fall
behind. Instead the stations produce chunks into a bounded queue and a single
worker consumes them against one shared runtime.

The queue is bounded on purpose. If the model can't keep up, something has to
give, and the choice is between unbounded latency and dropped audio. Dropping
is the honest failure: a chunk from four minutes ago has no monitoring value,
and a drop is counted and shown, whereas a growing backlog looks like a
working system until memory runs out.
"""

from __future__ import annotations

import collections
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field

import numpy as np

from runtimes import load_runtime
from stations.config import Settings, Station
from transcribe import is_silent, spot_keywords

# How many recent chunks feed the rolling RTF shown per station. Ten chunks is
# under a minute at default settings — long enough to smooth one slow decode,
# short enough that a station going bad shows up while it matters.
RTF_WINDOW = 10


@dataclass
class StationHealth:
    """What the operator needs to trust, or distrust, a station's output."""

    station_id: str
    label: str
    session_id: str
    chunks_done: int = 0
    chunks_dropped: int = 0
    chunks_silent: int = 0
    keywords_found: int = 0
    last_chunk_at: float | None = None
    last_error: str | None = None
    _recent_rtf: collections.deque = field(default_factory=lambda: collections.deque(maxlen=RTF_WINDOW))

    @property
    def mean_rtf(self) -> float | None:
        return sum(self._recent_rtf) / len(self._recent_rtf) if self._recent_rtf else None

    @property
    def keeping_up(self) -> bool | None:
        """RTF < 1 means the model transcribed the chunk faster than realtime."""
        rtf = self.mean_rtf
        return None if rtf is None else rtf < 1.0

    def as_dict(self) -> dict:
        return {
            "station_id": self.station_id,
            "label": self.label,
            "session_id": self.session_id,
            "chunks_done": self.chunks_done,
            "chunks_dropped": self.chunks_dropped,
            "chunks_silent": self.chunks_silent,
            "keywords_found": self.keywords_found,
            "last_chunk_at": self.last_chunk_at,
            "last_error": self.last_error,
            "mean_rtf": round(self.mean_rtf, 3) if self.mean_rtf is not None else None,
            "keeping_up": self.keeping_up,
        }


class ChunkQueue:
    """Bounded work queue that drops the oldest chunk instead of blocking."""

    def __init__(self, maxsize: int) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self.dropped: dict[str, int] = collections.defaultdict(int)

    def put(self, item) -> str | None:
        """Enqueue, evicting the oldest if full. Returns the evicted station id."""
        # Locked so two capture threads hitting a full queue can't both evict
        # and leave it under-full, or interleave into an over-full state.
        with self._lock:
            try:
                self._queue.put_nowait(item)
                return None
            except queue.Full:
                try:
                    stale_station, _, _ = self._queue.get_nowait()
                except queue.Empty:  # drained between the two calls
                    stale_station = None
                if stale_station is not None:
                    self.dropped[stale_station.id] += 1
                try:
                    self._queue.put_nowait(item)
                except queue.Full:  # pragma: no cover - another producer refilled it
                    self.dropped[item[0].id] += 1
                    return item[0].id
                return stale_station.id if stale_station else None

    def get(self, timeout: float):
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def depth(self) -> int:
        return self._queue.qsize()


class Engine:
    """Shared inference across every station."""

    def __init__(self, settings: Settings, on_event=None) -> None:
        self.settings = settings
        self._on_event = on_event
        self.queue = ChunkQueue(settings.queue_size)
        self.health: dict[str, StationHealth] = {}
        self._last_alerted: dict[str, dict[str, float]] = {}
        self._runtime = None
        self._runtime_lock = threading.Lock()
        self._stop = threading.Event()
        self._workers: list[threading.Thread] = []

    # -- lifecycle ---------------------------------------------------------

    def load(self):
        """Load the shared model. Slow, and worth doing before any mic opens."""
        with self._runtime_lock:
            if self._runtime is None:
                self._runtime = load_runtime(self.settings.runtime, self.settings.model)
            return self._runtime

    def register(self, station: Station) -> StationHealth:
        health = StationHealth(
            station_id=station.id, label=station.label, session_id=str(uuid.uuid4())
        )
        self.health[station.id] = health
        self._last_alerted[station.id] = {}
        return health

    def start(self) -> None:
        self._stop.clear()
        for n in range(self.settings.workers):
            worker = threading.Thread(target=self._run, name=f"infer-{n}", daemon=True)
            worker.start()
            self._workers.append(worker)

    def stop(self, timeout: float = 30.0) -> None:
        self._stop.set()
        for worker in self._workers:
            worker.join(timeout=timeout)
        self._workers.clear()

    # -- the hot path ------------------------------------------------------

    def submit(self, station: Station, chunk: np.ndarray, chunk_time: float) -> None:
        """Called by a capture thread. Never blocks for long."""
        evicted = self.queue.put((station, chunk, chunk_time))
        if evicted is not None:
            health = self.health.get(evicted)
            if health:
                health.chunks_dropped = self.queue.dropped[evicted]
            self._emit(
                {
                    "type": "drop",
                    "station_id": evicted,
                    "label": health.label if health else evicted,
                    "dropped_total": self.queue.dropped[evicted],
                    "queue_depth": self.queue.depth,
                }
            )

    def _run(self) -> None:
        runtime = self.load()
        while not self._stop.is_set():
            item = self.queue.get(timeout=0.2)
            if item is None:
                continue
            station, chunk, chunk_time = item
            try:
                self._process(runtime, station, chunk, chunk_time)
            except Exception as exc:  # noqa: BLE001 - one bad chunk must not end the worker
                health = self.health.get(station.id)
                if health:
                    health.last_error = str(exc)
                self._emit(
                    {
                        "type": "error",
                        "station_id": station.id,
                        "label": station.label,
                        "message": str(exc),
                    }
                )

    def _process(self, runtime, station: Station, chunk: np.ndarray, chunk_time: float) -> None:
        health = self.health.get(station.id) or self.register(station)

        if is_silent(chunk, station.silence_threshold):
            health.chunks_silent += 1
            health.last_chunk_at = time.time()
            self._emit(
                {
                    "type": "chunk",
                    "station_id": station.id,
                    "label": station.label,
                    "time": round(chunk_time, 1),
                    "text": None,
                    "silent": True,
                }
            )
            return

        started = time.perf_counter()
        text = runtime.transcribe(chunk, station.language)
        latency = time.perf_counter() - started
        rtf = latency / station.chunking.chunk_seconds

        health.chunks_done += 1
        health.last_chunk_at = time.time()
        health._recent_rtf.append(rtf)

        self._emit(
            {
                "type": "chunk",
                "station_id": station.id,
                "label": station.label,
                "time": round(chunk_time, 1),
                "text": text,
                "silent": False,
                "latency": round(latency, 2),
                "rtf": round(rtf, 2),
                "queue_depth": self.queue.depth,
            }
        )

        if station.keywords:
            # The PoC's spotter, reused so detection and debouncing behave
            # identically to everything already benchmarked — it just carries
            # the station through to the stored event now.
            spot_keywords(
                text,
                station.keywords,
                self._last_alerted.setdefault(station.id, {}),
                chunk_time,
                model_key=self.settings.model,
                session_id=health.session_id,
                backend_url=self.settings.backend_url,
                on_event=self._keyword_event(station, health),
                debounce_seconds=station.chunking.debounce_seconds,
                station=station.label,
            )

    def _keyword_event(self, station: Station, health: StationHealth):
        def emit(event: dict) -> None:
            health.keywords_found += 1
            self._emit({**event, "station_id": station.id, "label": station.label})

        return emit

    def _emit(self, event: dict) -> None:
        if self._on_event:
            self._on_event(event)
