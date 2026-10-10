"""Process-owned runtime handles and finite-clip decoding."""

from __future__ import annotations

import os
import threading
import time
import uuid
import warnings
from copy import deepcopy
from queue import Queue

from .audio import TARGET_RATE, read_clip
from .config import (
    flow_config as validate_flow_config,
)
from .config import (
    model_config as validate_model_config,
)
from .errors import (
    ChunkInferenceWarning,
    ConfigurationError,
    ModelClosedError,
    ModelLoadError,
)
from .measurements import publish_inference_measurement
from .runtime import create_runtime


class ModelHandle:
    """One fixed model/runtime instance owned by the process that loaded it."""

    def __init__(self, config, runtime):
        self._config = deepcopy(config)
        from .catalog import list_models

        self.languages = next(
            (
                set(item["languages"])
                for item in list_models()
                if item["key"] == config["model"]
            ),
            {"th", "en", "auto"},
        )
        self._runtime_impl = runtime
        self._owner_pid = os.getpid()
        self._state = "ready"
        self._state_lock = threading.RLock()
        self._inference_lock = threading.RLock()
        self._work_queue = Queue(maxsize=config["queue_capacity"])
        self._ingress_queue = Queue(maxsize=max(16, config["queue_capacity"] * 4))
        self._ingress_lock = threading.RLock()
        self._ingress_worker = None
        self._worker = None
        self._sessions = set()

    @property
    def model(self):
        return self._config["model"]

    @property
    def runtime(self):
        return self._config["runtime"]

    @property
    def precision(self):
        return self._config["precision"]

    @property
    def state(self):
        with self._state_lock:
            return self._state

    @property
    def config(self):
        return deepcopy(self._config)

    def _check(self):
        if os.getpid() != self._owner_pid:
            raise ModelLoadError(
                "ModelHandle belongs to the process that loaded it; load a new handle in this process"
            )
        if self.state != "ready":
            raise ModelClosedError(f"Model handle is {self.state}")

    def _transcribe(self, audio, flow):
        self._check()
        with self._inference_lock:
            self._check()
            return self._runtime_impl.transcribe(
                audio,
                language=flow["language"],
                decoding_options=deepcopy(flow["decoding_options"]),
            )

    def _ensure_worker(self):
        with self._state_lock:
            self._check()
            if self._worker is None:
                from .session import ingress_worker, model_worker

                self._worker = threading.Thread(
                    target=model_worker,
                    args=(self,),
                    name=f"stt-model-{id(self):x}",
                    daemon=True,
                )
                self._ingress_worker = threading.Thread(
                    target=ingress_worker,
                    args=(self,),
                    name=f"stt-ingress-{id(self):x}",
                    daemon=True,
                )
                self._worker.start()
                self._ingress_worker.start()

    def close(self, *, timeout=30):
        import time

        deadline = None if timeout is None else time.monotonic() + timeout
        with self._state_lock:
            if self._state == "closed":
                return
            if os.getpid() != self._owner_pid:
                raise ModelLoadError(
                    "Only the process that loaded a ModelHandle may close it"
                )
            sessions = list(self._sessions)
        for session in sessions:
            remaining = (
                None if deadline is None else max(0, deadline - time.monotonic())
            )
            session.stop(timeout=remaining)
        remaining = None if deadline is None else max(0, deadline - time.monotonic())
        acquired = (
            self._inference_lock.acquire(timeout=remaining)
            if remaining is not None
            else self._inference_lock.acquire()
        )
        if not acquired:
            raise TimeoutError("Model inference did not finish before close timeout")
        try:
            with self._state_lock:
                if self._state == "closed":
                    return
                self._state = "closed"
            close = getattr(self._runtime_impl, "close", None)
            if close:
                close()
        finally:
            self._inference_lock.release()
        worker = self._worker
        if worker is not None:
            remaining = (
                None if deadline is None else max(0, deadline - time.monotonic())
            )
            worker.join(remaining)
            if worker.is_alive():
                raise TimeoutError(
                    "Model inference worker did not stop before close timeout"
                )
        ingress = self._ingress_worker
        if ingress is not None:
            remaining = (
                None if deadline is None else max(0, deadline - time.monotonic())
            )
            ingress.join(remaining)
            if ingress.is_alive():
                raise TimeoutError(
                    "Model input dispatcher did not stop before close timeout"
                )


def load_model(model_config=None, *, runtime_factory=None):
    resolved = validate_model_config(model_config)
    from .catalog import list_models

    if not any(item["key"] == resolved["model"] for item in list_models()):
        raise ConfigurationError(
            f"Unknown model key {resolved['model']!r}; call list_available_models() to see known models"
        )
    try:
        runtime = (
            runtime_factory(deepcopy(resolved))
            if runtime_factory
            else create_runtime(resolved)
        )
        if runtime is None or not callable(getattr(runtime, "transcribe", None)):
            raise TypeError(
                "runtime factory must return an object with transcribe(audio, *, language, decoding_options)"
            )
        return ModelHandle(resolved, runtime)
    except ModelLoadError:
        raise
    except Exception as exc:
        raise ModelLoadError(
            f"Unable to load {resolved['model']} with {resolved['runtime']}: {exc}"
        ) from exc


def transcribe_clip(
    clip,
    model_handle,
    flow_config=None,
    *,
    measurement_sink=None,
    source_id=None,
    measurement_clock=None,
    monotonic_clock=None,
):
    if not isinstance(model_handle, ModelHandle):
        raise TypeError("model_handle must be a ModelHandle returned by load_model")
    model_handle._check()
    flow = validate_flow_config(flow_config)
    if flow["language"] != "auto" and flow["language"] not in model_handle.languages:
        raise ConfigurationError(
            f"Language {flow['language']!r} is not supported by model {model_handle.model!r}"
        )
    audio = read_clip(clip)
    frame_count = max(1, round(flow["chunk_seconds"] * TARGET_RATE))
    transcripts = []
    source_id = source_id or uuid.uuid4().hex
    monotonic_clock = monotonic_clock or time.perf_counter
    inferred_chunks = []
    for sequence, start in enumerate(range(0, audio.size, frame_count)):
        chunk = audio[start : start + frame_count]
        if (
            chunk.size
            and float((chunk * chunk).mean() ** 0.5) >= flow["silence_threshold"]
        ):
            inferred_chunks.append((sequence, chunk))
    if not inferred_chunks:
        return ""

    # Keep a clip's inferred chunks contiguous when other sessions share this handle.
    queued_at = monotonic_clock()
    with model_handle._inference_lock:
        model_handle._check()
        for index, (sequence, chunk) in enumerate(inferred_chunks):
            inference_started = monotonic_clock()
            queue_wait = (
                max(0.0, inference_started - queued_at) if index == 0 else 0.0
            )
            try:
                text = model_handle._transcribe(chunk, flow)
            except Exception as exc:  # noqa: BLE001
                inference_elapsed = max(0.0, monotonic_clock() - inference_started)
                publish_inference_measurement(
                    measurement_sink,
                    operation="finite-clip",
                    source_id=source_id,
                    sequence=sequence,
                    audio_seconds=len(chunk) / TARGET_RATE,
                    inference_seconds=inference_elapsed,
                    queue_wait_seconds=queue_wait,
                    status="failed",
                    error=exc,
                    clock=measurement_clock,
                )
                warnings.warn(
                    f"Chunk {sequence} inference failed: {exc}",
                    ChunkInferenceWarning,
                    stacklevel=2,
                )
                continue
            inference_elapsed = max(0.0, monotonic_clock() - inference_started)
            publish_inference_measurement(
                measurement_sink,
                operation="finite-clip",
                source_id=source_id,
                sequence=sequence,
                audio_seconds=len(chunk) / TARGET_RATE,
                inference_seconds=inference_elapsed,
                queue_wait_seconds=queue_wait,
                clock=measurement_clock,
            )
            text = str(text).strip()
            if text:
                transcripts.append(text)
    return " ".join(transcripts)
