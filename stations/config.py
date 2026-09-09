"""What the operator configures: the stations, and the settings they share.

Persisted as JSON rather than held in a database because this is deployment
configuration, not data — an operator editing stations.json by hand on the
UBX-330M and restarting the service is a supported way to work, and the file
is what the deploy script ships.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from transcribe import (
    DEFAULT_BACKEND_URL,
    DEFAULT_LANGUAGE,
    DEFAULT_SILENCE_RMS,
    LANGUAGES,
    Chunking,
)

CONFIG_FILE = Path(__file__).parent.parent / "stations.json"

# The product is pinned to these; the PoC panel is where other combinations get
# compared. Overridable in the file for the Mac (no Intel GPU) and for a
# capacity benchmark, but this is what a deploy uses.
DEFAULT_MODEL = "turbo"  # typhoon-ai/typhoon-whisper-turbo
DEFAULT_RUNTIME = "openvino-gpu"

# Chunks waiting for the shared model. Sized as roughly two chunks per station
# for three stations: enough to ride out one slow inference, small enough that
# a sustained overload is visible as drops within seconds rather than as
# minutes of latency nobody can see.
DEFAULT_QUEUE_SIZE = 6

_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class ConfigError(ValueError):
    """Bad configuration, phrased for whoever has to fix the file."""


@dataclass
class Station:
    """One microphone and the configuration that gives it meaning."""

    id: str
    label: str
    # Matched against the audio device *name*, not an index. PortAudio indices
    # shift when a USB mic is replugged or the machine reboots, which would
    # silently swap two stations' labels — the one failure that would corrupt
    # the record without anything looking wrong.
    device: str
    keywords: list[str] = field(default_factory=list)
    language: str = DEFAULT_LANGUAGE
    enabled: bool = True
    silence_threshold: float = DEFAULT_SILENCE_RMS
    chunk_seconds: float = 5.0
    overlap_seconds: float = 1.0

    def __post_init__(self) -> None:
        if not _SAFE_ID.match(self.id):
            raise ConfigError(
                f"Station id {self.id!r} must be lowercase letters, digits, - or _ "
                "(it appears in URLs and event rows)"
            )
        if not self.label.strip():
            raise ConfigError(f"Station {self.id!r} needs a label — it is what the UI shows")
        if not self.device.strip():
            raise ConfigError(f"Station {self.id!r} needs a device name to capture from")
        if self.language not in LANGUAGES:
            raise ConfigError(
                f"Station {self.id!r}: unknown language {self.language!r}; "
                f"expected one of {', '.join(LANGUAGES)}"
            )
        if not 0 <= self.silence_threshold < 1:
            raise ConfigError(
                f"Station {self.id!r}: silence_threshold must be in [0, 1); "
                f"got {self.silence_threshold}"
            )
        # Chunking validates its own pair (chunk > overlap >= 0, chunk <= 30s).
        self.chunking

    @property
    def chunking(self) -> Chunking:
        try:
            return Chunking(self.chunk_seconds, self.overlap_seconds)
        except ValueError as exc:
            raise ConfigError(f"Station {self.id!r}: {exc}") from None


@dataclass
class Settings:
    """Everything the stations share: one model, one runtime, one queue."""

    model: str = DEFAULT_MODEL
    runtime: str = DEFAULT_RUNTIME
    backend_url: str = DEFAULT_BACKEND_URL
    queue_size: int = DEFAULT_QUEUE_SIZE
    # One worker by design: a second thread on the same iGPU contends for the
    # same execution units rather than adding throughput. Configurable because
    # a CPU runtime with spare cores is the case where more than one helps.
    workers: int = 1
    stations: list[Station] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.queue_size < 1:
            raise ConfigError("queue_size must be at least 1")
        if self.workers < 1:
            raise ConfigError("workers must be at least 1")
        ids = [s.id for s in self.stations]
        duplicate_ids = {i for i in ids if ids.count(i) > 1}
        if duplicate_ids:
            raise ConfigError(f"Duplicate station id(s): {', '.join(sorted(duplicate_ids))}")
        # Labels are how an operator tells two stations apart in the UI and how
        # a detection row reads later. Two "Line 1"s make the record ambiguous.
        labels = [s.label.strip().lower() for s in self.stations]
        duplicate_labels = {label for label in labels if labels.count(label) > 1}
        if duplicate_labels:
            raise ConfigError(
                f"Duplicate station label(s): {', '.join(sorted(duplicate_labels))} — "
                "labels identify a station in the UI and in stored events"
            )

    def station(self, station_id: str) -> Station | None:
        return next((s for s in self.stations if s.id == station_id), None)


def _from_dict(data: dict) -> Settings:
    if not isinstance(data, dict):
        raise ConfigError("stations.json must contain a JSON object")
    raw_stations = data.get("stations", [])
    if not isinstance(raw_stations, list):
        raise ConfigError("stations.json: 'stations' must be a list")
    known = {f for f in Station.__dataclass_fields__}
    stations = []
    for entry in raw_stations:
        if not isinstance(entry, dict):
            raise ConfigError("stations.json: each station must be an object")
        unknown = set(entry) - known
        if unknown:
            # A typo'd key silently taking a default is how a station ends up
            # listening to the wrong mic with nothing in the logs.
            raise ConfigError(
                f"Station {entry.get('id', '?')!r}: unknown field(s) "
                f"{', '.join(sorted(unknown))}"
            )
        stations.append(Station(**entry))
    settings_fields = {f for f in Settings.__dataclass_fields__} - {"stations"}
    extra = {k: v for k, v in data.items() if k in settings_fields}
    return Settings(stations=stations, **extra)


_write_lock = threading.Lock()


def load(path: Path = CONFIG_FILE) -> Settings:
    """Read the configuration, or return the empty default if there is none."""
    if not path.exists():
        return Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        # Unlike the PoC's models.local.json, a broken file here is not
        # survivable by falling back to a default: silently running zero
        # stations looks identical to running fine.
        raise ConfigError(f"{path} is not valid JSON: {exc}") from None
    return _from_dict(data)


def save(settings: Settings, path: Path = CONFIG_FILE) -> None:
    """Write the configuration atomically, so a crash can't truncate it."""
    payload = asdict(settings)
    with _write_lock:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
