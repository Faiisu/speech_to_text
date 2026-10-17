"""Bounded, process-local microphone signal diagnostic lifecycle."""

from __future__ import annotations

import math
import threading
import time
import uuid

import numpy as np

from speech_to_text.features.model_deployment import (
    AudioInputDeviceError,
    AudioInputError,
    create_microphone_source,
)


class AudioInputTestConflictError(RuntimeError):
    """Another diagnostic microphone session is active."""


class AudioInputTestNotFoundError(LookupError):
    """The requested diagnostic session is not the retained session."""


class AudioInputTestUnavailableError(RuntimeError):
    """The microphone source could not be opened or kept running."""


class _TestRecord:
    def __init__(self, test_id, requested_device):
        self.test_id = test_id
        self.status = "starting"
        self.device = requested_device
        self.sample_rate = None
        self.channels = None
        self.frames_received = 0
        self.rms = None
        self.peak = None
        self.error = None
        self.started_at = time.monotonic()
        self.stopped_at = None
        self.source = None
        self.timer = None
        self.lock = threading.RLock()
        self.closed_event = threading.Event()


class AudioInputTestService:
    """Own at most one source and retain only its latest diagnostic snapshot.

    ``source_factory`` is an injectable hardware boundary with the same
    signature as Feature 01's ``create_microphone_source``. The default session
    limit is 30 seconds.
    """

    def __init__(self, *, source_factory=None, max_seconds=30):
        if (
            isinstance(max_seconds, bool)
            or not isinstance(max_seconds, (int, float))
            or not math.isfinite(max_seconds)
            or max_seconds <= 0
            or max_seconds > 30
        ):
            raise ValueError("max_seconds must be finite and in (0, 30]")
        self.max_seconds = float(max_seconds)
        self._source_factory = (
            create_microphone_source if source_factory is None else source_factory
        )
        self._lock = threading.RLock()
        self._record = None
        self._active_id = None
        self._closed = False

    def start(self, device=None):
        if device is not None and (not isinstance(device, str) or not device.strip()):
            raise AudioInputDeviceError("device must be a non-empty string or null")
        with self._lock:
            if self._closed:
                raise AudioInputTestUnavailableError("Microphone test service is closed")
            if self._active_id is not None:
                raise AudioInputTestConflictError("A microphone test is already active")
            record = _TestRecord(uuid.uuid4().hex, device)
            self._record = record
            self._active_id = record.test_id
            try:
                record.source = self._source_factory(
                    device=device,
                    on_audio=lambda frames: self._on_audio(record, frames),
                    on_error=lambda error: self._on_error(record, error),
                )
                record.device = getattr(record.source, "device_name", device)
                record.sample_rate = int(record.source.sample_rate)
                record.channels = int(record.source.channels)
                record.source.start()
                with record.lock:
                    record.status = "running"
                record.timer = threading.Timer(
                    self.max_seconds, self._auto_stop, args=(record,)
                )
                record.timer.daemon = True
                record.timer.start()
            except AudioInputDeviceError:
                self._discard_start(record)
                raise
            except AudioInputError as exc:
                self._discard_start(record)
                raise AudioInputTestUnavailableError(
                    "Microphone capture is unavailable. Check the input device and audio backend."
                ) from exc
            except Exception as exc:
                self._discard_start(record)
                raise AudioInputTestUnavailableError(
                    "Unable to start microphone capture. Check the input device and audio backend."
                ) from exc
            return self._snapshot(record)

    def get(self, test_id):
        with self._lock:
            record = self._record
            if record is None or record.test_id != test_id:
                raise AudioInputTestNotFoundError("Microphone test not found")
        return self._snapshot(record)

    def stop(self, test_id):
        with self._lock:
            record = self._record
            if record is None or record.test_id != test_id:
                raise AudioInputTestNotFoundError("Microphone test not found")
            if record.status in {"completed", "stopped", "failed"}:
                return self._snapshot(record)
            if record.status == "stopping":
                wait_for_close = True
                timer = None
            else:
                wait_for_close = False
                with record.lock:
                    record.status = "stopping"
                timer = record.timer
                record.timer = None
        if wait_for_close:
            record.closed_event.wait()
            return self._snapshot(record)
        if timer is not None:
            timer.cancel()
        try:
            record.source.stop()
        except Exception:
            with record.lock:
                record.error = "Microphone capture could not be closed cleanly."
            self._finish(record, "failed")
        else:
            self._finish(record, "stopped")
        return self._snapshot(record)

    def close(self):
        with self._lock:
            self._closed = True
            test_id = self._active_id
        if test_id is not None:
            self.stop(test_id)

    def _discard_start(self, record):
        source = record.source
        if source is not None:
            try:
                source.stop()
            except Exception:
                pass
        with self._lock:
            if self._active_id == record.test_id:
                self._active_id = None
            with record.lock:
                record.status = "failed"
                record.stopped_at = time.monotonic()
                record.closed_event.set()

    def _on_audio(self, record, frames):
        samples = np.asarray(frames, dtype=np.float32)
        if samples.ndim == 0:
            return
        frame_count = int(samples.shape[0])
        if frame_count <= 0:
            return
        with record.lock:
            if record.status not in {"starting", "running"}:
                return
            values = samples.reshape(-1)
            if values.size == 0:
                return
            record.frames_received += frame_count
            record.rms = float(np.sqrt(np.mean(np.square(values), dtype=np.float64)))
            record.peak = float(np.max(np.abs(values)))

    def _on_error(self, record, error):
        if getattr(error, "recoverable", False):
            return
        with self._lock:
            if self._record is not record or record.status not in {"starting", "running"}:
                return
            with record.lock:
                record.status = "stopping"
                record.error = "Microphone capture stopped because of an input error."
            timer = record.timer
            record.timer = None
        if timer is not None:
            timer.cancel()
        try:
            if record.source is not None:
                record.source.stop()
        except Exception:
            pass
        self._finish(record, "failed")

    def _auto_stop(self, record):
        with self._lock:
            if self._record is not record or record.status != "running":
                return
            with record.lock:
                record.status = "stopping"
            record.timer = None
        try:
            record.source.stop()
        except Exception:
            with record.lock:
                record.error = "Microphone capture could not be closed cleanly."
            self._finish(record, "failed")
        else:
            self._finish(record, "completed")

    def _finish(self, record, status):
        with self._lock:
            if self._record is not record:
                return
            with record.lock:
                record.status = status
                record.stopped_at = time.monotonic()
                record.closed_event.set()
            if self._active_id == record.test_id:
                self._active_id = None

    def _snapshot(self, record):
        with self._lock, record.lock:
            end = record.stopped_at or time.monotonic()
            elapsed = min(self.max_seconds, max(0.0, end - record.started_at))
            return {
                "test_id": record.test_id,
                "status": record.status,
                "device": record.device,
                "sample_rate": record.sample_rate,
                "channels": record.channels,
                "frames_received": record.frames_received,
                "rms": record.rms,
                "peak": record.peak,
                "elapsed_seconds": elapsed,
                "error": record.error,
            }
