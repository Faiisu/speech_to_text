"""Per-source microphone buffering, inference routing, events, and shutdown."""

from __future__ import annotations

import math
import os
import threading
import time
from datetime import datetime, timezone
import uuid
from collections import deque
from queue import Empty, Full, Queue

import numpy as np

from .audio import TARGET_RATE, StreamingResampler
from .capture import SoundDeviceSource
from .config import flow_config
from .errors import AudioInputError, ConfigurationError
from .measurements import publish_inference_measurement


def _event(kind, source_id, **fields):
    return {"type": kind, "source_id": source_id, **fields}


class TranscriptionSession:
    def __init__(self, device, model_handle, settings, audio_source_factory=None):
        self.model_handle = model_handle
        self.settings = flow_config(settings)
        if (
            self.settings["language"] != "auto"
            and self.settings["language"] not in model_handle.languages
        ):
            raise ConfigurationError(
                f"Language {self.settings['language']!r} is not supported by model {model_handle.model!r}"
            )
        self.source_id = self.settings.get("source_id") or uuid.uuid4().hex
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise ConfigurationError("source_id must be a non-empty string")
        self.settings["source_id"] = self.source_id
        if any(
            session.source_id == self.source_id and not session._terminal
            for session in model_handle._sessions
        ):
            raise ConfigurationError(
                f"source_id {self.source_id!r} is already active for this model handle"
            )
        self.result_queue = Queue()
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._active = True
        self._stopping = False
        self._terminal = False
        self._fatal_error = False
        self._shutdown_chunks = None
        self._shutdown_next = 0
        self._shutdown_end_enqueued = False
        self._last_sequence = -1
        self._first_queued_at = None
        self._pending = 0
        self._capture_done = False
        self._buffer = np.empty(0, dtype=np.float32)
        self._chunk_frames = max(
            1, int(round(self.settings["chunk_seconds"] * TARGET_RATE))
        )
        self._resampler = None
        factory = audio_source_factory or SoundDeviceSource
        try:
            self.source = factory(
                device=device, on_audio=self._on_audio, on_error=self._on_error
            )
            if (
                isinstance(self.source.sample_rate, bool)
                or not isinstance(self.source.sample_rate, int)
                or not 8000 <= self.source.sample_rate <= 48000
            ):
                raise AudioInputError(
                    "Microphone source sample_rate must be an integer in [8000, 48000]"
                )
            if (
                isinstance(self.source.channels, bool)
                or not isinstance(self.source.channels, int)
                or self.source.channels not in (1, 2)
            ):
                raise AudioInputError("Microphone source channels must be 1 or 2")
            self._resampler = StreamingResampler(self.source.sample_rate)
            self.source.start()
        except Exception:
            self._active = False
            try:
                source = getattr(self, "source", None)
                if source:
                    source.stop()
            finally:
                model_handle._sessions.discard(self)
            raise

    def _on_audio(self, frames):
        if not self._active or self._stopping:
            return
        try:
            frames_array = np.asarray(frames)
            if self.source.channels == 1 and frames_array.ndim not in (1, 2):
                raise AudioInputError(
                    "Captured microphone frames have an invalid channel layout"
                )
            if frames_array.ndim == 2 and frames_array.shape[1] != self.source.channels:
                raise AudioInputError(
                    "Captured microphone channel count does not match source.channels"
                )
            pcm = self._resampler.feed(frames)
            if not pcm.size:
                return
            with self._lock:
                if not self._active or self._stopping:
                    return
                combined = np.concatenate((self._buffer, pcm))
                pos = 0
                while combined.size - pos >= self._chunk_frames:
                    chunk = combined[pos : pos + self._chunk_frames].copy()
                    pos += self._chunk_frames
                    if not self._enqueue_chunk(chunk):
                        return
                self._buffer = combined[pos:].copy()
        except Exception as exc:
            self._schedule_fatal("CAPTURE_FAILED", str(exc))

    def _on_error(self, error):
        self._schedule_fatal("CAPTURE_FAILED", str(error))

    def _schedule_fatal(self, code, message):
        threading.Thread(
            target=self._fatal,
            args=(code, message),
            name=f"stt-fatal-{self.source_id}",
            daemon=True,
        ).start()

    def _fatal(self, code, message):
        with self._condition:
            if not self._active or self._terminal:
                return
            self._active = False
            self._fatal_error = True
            self.result_queue.put(
                _event(
                    "error",
                    self.source_id,
                    code=code,
                    message=str(message),
                    sequence=None,
                    fatal=True,
                )
            )
        try:
            self.source.stop()
        except Exception:
            pass
        self._purge_model_queue()
        self._emit_completed("failed")

    def _purge_model_queue(self):
        for q in (self.model_handle._ingress_queue, self.model_handle._work_queue):
            with q.mutex:
                if q is self.model_handle._ingress_queue:
                    retained = deque(
                        item for item in q.queue if len(item) < 2 or item[1] is not self
                    )
                    removed = len(q.queue) - len(retained)
                else:
                    retained = deque(item for item in q.queue if item[0] is not self)
                    removed = len(q.queue) - len(retained)
                q.queue.clear()
                q.queue.extend(retained)
                q.unfinished_tasks = max(0, q.unfinished_tasks - removed)
                q.not_full.notify_all()
                if removed and q is self.model_handle._work_queue:
                    with self._condition:
                        self._pending = max(0, self._pending - removed)
                        self._condition.notify_all()

    def _complete_work(self, sequence, transcript=None, error=None):
        with self._condition:
            active = self._active and not self._terminal
            if active and error is not None:
                self.result_queue.put(
                    _event(
                        "error",
                        self.source_id,
                        code="CHUNK_INFERENCE_FAILED",
                        message=str(error),
                        sequence=sequence,
                        fatal=False,
                    )
                )
            elif active and transcript:
                self.result_queue.put(
                    _event(
                        "transcript", self.source_id, sequence=sequence, text=transcript
                    )
                )
            self._pending = max(0, self._pending - 1)
            self._condition.notify_all()
            should_complete = (
                self._capture_done and self._pending == 0 and not self._terminal
            )
        if should_complete:
            with self._condition:
                self._active = False
            self._emit_completed("stopped")

    def _capture_finished(self):
        with self._condition:
            self._capture_done = True
            should_complete = self._pending == 0 and not self._terminal
        if should_complete:
            with self._condition:
                self._active = False
            self._emit_completed("stopped")

    def _emit_completed(self, status):
        with self._condition:
            if self._terminal:
                return
            self._terminal = True
            self.result_queue.put(
                _event(
                    "completed",
                    self.source_id,
                    status=status,
                    last_sequence=self._last_sequence
                    if self._last_sequence >= 0
                    else None,
                )
            )
            self._condition.notify_all()
        self.model_handle._sessions.discard(self)

    def stop(self, *, timeout=None):
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout < 0
        ):
            raise ValueError("timeout must be a non-negative number or None")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            if self._terminal:
                return
            is_stopping = self._stopping
            if not is_stopping and not self._active:
                while not self._terminal:
                    remaining = (
                        None if deadline is None else deadline - time.monotonic()
                    )
                    if remaining is not None and remaining <= 0:
                        raise TimeoutError(
                            "Microphone session did not shut down before timeout"
                        )
                    self._condition.wait(remaining)
                return
        if not is_stopping:
            self.model_handle._check()
            stop_error = []

            def stop_source():
                try:
                    self.source.stop()
                except Exception as exc:
                    stop_error.append(exc)

            stopper = threading.Thread(
                target=stop_source, name=f"stt-stop-{self.source_id}", daemon=True
            )
            stopper.start()
            remaining = (
                None if deadline is None else max(0, deadline - time.monotonic())
            )
            stopper.join(remaining)
            if stopper.is_alive():
                raise TimeoutError("Microphone capture did not stop before timeout")
            if stop_error:
                self._fatal("CAPTURE_FAILED", str(stop_error[0]))
                return
            with self._condition:
                self._stopping = True
                tail = self._resampler.flush()
                combined = np.concatenate((self._buffer, tail))
                self._buffer = np.empty(0, dtype=np.float32)
                self._shutdown_chunks = []
                for start in range(0, combined.size, self._chunk_frames):
                    self._shutdown_chunks.append(
                        combined[start : start + self._chunk_frames].copy()
                    )
        self._enqueue_shutdown(deadline)
        with self._condition:
            while not self._terminal:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError(
                        "Accepted microphone chunks did not finish before timeout"
                    )
                self._condition.wait(remaining)

    def _enqueue_shutdown(self, deadline):
        try:
            while self._shutdown_next < len(self._shutdown_chunks):
                remaining = (
                    None if deadline is None else max(0, deadline - time.monotonic())
                )
                self._enqueue_chunk(
                    self._shutdown_chunks[self._shutdown_next], timeout=remaining
                )
                self._shutdown_next += 1
            if not self._shutdown_end_enqueued:
                remaining = (
                    None if deadline is None else max(0, deadline - time.monotonic())
                )
                self.model_handle._ingress_queue.put(("end", self), timeout=remaining)
                self._shutdown_end_enqueued = True
        except Full as exc:
            raise TimeoutError(
                "Model input dispatcher did not accept captured audio before timeout"
            ) from exc

    def _enqueue_chunk(self, chunk, timeout=0):
        if (
            not chunk.size
            or float(np.sqrt(np.mean(chunk * chunk)))
            < self.settings["silence_threshold"]
        ):
            return True
        with self.model_handle._ingress_lock:
            with self._condition:
                if not self._active:
                    return False
                sequence = self._last_sequence + 1
                self._last_sequence = sequence
            try:
                self.model_handle._ingress_queue.put(
                    ("chunk", self, sequence, chunk.copy()),
                    timeout=timeout,
                )
            except Full:
                if timeout > 0:
                    raise
                self._schedule_fatal(
                    "INPUT_QUEUE_TIMEOUT", "Shared audio ingress queue is full"
                )
                return False
        return True


def model_worker(handle):
    while True:
        try:
            session, sequence, audio, queued_at = handle._work_queue.get(timeout=0.1)
        except Empty:
            if handle.state != "ready":
                return
            continue
        try:
            if session._active:
                inference_error = None
                transcript = None
                with handle._inference_lock:
                    handle._check()
                    inference_started = time.perf_counter()
                    queue_wait = max(0.0, inference_started - queued_at)
                    try:
                        transcript = handle._transcribe(audio, session.settings)
                    except Exception as exc:
                        inference_error = exc
                    inference_elapsed = max(
                        0.0, time.perf_counter() - inference_started
                    )
                if inference_error is None:
                    transcript = str(transcript).strip()
                measurement_sink = getattr(handle, "_measurement_sink", None)
                record = publish_inference_measurement(
                    measurement_sink,
                    operation=getattr(
                        handle, "_measurement_operation", "microphone-session"
                    ),
                    source_id=session.source_id,
                    sequence=sequence,
                    audio_seconds=len(audio) / TARGET_RATE,
                    inference_seconds=inference_elapsed,
                    queue_wait_seconds=queue_wait,
                    status="failed" if inference_error else "completed",
                    error=inference_error,
                )
                if session._active:
                    session.result_queue.put(record)
                sink = getattr(handle, "_telemetry_sink", None)
                if sink is not None:
                    duration = len(audio) / TARGET_RATE
                    sink.put(
                        {
                            "source_id": session.source_id,
                            "sequence": sequence,
                            "queue_wait_seconds": queue_wait,
                            "elapsed_seconds": queue_wait + inference_elapsed,
                            "inference_seconds": inference_elapsed,
                            "chunk_duration_seconds": duration,
                            "rtf": (queue_wait + inference_elapsed)
                            / max(duration, 1e-9),
                            "pid": os.getpid(),
                        }
                    )
                session._complete_work(
                    sequence,
                    transcript=transcript if transcript else None,
                    error=inference_error,
                )
            else:
                session._complete_work(sequence)
        finally:
            handle._work_queue.task_done()


def ingress_worker(handle):
    """Preserve callback acceptance order before bounded inference scheduling."""
    while True:
        try:
            item = handle._ingress_queue.get(timeout=0.1)
        except Empty:
            if handle.state != "ready":
                return
            continue
        try:
            if item[0] == "end":
                item[1]._capture_finished()
                continue
            _, session, sequence, audio = item
            with session._condition:
                if not session._active:
                    continue
                session._pending += 1
            queued_at = time.perf_counter()
            try:
                handle._work_queue.put(
                    (session, sequence, audio, queued_at),
                    timeout=handle.config["enqueue_timeout_seconds"],
                )
                if session._first_queued_at is None:
                    session._first_queued_at = (
                        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    )
                    session.result_queue.put(
                        {
                            "type": "audio_queued",
                            "source_id": session.source_id,
                            "first_queued_at": session._first_queued_at,
                        }
                    )
            except Full:
                with session._condition:
                    session._pending = max(0, session._pending - 1)
                    session._condition.notify_all()
                if session._active:
                    session._fatal(
                        "INPUT_QUEUE_TIMEOUT",
                        f"Shared inference queue remained full for {handle.config['enqueue_timeout_seconds']} seconds",
                    )
        finally:
            handle._ingress_queue.task_done()
