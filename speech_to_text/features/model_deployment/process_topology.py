"""Selectable microphone process topologies using explicit queue IPC."""

from __future__ import annotations

import math
import multiprocessing as mp
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from collections import deque
from copy import deepcopy
from multiprocessing.managers import SyncManager
from queue import Empty, Full, Queue

import numpy as np

from .audio import TARGET_RATE, StreamingResampler
from .catalog import list_models
from .config import (
    flow_config as validate_flow_config,
)
from .config import (
    model_config as validate_model_config,
)
from .errors import AudioInputError, ConfigurationError, ModelLoadError
from .measurements import publish_inference_measurement


class _BoundedFIFO:
    """Bounded process queue with atomic source-specific removal."""

    def __init__(self, capacity):
        import threading

        self.capacity = capacity
        self.items = deque()
        self.model_events = deque()
        self.retired = set()
        self.condition = threading.Condition()

    def put(self, item, timeout=None):
        from queue import Full

        deadline = None if timeout is None else time.monotonic() + timeout
        with self.condition:
            while len(self.items) >= self.capacity:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise Full
                self.condition.wait(remaining)
            self.items.append(item)
            self.condition.notify_all()

    def get(self, timeout=None):
        from queue import Empty

        deadline = None if timeout is None else time.monotonic() + timeout
        with self.condition:
            while not self.items:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise Empty
                self.condition.wait(remaining)
            item = self.items.popleft()
            self.condition.notify_all()
            return item

    def discard_source(self, source_id):
        with self.condition:
            kept = [
                item for item in self.items if len(item) < 2 or item[1] != source_id
            ]
            removed = len(self.items) - len(kept)
            self.items.clear()
            self.items.extend(kept)
            self.model_events = deque(
                event
                for event in self.model_events
                if event.get("source_id") != source_id
            )
            self.condition.notify_all()
            return removed

    def retire_source(self, source_id):
        with self.condition:
            self.retired.add(source_id)
            kept = [
                item for item in self.items if len(item) < 2 or item[1] != source_id
            ]
            removed = len(self.items) - len(kept)
            self.items.clear()
            self.items.extend(kept)
            self.model_events = deque(
                event
                for event in self.model_events
                if event.get("source_id") != source_id
            )
            self.condition.notify_all()
            return removed

    def publish_model_event(self, event):
        with self.condition:
            source_id = event.get("source_id")
            if source_id in self.retired:
                return False
            self.model_events.append(event)
            if event.get("type") == "completed":
                self.retired.add(source_id)
            self.condition.notify_all()
            return True

    def get_model_event(self, timeout=None):
        from queue import Empty

        deadline = None if timeout is None else time.monotonic() + timeout
        with self.condition:
            while not self.model_events:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise Empty
                self.condition.wait(remaining)
            return self.model_events.popleft()

    def qsize(self):
        with self.condition:
            return len(self.items)


class _IPCManager(SyncManager):
    pass


_IPCManager.register("BoundedFIFO", _BoundedFIFO)


def _put_event(output, kind, source_id, **fields):
    output.put({"type": kind, "source_id": source_id, "pid": os.getpid(), **fields})


def _validate_timeout(timeout):
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout < 0
    ):
        raise ConfigurationError("timeout must be a finite, non-negative number")


def _capture_chunks(
    device,
    source_id,
    flow,
    model_config,
    input_queue,
    source_control,
    model_control,
    output_queue,
    ready_queue,
    telemetry_queue,
    topology,
    runtime_factory=None,
    audio_source_factory=None,
):
    """Capture in its own spawned process; only bounded audio enters IPC."""
    from .capture import SoundDeviceSource
    from .model import load_model

    local_frames = Queue(maxsize=16)
    local_control = Queue()
    handle = None
    source = None
    ready_sent = False
    queue_failed = False
    per_source_model_load_seconds = None
    try:
        if topology == "per-input-model":
            model_start = time.perf_counter()
            handle = load_model(model_config, runtime_factory=runtime_factory)
            per_source_model_load_seconds = time.perf_counter() - model_start
            handle._telemetry_sink = telemetry_queue
            handle._measurement_operation = "microphone-process-group"
            from . import start_microphone_flow

            session = start_microphone_flow(
                device, handle, flow, audio_source_factory=audio_source_factory
            )
            source = session.source
            ready_queue.put(
                ("source", source_id, None, per_source_model_load_seconds)
            )
            ready_sent = True
            while True:
                try:
                    command = source_control.get(timeout=0.05)
                    if command[0] == "start":
                        begin = getattr(source, "start_stream", None)
                        if callable(begin):
                            begin()
                    elif command[0] == "stop":
                        session.stop(timeout=60)
                except Empty:
                    pass
                if getattr(source, "finished", False) and not session._terminal:
                    session.stop(timeout=60)
                try:
                    event = session.result_queue.get(timeout=0.05)
                except Empty:
                    if session._terminal:
                        return
                    continue
                event.setdefault("pid", os.getpid())
                output_queue.put(event)
                if event["type"] == "completed":
                    return

        def on_audio(frames):
            try:
                local_frames.put_nowait(np.asarray(frames, dtype=np.float32).copy())
            except Full:
                try:
                    local_control.put_nowait(
                        ("capture-error", "Microphone callback queue is full")
                    )
                except Full:
                    pass

        def on_error(error):
            local_control.put(("capture-error", str(error)))

        source_factory = audio_source_factory or SoundDeviceSource
        source = source_factory(device=device, on_audio=on_audio, on_error=on_error)
        resampler = StreamingResampler(source.sample_rate)
        target_frames = max(1, int(round(flow["chunk_seconds"] * TARGET_RATE)))
        buffer = np.empty(0, dtype=np.float32)
        sequence = -1
        first_queued_at = None
        source.start()
        ready_queue.put(
            ("source", source_id, None, per_source_model_load_seconds)
        )
        ready_sent = True

        stop_requested = False
        capture_error = None

        def submit(chunk):
            nonlocal sequence, queue_failed, first_queued_at
            if (
                not chunk.size
                or float(np.sqrt(np.mean(chunk * chunk))) < flow["silence_threshold"]
            ):
                return True
            sequence += 1
            if handle is not None:
                if first_queued_at is None:
                    first_queued_at = (
                        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    )
                    output_queue.put(
                        {
                            "type": "audio_queued",
                            "source_id": source_id,
                            "first_queued_at": first_queued_at,
                        }
                    )
                queued_at = time.perf_counter()
                inference_error = None
                with handle._inference_lock:
                    handle._check()
                    inference_started = time.perf_counter()
                    queue_wait = max(0.0, inference_started - queued_at)
                    try:
                        transcript = handle._transcribe(chunk, flow)
                    except Exception as exc:
                        inference_error = exc
                    inference_seconds = max(
                        0.0, time.perf_counter() - inference_started
                    )
                if inference_error is None:
                    transcript = str(transcript).strip()
                if inference_error is None and transcript:
                    _put_event(
                        output_queue,
                        "transcript",
                        source_id,
                        sequence=sequence,
                        text=transcript,
                    )
                elif inference_error is not None:
                    _put_event(
                        output_queue,
                        "error",
                        source_id,
                        code="CHUNK_INFERENCE_FAILED",
                        message=str(inference_error),
                        sequence=sequence,
                        fatal=False,
                    )
                record = publish_inference_measurement(
                    None,
                    operation="microphone-process-group",
                    source_id=source_id,
                    sequence=sequence,
                    audio_seconds=len(chunk) / TARGET_RATE,
                    inference_seconds=inference_seconds,
                    queue_wait_seconds=queue_wait,
                    status="failed" if inference_error else "completed",
                    error=inference_error,
                )
                output_queue.put(record)
                telemetry_queue.put(
                    {
                        "source_id": source_id,
                        "sequence": sequence,
                        "queue_wait_seconds": queue_wait,
                        "elapsed_seconds": queue_wait + inference_seconds,
                        "inference_seconds": inference_seconds,
                        "chunk_duration_seconds": len(chunk) / TARGET_RATE,
                        "rtf": (queue_wait + inference_seconds)
                        / max(len(chunk) / TARGET_RATE, 1e-9),
                        "discarded_inflight": False,
                        "pid": os.getpid(),
                    }
                )
                return True
            try:
                input_queue.put(
                    ("chunk", source_id, sequence, chunk, flow, time.perf_counter()),
                    timeout=model_config["enqueue_timeout_seconds"],
                )
                if first_queued_at is None:
                    first_queued_at = (
                        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    )
                    output_queue.put(
                        {
                            "type": "audio_queued",
                            "source_id": source_id,
                            "first_queued_at": first_queued_at,
                        }
                    )
                return True
            except Full:
                discarded = input_queue.retire_source(source_id)
                model_control.put(("retire", source_id))
                _put_event(
                    output_queue,
                    "error",
                    source_id,
                    code="INPUT_QUEUE_TIMEOUT",
                    message=f"Shared inference queue remained full for {model_config['enqueue_timeout_seconds']} seconds",
                    sequence=sequence,
                    fatal=True,
                    rejected_chunks=1,
                    discarded_queued_chunks=discarded,
                )
                _put_event(
                    output_queue,
                    "completed",
                    source_id,
                    status="failed",
                    last_sequence=sequence,
                )
                return False

        while not stop_requested and capture_error is None:
            try:
                command = source_control.get_nowait()
                if command[0] == "stop":
                    stop_requested = True
                elif command[0] == "start":
                    begin = getattr(source, "start_stream", None)
                    if callable(begin):
                        begin()
            except Empty:
                pass
            try:
                message = local_control.get_nowait()
                if message[0] == "capture-error":
                    capture_error = message[1]
            except Empty:
                pass
            try:
                frames = local_frames.get(timeout=0.05)
            except Empty:
                if getattr(source, "finished", False):
                    stop_requested = True
                continue
            buffer = np.concatenate((buffer, resampler.feed(frames)))
            offset = 0
            while buffer.size - offset >= target_frames:
                if not submit(buffer[offset : offset + target_frames].copy()):
                    stop_requested = True
                    capture_error = None
                    queue_failed = True
                    break
                offset += target_frames
            buffer = buffer[offset:].copy()

        try:
            source.stop()
        except Exception as exc:
            capture_error = capture_error or str(exc)
        # Preserve frames accepted by the bounded callback queue before stop.
        while not queue_failed:
            try:
                frames = local_frames.get_nowait()
            except Empty:
                break
            buffer = np.concatenate((buffer, resampler.feed(frames)))
            while buffer.size >= target_frames:
                if not submit(buffer[:target_frames].copy()):
                    capture_error = None
                    queue_failed = True
                    break
                buffer = buffer[target_frames:].copy()
        if not queue_failed:
            buffer = np.concatenate((buffer, resampler.flush()))
            while buffer.size > target_frames:
                if not submit(buffer[:target_frames].copy()):
                    capture_error = None
                    queue_failed = True
                    break
                buffer = buffer[target_frames:].copy()
        if capture_error:
            if model_control is not None:
                discarded = input_queue.retire_source(source_id)
                model_control.put(("retire", source_id))
            else:
                discarded = 0
            _put_event(
                output_queue,
                "error",
                source_id,
                code="CAPTURE_FAILED",
                message=capture_error,
                sequence=None,
                fatal=True,
                discarded_queued_chunks=discarded,
            )
            _put_event(
                output_queue,
                "completed",
                source_id,
                status="failed",
                last_sequence=sequence if sequence >= 0 else None,
            )
            return
        if queue_failed:
            return
        if buffer.size and not submit(buffer):
            return
        status = "stopped"
        last = sequence if sequence >= 0 else None
        if handle is None:
            try:
                input_queue.put(
                    ("complete", source_id, status, last),
                    timeout=model_config["enqueue_timeout_seconds"],
                )
            except Full:
                discarded = input_queue.retire_source(source_id)
                model_control.put(("retire", source_id))
                _put_event(
                    output_queue,
                    "error",
                    source_id,
                    code="INPUT_QUEUE_TIMEOUT",
                    message="Shared inference queue remained full while reporting source completion",
                    sequence=None,
                    fatal=True,
                    discarded_queued_chunks=discarded,
                )
                _put_event(
                    output_queue,
                    "completed",
                    source_id,
                    status="failed",
                    last_sequence=last,
                )
        else:
            _put_event(
                output_queue, "completed", source_id, status=status, last_sequence=last
            )
    except Exception as exc:
        if (
            not ready_sent
            and source is None
            and topology == "per-input-model"
            and handle is None
        ):
            ready_queue.put(("model", source_id, str(exc)))
        elif not ready_sent:
            ready_queue.put(("source", source_id, str(exc)))
        else:
            _put_event(
                output_queue,
                "error",
                source_id,
                code="CAPTURE_FAILED",
                message=str(exc),
                sequence=None,
                fatal=True,
                discarded_queued_chunks=0,
            )
            _put_event(
                output_queue,
                "completed",
                source_id,
                status="failed",
                last_sequence=None,
            )
    finally:
        if source is not None:
            try:
                source.stop()
            except Exception:
                pass
        if handle is not None:
            handle.close()


def _shared_model_worker(
    config,
    input_queue,
    control_queue,
    output_queue,
    ready_queue,
    telemetry_queue,
    runtime_factory=None,
):
    from .model import load_model

    try:
        handle = load_model(config, runtime_factory=runtime_factory)
        ready_queue.put(("model", None, None))
    except Exception as exc:
        ready_queue.put(("model", None, str(exc)))
        return
    retired = set()
    stopping = False

    def apply_controls():
        nonlocal stopping
        while True:
            try:
                control = control_queue.get_nowait()
            except Empty:
                break
            if control[0] == "retire":
                source_id = control[1]
                retired.add(source_id)
                input_queue.retire_source(source_id)
            elif control[0] == "shutdown":
                stopping = True

    try:
        while not stopping:
            apply_controls()
            if stopping:
                break
            try:
                item = input_queue.get(timeout=0.05)
            except Empty:
                continue
            if item[0] == "chunk":
                _, source_id, sequence, audio, flow, queued_at = item
                if source_id in retired:
                    continue
                inference_error = None
                text = None
                with handle._inference_lock:
                    handle._check()
                    inference_started = time.perf_counter()
                    queue_wait = max(0.0, inference_started - queued_at)
                    try:
                        text = handle._transcribe(audio, flow)
                    except Exception as exc:
                        inference_error = exc
                    inference_seconds = max(
                        0.0, time.perf_counter() - inference_started
                    )
                if inference_error is None:
                    text = str(text).strip()
                duration = len(audio) / TARGET_RATE
                if inference_error is None:
                    apply_controls()
                    record = publish_inference_measurement(
                        None,
                        operation="microphone-process-group",
                        source_id=source_id,
                        sequence=sequence,
                        audio_seconds=duration,
                        inference_seconds=inference_seconds,
                        queue_wait_seconds=queue_wait,
                    )
                    input_queue.publish_model_event(record)
                    telemetry_queue.put(
                        {
                            "source_id": source_id,
                            "sequence": sequence,
                            "queue_wait_seconds": max(0, inference_started - queued_at),
                            "elapsed_seconds": queue_wait + inference_seconds,
                            "inference_seconds": inference_seconds,
                            "chunk_duration_seconds": duration,
                            "rtf": (queue_wait + inference_seconds)
                            / max(duration, 1e-9),
                            "discarded_inflight": source_id in retired,
                            "pid": os.getpid(),
                        }
                    )
                    if text and source_id not in retired:
                        input_queue.publish_model_event(
                            {
                                "type": "transcript",
                                "source_id": source_id,
                                "sequence": sequence,
                                "text": text,
                            }
                        )
                else:
                    apply_controls()
                    record = publish_inference_measurement(
                        None,
                        operation="microphone-process-group",
                        source_id=source_id,
                        sequence=sequence,
                        audio_seconds=len(audio) / TARGET_RATE,
                        inference_seconds=inference_seconds,
                        queue_wait_seconds=queue_wait,
                        status="failed",
                        error=inference_error,
                    )
                    input_queue.publish_model_event(record)
                    if source_id not in retired:
                        input_queue.publish_model_event(
                            {
                                "type": "error",
                                "source_id": source_id,
                                "code": "CHUNK_INFERENCE_FAILED",
                                "message": str(inference_error),
                                "sequence": sequence,
                                "fatal": False,
                                "pid": os.getpid(),
                            }
                        )
            elif item[0] == "complete":
                _, source_id, status, last_sequence = item
                if source_id not in retired:
                    input_queue.publish_model_event(
                        {
                            "type": "completed",
                            "source_id": source_id,
                            "status": status,
                            "last_sequence": last_sequence,
                            "pid": os.getpid(),
                        }
                    )
                retired.discard(source_id)
    finally:
        handle.close()


class ProcessSession:
    def __init__(self, source_id, process, control_queue, *, result_queue=None):
        self.source_id = source_id
        self.process = process
        self._control_queue = control_queue
        self.result_queue = result_queue or Queue()
        self._stopping = False
        self._terminal = False
        self._terminal_event = threading.Event()

    def stop(self, *, timeout=30):
        _validate_timeout(timeout)
        deadline = time.monotonic() + timeout
        remaining = lambda: max(0, deadline - time.monotonic())
        if self._stopping:
            if self.process.is_alive():
                self.process.join(remaining())
                if self.process.is_alive():
                    raise TimeoutError(
                        f"Microphone process {self.source_id!r} did not stop before timeout"
                    )
            if not self._terminal_event.wait(remaining()):
                raise TimeoutError(
                    f"Microphone process {self.source_id!r} did not report completion before timeout"
                )
            return
        self._stopping = True
        if self.process.is_alive():
            self._control_queue.put(("stop", self.source_id))
            self.process.join(remaining())
            if self.process.is_alive():
                raise TimeoutError(
                    f"Microphone process {self.source_id!r} did not stop before timeout"
                )
        if not self._terminal_event.wait(remaining()):
            raise TimeoutError(
                f"Microphone process {self.source_id!r} did not report completion before timeout"
            )


class ProcessFlowGroup:
    """Owns independent capture processes and the optional shared model process."""

    def __init__(
        self,
        sessions,
        context,
        output_queue,
        *,
        model_process=None,
        model_control=None,
        input_queue=None,
        manager=None,
        telemetry_queue=None,
    ):
        self.sessions = sessions
        self._context = context
        self._output_queue = output_queue
        self._model_process = model_process
        self._model_control = model_control
        self._input_queue = input_queue
        self._manager = manager
        self.topology = (
            "shared-model" if model_process is not None else "per-input-model"
        )
        self.telemetry_queue = telemetry_queue or context.Queue()
        self._finished = threading.Event()
        self._aborted = False
        self._terminal_ids = set()
        self._condition = threading.Condition()
        self._dispatcher = threading.Thread(
            target=self._dispatch, name="stt-process-results", daemon=True
        )
        self._dispatcher.start()

    def _dispatch(self):
        by_id = {session.source_id: session for session in self.sessions}
        while not self._finished.is_set():
            try:
                event = (
                    self._input_queue.get_model_event(timeout=0)
                    if self._input_queue is not None
                    else None
                )
            except Empty:
                event = None
            if event is None:
                try:
                    event = self._output_queue.get(timeout=0.05)
                except Empty:
                    continue
            session = by_id.get(event.get("source_id"))
            if session is not None:
                if session._terminal:
                    continue
                session.result_queue.put(event)
                if event.get("type") == "completed":
                    session._terminal = True
                    session._terminal_event.set()
                    with self._condition:
                        self._terminal_ids.add(session.source_id)
                        self._condition.notify_all()

    def stop(self, *, timeout=30):
        _validate_timeout(timeout)
        deadline = time.monotonic() + timeout if timeout is not None else None
        for session in self.sessions:
            remaining = (
                None if deadline is None else max(0, deadline - time.monotonic())
            )
            session.stop(timeout=remaining)
        with self._condition:
            while len(self._terminal_ids) < len(self.sessions):
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError(
                        "Microphone process results did not reach their terminal events before timeout"
                    )
                self._condition.wait(remaining)
        if self._model_process is not None and self._model_process.is_alive():
            self._model_control.put(("shutdown", None))
            remaining = (
                None if deadline is None else max(0, deadline - time.monotonic())
            )
            self._model_process.join(remaining)
            if self._model_process.is_alive():
                raise TimeoutError("Shared model process did not stop before timeout")
        self._finished.set()
        self._dispatcher.join(1)
        for session in self.sessions:
            if session.process.is_alive():
                session.process.terminate()
                session.process.join(1)
        if self._manager is not None:
            self._manager.shutdown()
            self._manager = None

    def abort(self, *, timeout=5):
        """Force-stop owned child processes after a failed benchmark run."""
        _validate_timeout(timeout)
        if self._aborted:
            return
        deadline = time.monotonic() + timeout
        processes = [session.process for session in self.sessions]
        if self._model_process is not None:
            processes.append(self._model_process)
        for process in processes:
            if process.is_alive():
                try:
                    process.terminate()
                except (AssertionError, OSError, ValueError):
                    pass
        for process in processes:
            try:
                process.join(max(0, deadline - time.monotonic()))
            except (AssertionError, OSError, ValueError):
                pass
        for process in processes:
            if process.is_alive():
                try:
                    process.kill()
                    process.join(max(0, deadline - time.monotonic()) or 1)
                except (AttributeError, AssertionError, OSError, ValueError):
                    pass
        self._finished.set()
        self._dispatcher.join(max(0, deadline - time.monotonic()))
        if self._manager is not None:
            self._manager.shutdown()
            self._manager = None
        if self._dispatcher.is_alive():
            self._dispatcher.join(1)
        alive = [process.pid for process in processes if process.is_alive()]
        if alive:
            raise TimeoutError(
                f"Could not stop owned process(es) during abort: {alive}"
            )
        if self._dispatcher.is_alive():
            raise TimeoutError(
                "Could not stop the process result dispatcher during abort"
            )
        self._aborted = True


def start_multiprocess_microphone_flows(
    devices,
    model_config=None,
    flow_config=None,
    *,
    topology="shared-model",
    runtime_factory=None,
    audio_source_factory=None,
    flow_configs=None,
    startup_monitor_callback=None,
):
    """Start separate capture processes in shared-model or per-input-model mode.

    Shared mode owns one model in its own process and routes tagged PCM chunks
    over a bounded FIFO queue. Per-input mode loads one independent model in
    each microphone process. Runtime factories, when supplied, must be
    picklable because they are passed to spawned processes. An optional startup
    monitor callback receives the started process handles while readiness is
    pending, allowing callers to sample startup resource use.
    """
    resolved_model = validate_model_config(model_config)
    common_flow = validate_flow_config(flow_config)
    catalog_entry = next(
        (entry for entry in list_models() if entry["key"] == resolved_model["model"]),
        None,
    )
    if catalog_entry is None:
        raise ConfigurationError(
            f"Unknown model key {resolved_model['model']!r}; call list_available_models() to inspect known models"
        )
    if topology not in {"shared-model", "per-input-model"}:
        raise ConfigurationError("topology must be 'shared-model' or 'per-input-model'")
    if not isinstance(devices, (list, tuple)):
        raise ConfigurationError(
            "devices must be a list or tuple of microphone device names"
        )
    device_list = list(devices)
    if not device_list:
        raise ConfigurationError(
            "devices must contain at least one microphone device name or None"
        )
    if any(
        device is not None and (not isinstance(device, str) or not device.strip())
        for device in device_list
    ):
        raise ConfigurationError(
            "Each device must be omitted or a non-empty stable device name"
        )
    if flow_configs is None:
        per_source_flows = [deepcopy(common_flow) for _ in device_list]
    else:
        if not isinstance(flow_configs, (list, tuple)) or len(flow_configs) != len(
            device_list
        ):
            raise ConfigurationError(
                "flow_configs must be a list with one configuration per device"
            )
        if any(not isinstance(override, dict) for override in flow_configs):
            raise ConfigurationError("Each flow_configs entry must be a mapping")
        per_source_flows = [
            validate_flow_config({**common_flow, **override})
            for override in flow_configs
        ]
    source_ids = [
        flow.get("source_id") for flow in per_source_flows if flow.get("source_id")
    ]
    if len(source_ids) != len(set(source_ids)):
        raise ConfigurationError("Each microphone process must have a unique source_id")
    if len(device_list) > 1 and common_flow.get("source_id") and flow_configs is None:
        raise ConfigurationError(
            "source_id must be set per device through flow_configs when starting multiple microphone processes"
        )
    for flow in per_source_flows:
        if (
            flow["language"] != "auto"
            and flow["language"] not in catalog_entry["languages"]
        ):
            raise ConfigurationError(
                f"Language {flow['language']!r} is not supported by model {resolved_model['model']!r}"
            )
    context = mp.get_context("spawn")
    manager = _IPCManager(ctx=context)
    manager.start()
    output_queue = context.Queue()
    ready_queue = context.Queue()
    telemetry_queue = context.Queue()
    model_process = None

    def wait_for_ready(processes):
        deadline = time.monotonic() + 60
        while True:
            if startup_monitor_callback is not None:
                startup_monitor_callback(tuple(processes))
            try:
                timeout = min(0.1, max(0, deadline - time.monotonic()))
                return ready_queue.get(timeout=timeout)
            except Empty:
                if time.monotonic() >= deadline:
                    raise
                continue

    model_control = None
    input_queue = None
    if topology == "shared-model":
        try:
            input_queue = manager.BoundedFIFO(resolved_model["queue_capacity"])
            model_control = manager.Queue()
            model_start = time.perf_counter()
            model_process = context.Process(
                target=_shared_model_worker,
                args=(
                    resolved_model,
                    input_queue,
                    model_control,
                    output_queue,
                    ready_queue,
                    telemetry_queue,
                    runtime_factory,
                ),
                name="stt-shared-model",
            )
            model_process.start()
            kind, _, error = wait_for_ready([model_process])
            if error:
                model_process.join(1)
                raise ModelLoadError(error)
            if kind != "model":
                raise ModelLoadError(
                    "Shared model process failed before initialization"
                )
            model_load_seconds = time.perf_counter() - model_start
        except Exception:
            if model_process is not None and model_process.is_alive():
                model_process.terminate()
                model_process.join(2)
            manager.shutdown()
            raise
    else:
        model_load_seconds = None
    sessions = []
    session_start_times = {}
    try:
        for index, (device, flow) in enumerate(zip(device_list, per_source_flows)):
            flow = deepcopy(flow)
            source_id = (
                flow.get("source_id") or f"mic-{index + 1}-{uuid.uuid4().hex[:8]}"
            )
            flow["source_id"] = source_id
            source_control = manager.Queue()
            source_start = time.perf_counter()
            process = context.Process(
                target=_capture_chunks,
                args=(
                    device,
                    source_id,
                    flow,
                    resolved_model,
                    input_queue,
                    source_control,
                    model_control,
                    output_queue,
                    ready_queue,
                    telemetry_queue,
                    topology,
                    runtime_factory,
                    audio_source_factory,
                ),
                name=f"stt-{source_id}",
            )
            process.start()
            sessions.append(ProcessSession(source_id, process, source_control))
            session_start_times[source_id] = source_start
        source_load_seconds = {}
        per_source_model_load_seconds = {}
        for _ in range(len(device_list)):
            ready = wait_for_ready(
                [
                    *([model_process] if model_process is not None else []),
                    *(session.process for session in sessions),
                ]
            )
            kind, source_id, error = ready[:3]
            if error:
                raise (
                    AudioInputError(error)
                    if kind == "source"
                    else ModelLoadError(error)
                )
            if kind != "source":
                raise AudioInputError("Microphone process failed during startup")
            source_load_seconds[source_id] = (
                time.perf_counter() - session_start_times[source_id]
            )
            if len(ready) > 3 and ready[3] is not None:
                per_source_model_load_seconds[source_id] = ready[3]
        # File-backed sources wait for this command so all inputs begin only
        # after the shared model (when present) and every input process are ready.
        for session in sessions:
            session._control_queue.put(("start", session.source_id))
    except Exception:
        for session in sessions:
            if session.process.is_alive():
                session.process.terminate()
                session.process.join(2)
        if model_process is not None and model_process.is_alive():
            model_process.terminate()
            model_process.join(2)
        manager.shutdown()
        raise
    group = ProcessFlowGroup(
        sessions,
        context,
        output_queue,
        model_process=model_process,
        model_control=model_control,
        input_queue=input_queue,
        manager=manager,
        telemetry_queue=telemetry_queue,
    )
    group.model_load_seconds = model_load_seconds
    group.per_source_model_load_seconds = per_source_model_load_seconds
    group.source_load_seconds = source_load_seconds
    group.topology = topology
    return group
