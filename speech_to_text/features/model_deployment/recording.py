"""Bounded host microphone capture saved as a PCM16 mono WAV."""

from __future__ import annotations

import math
import threading
from pathlib import Path
from queue import Empty, Full, Queue
import wave

import numpy as np

from .capture import AudioInputOverflow, SoundDeviceSource
from .errors import AudioInputError


class AudioRecording:
    def __init__(self, device, output_path, max_seconds, audio_source_factory=None):
        if (
            isinstance(max_seconds, bool)
            or not isinstance(max_seconds, (int, float))
            or not math.isfinite(max_seconds)
            or max_seconds <= 0
        ):
            raise ValueError("max_seconds must be finite and positive")
        self.output_path = Path(output_path)
        self.max_seconds = float(max_seconds)
        self.status = "recording"
        self.error = None
        self.warnings = []
        self.duration_seconds = 0.0
        self._lock = threading.RLock()
        self._stop_lock = threading.Lock()
        self._queue = Queue(maxsize=64)
        self._stop_requested = threading.Event()
        self._ready = threading.Event()
        self._done = threading.Event()
        self._accepting = True
        self._writer_error = None
        self._frames_written = 0
        self._source = None
        factory = audio_source_factory or SoundDeviceSource
        try:
            self._source = factory(device=device, on_audio=self._on_audio, on_error=self._on_error)
            self.sample_rate = self._source.sample_rate
            self.channels = self._source.channels
            if (
                isinstance(self.sample_rate, bool)
                or not isinstance(self.sample_rate, int)
                or not 8000 <= self.sample_rate <= 48000
                or self.channels not in (1, 2)
            ):
                raise AudioInputError("Recording source must provide 8–48 kHz mono or stereo audio")
            self.device = getattr(self._source, "device_name", device)
            self._max_frames = int(self.sample_rate * self.max_seconds)
            self._writer = threading.Thread(target=self._write_audio, name="audio-recording-writer", daemon=True)
            self._writer.start()
            self._ready.wait()
            if self._writer_error:
                raise self._writer_error
            self._source.start()
            self._watchdog = threading.Thread(target=self._watch_duration, name="audio-recording-limit", daemon=True)
            self._watchdog.start()
        except Exception:
            self._accepting = False
            try:
                if self._source is not None:
                    self._source.stop()
            except Exception:
                pass
            self._stop_requested.set()
            if hasattr(self, "_writer"):
                self._writer.join(timeout=10)
            self.status = "failed"
            raise

    def _on_audio(self, frames):
        if not self._accepting:
            return
        try:
            values = np.asarray(frames, dtype=np.float32)
            if values.ndim == 2 and values.shape[1] == self.channels:
                values = values.mean(axis=1)
            if values.ndim != 1 or not np.isfinite(values).all():
                raise AudioInputError("Captured microphone frames are invalid")
            self._queue.put_nowait(values.copy())
        except Full:
            self._on_error(AudioInputError("Recording buffer overflow; capture stopped"))
        except Exception as exc:
            self._on_error(exc)

    def _on_error(self, error):
        with self._lock:
            if isinstance(error, AudioInputOverflow):
                self.warnings.append(str(error))
                del self.warnings[:-20]
                return
            if self.status != "recording" or self.error is not None:
                return
            self.error = str(error).strip() or type(error).__name__
            self._accepting = False
        threading.Thread(target=self._stop_source, name="audio-recording-error-stop", daemon=True).start()

    def _stop_source(self):
        with self._stop_lock:
            try:
                self._source.stop()
            except Exception as exc:
                with self._lock:
                    self.error = self.error or (str(exc).strip() or type(exc).__name__)
            self._stop_requested.set()

    def _watch_duration(self):
        if not self._stop_requested.wait(self.max_seconds):
            self.stop()

    def _write_audio(self):
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(self.output_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(self.sample_rate)
                self._ready.set()
                while True:
                    if self._stop_requested.is_set() and self._queue.empty():
                        break
                    try:
                        samples = self._queue.get(timeout=0.1)
                    except Empty:
                        continue
                    remaining = self._max_frames - self._frames_written
                    samples = samples[:remaining]
                    if samples.size:
                        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
                        wav.writeframesraw(pcm.tobytes())
                        self._frames_written += int(samples.size)
                        self.duration_seconds = self._frames_written / self.sample_rate
                    if self._frames_written >= self._max_frames:
                        self._accepting = False
                        self._stop_source()
            with self._lock:
                self.status = "failed" if self.error or self._writer_error else "completed"
        except Exception as exc:
            self._writer_error = exc
            with self._lock:
                self._accepting = False
                self.status = "failed"
                self.error = self.error or (str(exc).strip() or type(exc).__name__)
            self._ready.set()
            self._stop_source()
        finally:
            self._done.set()

    def stop(self):
        with self._stop_lock:
            if self.status == "recording":
                self._accepting = False
                try:
                    self._source.stop()
                except Exception as exc:
                    with self._lock:
                        self.error = self.error or (str(exc).strip() or type(exc).__name__)
                self._stop_requested.set()
        self._done.wait(timeout=10)
        if not self._done.is_set():
            with self._lock:
                self.status = "failed"
                self.error = self.error or "Recording writer did not finish"
        return self.snapshot()

    def wait(self):
        """Wait for automatic completion or failure and return the final snapshot."""
        self._done.wait()
        return self.snapshot()

    def snapshot(self):
        with self._lock:
            return {
                "status": self.status,
                "duration_seconds": self.duration_seconds,
                "device": self.device,
                "error": self.error,
                "warnings": list(self.warnings),
            }


def start_audio_recording(device=None, output_path=None, *, max_seconds=120, audio_source_factory=None):
    """Start bounded microphone capture without loading a model."""
    if output_path is None:
        raise ValueError("output_path is required")
    return AudioRecording(device, output_path, max_seconds, audio_source_factory)
