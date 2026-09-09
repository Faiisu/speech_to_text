"""Per-station audio capture with constant memory.

The PoC's run_recording_session keeps every sample of a session
(transcribe.py) and rebuilds the whole buffer on each chunk. That is fine for
a recording that lasts minutes and ends with a full-clip reference transcript.
A station runs for weeks and never wants the full recording, so this keeps
only the audio still needed to cut the next chunk: memory is a function of the
chunk length, not of uptime.
"""

from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd

from stations.config import Station
from transcribe import SAMPLE_RATE


class DeviceError(RuntimeError):
    """A configured microphone isn't there, or its name is ambiguous."""


def input_devices() -> list[dict]:
    """Every device that can capture, as {index, name, channels}."""
    return [
        {"index": i, "name": d["name"], "channels": d["max_input_channels"]}
        for i, d in enumerate(sd.query_devices())
        if d["max_input_channels"] > 0
    ]


def resolve_device(name: str) -> int:
    """Find the input device index for a configured device name.

    Exact match wins; otherwise a case-insensitive substring match, which must
    be unambiguous. An ambiguous name is an error rather than a first-match
    guess: picking the wrong one of two identical USB mics would label every
    detection with the wrong station, and nothing downstream could tell.
    """
    devices = input_devices()
    if not devices:
        raise DeviceError("No audio input devices at all — is a microphone connected?")

    exact = [d for d in devices if d["name"] == name]
    if len(exact) == 1:
        return exact[0]["index"]

    matches = [d for d in devices if name.lower() in d["name"].lower()]
    if len(matches) == 1:
        return matches[0]["index"]
    if not matches:
        available = ", ".join(repr(d["name"]) for d in devices)
        raise DeviceError(f"No input device matching {name!r}. Available: {available}")
    ambiguous = ", ".join(repr(d["name"]) for d in matches)
    raise DeviceError(
        f"Device name {name!r} matches {len(matches)} inputs ({ambiguous}). "
        "Use the full device name so the station can't bind to the wrong microphone."
    )


def measure_noise(device: str, seconds: float = 5.0) -> dict:
    """Record a short sample and report how loud this microphone's room is.

    The silence gate compares a whole chunk's mean RMS against a threshold, so
    the useful question is "what does this microphone read when nobody is
    talking" — that is the number the threshold has to sit above. Per-frame
    percentiles come back too, because a steady hum and an occasional door
    slam need different thresholds and one overall average hides which you
    have.
    """
    index = resolve_device(device)
    frames = int(seconds * SAMPLE_RATE)
    recording = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1,
                       dtype="float32", device=index)
    sd.wait()
    audio = recording.reshape(-1)

    def dbfs(value: float) -> float | None:
        return round(float(20 * np.log10(value)), 1) if value > 0 else None

    overall = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
    # 100ms frames: short enough to separate a hum from a bang, long enough
    # that one sample of noise doesn't dominate.
    frame = SAMPLE_RATE // 10
    usable = audio[: audio.size - audio.size % frame] if audio.size >= frame else audio
    if usable.size >= frame:
        blocks = usable.reshape(-1, frame)
        levels = np.sqrt(np.mean(np.square(blocks), axis=1))
    else:
        levels = np.array([overall], dtype="float32")

    median = float(np.median(levels))
    p90 = float(np.percentile(levels, 90))
    loudest = float(levels.max())

    # Three times the measured floor is about +10dB of headroom: high enough
    # that room noise doesn't wake the model, low enough that quiet speech
    # still gets through.
    suggested = round(max(overall, median) * 3, 4)

    return {
        "device": device,
        "seconds": seconds,
        "rms": round(overall, 5),
        "dbfs": dbfs(overall),
        "median_rms": round(median, 5),
        "median_dbfs": dbfs(median),
        "p90_dbfs": dbfs(p90),
        "loudest_dbfs": dbfs(loudest),
        "peak_dbfs": dbfs(float(np.abs(audio).max()) if audio.size else 0.0),
        "suggested_threshold": suggested,
        "suggested_dbfs": dbfs(suggested),
        # A floor this high is not a threshold problem, and saying so beats
        # letting the operator raise the gate until real speech is dropped too.
        "noisy": overall > 0.05,
    }


class _RingBuffer:
    """Holds just enough recent audio to cut the next chunk.

    Blocks arrive from the PortAudio callback and are dropped from the front as
    soon as they're past the current chunk's start. Total retained samples stay
    below one chunk plus one in-flight block, whatever the uptime.
    """

    def __init__(self, chunk_samples: int) -> None:
        self._blocks: deque[np.ndarray] = deque()
        self._samples = 0  # retained samples across all blocks, minus _offset
        self._offset = 0  # consumed samples at the head of the first block
        self._chunk_samples = chunk_samples
        self._lock = threading.Lock()

    @property
    def available(self) -> int:
        with self._lock:
            return self._samples

    def add(self, block: np.ndarray) -> None:
        with self._lock:
            self._blocks.append(block)
            self._samples += block.size

    def take_chunk(self) -> np.ndarray | None:
        """Return the next full chunk without consuming it, or None."""
        with self._lock:
            if self._samples < self._chunk_samples:
                return None
            out = np.empty(self._chunk_samples, dtype="float32")
            filled = 0
            offset = self._offset
            for block in self._blocks:
                take = min(block.size - offset, self._chunk_samples - filled)
                out[filled : filled + take] = block[offset : offset + take]
                filled += take
                offset = 0
                if filled == self._chunk_samples:
                    break
            return out

    def advance(self, samples: int) -> None:
        """Discard `samples` from the front — this is what bounds memory."""
        with self._lock:
            self._samples -= samples
            remaining = samples
            while remaining and self._blocks:
                head = self._blocks[0]
                in_head = head.size - self._offset
                if in_head > remaining:
                    self._offset += remaining
                    remaining = 0
                else:
                    self._blocks.popleft()
                    self._offset = 0
                    remaining -= in_head


class StationCapture:
    """Captures one station's audio and emits chunks to a sink.

    The sink is called with (station, chunk, chunk_time) and is expected not to
    block for long — it hands off to the shared inference queue, which applies
    its own backpressure policy.
    """

    def __init__(self, station: Station, sink, on_error=None) -> None:
        self.station = station
        self._sink = sink
        self._on_error = on_error
        self._chunking = station.chunking
        self._buffer = _RingBuffer(self._chunking.chunk_samples)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._device_index: int | None = None
        self.started_at: float | None = None
        self.stream_errors = 0

    # PortAudio calls this on its own high-priority thread: do as little as
    # possible here. Anything slow risks an input overflow and lost audio.
    def _callback(self, indata, frame_count, time_info, status) -> None:
        if status:
            self.stream_errors += 1
        self._buffer.add(indata.copy().reshape(-1))

    def _run(self) -> None:
        step_samples = self._chunking.step_samples
        elapsed_samples = 0
        try:
            self._device_index = resolve_device(self.station.device)
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                callback=self._callback,
                device=self._device_index,
            ):
                self.started_at = time.time()
                while not self._stop.is_set():
                    chunk = self._buffer.take_chunk()
                    if chunk is None:
                        # Sleep well under one step so a chunk is picked up
                        # promptly, without spinning a core per station.
                        self._stop.wait(0.05)
                        continue
                    self._sink(self.station, chunk, elapsed_samples / SAMPLE_RATE)
                    self._buffer.advance(step_samples)
                    elapsed_samples += step_samples
        except Exception as exc:  # noqa: BLE001 - one station's failure is not the service's
            if self._on_error:
                self._on_error(self.station, exc)

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"capture-{self.station.id}", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def buffered_seconds(self) -> float:
        return self._buffer.available / SAMPLE_RATE
