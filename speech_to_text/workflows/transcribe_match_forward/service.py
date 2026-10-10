"""Process-local service facade for backend-owned workflow operations."""

from __future__ import annotations

import os
import threading
from queue import Empty

from speech_to_text.features.model_deployment import (
    AudioInputError,
    ConfigurationError,
    ModelLoadError,
    list_available_models,
    load_model,
    start_multiprocess_microphone_flows,
)
from speech_to_text.features.word_matching import WordMatchingConfig, WordMatchingError

from .api import (
    forward_session,
    list_available_microphones,
    start_microphone_and_forward,
    transcribe_clip_and_forward,
)


class WorkflowServiceError(RuntimeError):
    """Base error translated from feature failures at the workflow boundary."""


class WorkflowConfigurationError(WorkflowServiceError):
    """The requested workflow or model configuration is invalid."""


class WorkflowInputError(WorkflowServiceError):
    """A requested audio input or microphone device cannot be used."""


class WorkflowUnavailableError(WorkflowServiceError):
    """A model or host audio dependency is unavailable."""


class _ProcessGroupOwner:
    """Make process-group shutdown safe across completion and service close."""

    def __init__(self, group):
        self.group = group
        self._lock = threading.Lock()
        self._closed = False

    def close(self):
        with self._lock:
            if self._closed:
                return
            try:
                self.group.stop()
            except Exception:
                self.group.abort()
                raise
            finally:
                self._closed = True


class TranscriptionService:
    """Lazily own one model handle for clip and microphone workflows."""

    def __init__(self, model_config=None):
        self._model_config = None if model_config is None else dict(model_config)
        self._model_handle = None
        self._model_handles = {}
        self._model_lock = threading.Lock()
        self._microphone_start_lock = threading.Lock()
        self._process_groups_lock = threading.Lock()
        self._process_groups = set()

    def _config_from_environment(self):
        return {
            key: os.environ[name]
            for key, name in (
                ("model", "SPEECH_TO_TEXT_MODEL"),
                ("runtime", "SPEECH_TO_TEXT_RUNTIME"),
                ("precision", "SPEECH_TO_TEXT_PRECISION"),
            )
            if name in os.environ
        }

    def _effective_model_config(self, model=None, runtime=None):
        config = {
            "model": "turbo",
            "runtime": "openvino-gpu",
            "precision": "source",
            "queue_capacity": 6,
            "enqueue_timeout_seconds": 1.0,
        }
        config.update(self._config_from_environment())
        if self._model_config is not None:
            config.update(self._model_config)
        if model is not None:
            config["model"] = model
        if runtime is not None:
            config["runtime"] = runtime
        precision_is_explicit = (
            "precision" in self._config_from_environment()
            or self._model_config is not None
            and "precision" in self._model_config
        )
        # `source` is Feature 01's OpenVINO default. CTranslate2 uses its
        # supported int8 default unless an operator explicitly configured precision.
        if config["runtime"] == "ctranslate2" and not precision_is_explicit:
            config["precision"] = "int8"
        return config

    @property
    def default_model(self):
        return self._effective_model_config()["model"]

    @property
    def default_runtime(self):
        return self._effective_model_config()["runtime"]

    def validate_model_key(self, model):
        """Reject model keys that are not present in the local catalog."""
        try:
            if not any(item["key"] == model for item in list_available_models()):
                raise WorkflowConfigurationError(f"Unknown model key {model!r}")
        except (OSError, ValueError) as exc:
            raise WorkflowUnavailableError(str(exc)) from exc
        return model

    def validate_model_runtime(self, model, runtime):
        """Validate a supported, catalog-compatible model/runtime pair."""
        try:
            item = next(
                (entry for entry in list_available_models() if entry["key"] == model),
                None,
            )
        except (OSError, ValueError) as exc:
            raise WorkflowUnavailableError(str(exc)) from exc
        if item is None:
            raise WorkflowConfigurationError(f"Unknown model key {model!r}")
        supported_runtimes = [option["key"] for option in item["runtimes"]]
        if runtime not in supported_runtimes:
            raise WorkflowConfigurationError(
                f"Unsupported runtime {runtime!r}; choose one of {', '.join(supported_runtimes)}"
            )
        choice = next(
            (option for option in item["runtimes"] if option["key"] == runtime),
            None,
        )
        if choice is None or not choice["compatible"]:
            raise WorkflowConfigurationError(
                f"Runtime {runtime!r} is not supported for model {model!r}"
            )
        return runtime

    def _get_model(self, model=None):
        config = self._effective_model_config(model)
        return self._get_model_config(config)

    def _get_model_config(self, config):
        cache_key = tuple(sorted(config.items()))
        with self._model_lock:
            handle = self._model_handles.get(cache_key)
            if handle is None:
                try:
                    handle = load_model(config)
                except ConfigurationError as exc:
                    raise WorkflowConfigurationError(str(exc)) from exc
                except ModelLoadError as exc:
                    raise WorkflowUnavailableError(str(exc)) from exc
                self._model_handles[cache_key] = handle
                if self._model_handle is None:
                    self._model_handle = handle
            return handle

    @staticmethod
    def _matching_config(keywords):
        if isinstance(keywords, (str, bytes)):
            raise WorkflowConfigurationError(
                "keywords must be a non-empty sequence of strings"
            )
        try:
            return WordMatchingConfig(keywords=tuple(keywords), language="th")
        except (WordMatchingError, TypeError) as exc:
            raise WorkflowConfigurationError(str(exc)) from exc

    def validate_keywords(self, keywords):
        """Validate API keyword input without exposing feature configuration types."""
        return self._matching_config(keywords).keywords

    def transcribe_clip(self, clip, keywords, flow_config=None):
        """Transcribe and match one clip without configuring external forwarding."""
        matching = self._matching_config(keywords)
        try:
            return transcribe_clip_and_forward(
                clip, self._get_model(), None, matching, flow_config
            )
        except (ConfigurationError, WordMatchingError, TypeError) as exc:
            raise WorkflowConfigurationError(str(exc)) from exc
        except AudioInputError as exc:
            raise WorkflowInputError(str(exc)) from exc
        except ModelLoadError as exc:
            raise WorkflowUnavailableError(str(exc)) from exc

    @staticmethod
    def _audio_error(exc):
        message = str(exc)
        if any(
            marker in message.lower()
            for marker in ("requires sounddevice", "portaudio", "unable to start")
        ):
            return WorkflowUnavailableError(message)
        return WorkflowInputError(message)

    def start_microphone(
        self,
        device,
        keywords,
        flow_config=None,
        *,
        execution_mode="shared",
        model=None,
        runtime=None,
    ):
        """Start one microphone workflow in shared or child-process mode."""
        matching = self._matching_config(keywords)
        if execution_mode not in ("shared", "per_workflow_process"):
            raise WorkflowConfigurationError(
                "execution_mode must be 'shared' or 'per_workflow_process'"
            )
        try:
            with self._microphone_start_lock:
                if execution_mode == "shared":
                    if runtime is None:
                        model_handle = (
                            self._get_model()
                            if model is None
                            else self._get_model(model)
                        )
                    else:
                        model_handle = self._get_model_config(
                            self._effective_model_config(model, runtime)
                        )
                    return start_microphone_and_forward(
                        device, model_handle, None, matching, flow_config
                    )

                model_config = self._effective_model_config(model, runtime)
                group = start_multiprocess_microphone_flows(
                    devices=[device],
                    model_config=model_config,
                    topology="per-input-model",
                    flow_configs=[{} if flow_config is None else flow_config],
                )
                owner = _ProcessGroupOwner(group)
                with self._process_groups_lock:
                    self._process_groups.add(owner)

                def finish_group():
                    try:
                        owner.close()
                    finally:
                        with self._process_groups_lock:
                            self._process_groups.discard(owner)

                try:
                    return forward_session(
                        group.sessions[0], None, matching, on_finished=finish_group
                    )
                except Exception:
                    group.abort()
                    with self._process_groups_lock:
                        self._process_groups.discard(owner)
                    raise
        except (ConfigurationError, WordMatchingError, TypeError) as exc:
            raise WorkflowConfigurationError(str(exc)) from exc
        except ModelLoadError as exc:
            raise WorkflowUnavailableError(str(exc)) from exc
        except Empty as exc:
            raise WorkflowUnavailableError(
                "microphone process did not become ready before startup timeout"
            ) from exc
        except AudioInputError as exc:
            raise self._audio_error(exc) from exc

    def list_microphones(self):
        """List host devices through the wrapped Feature 01 callable."""
        try:
            return list_available_microphones()
        except AudioInputError as exc:
            raise WorkflowUnavailableError(str(exc)) from exc

    def list_models(self):
        """Return model choices and local readiness through the workflow boundary."""
        try:
            return {
                "default_model": self._effective_model_config()["model"],
                "default_runtime": self._effective_model_config()["runtime"],
                "models": [
                {
                    "key": item["key"],
                    "display_name": item["display_name"],
                    "installed": item["installed"],
                    "ready": any(runtime["ready"] for runtime in item["runtimes"]),
                    "runtimes": item["runtimes"],
                }
                for item in list_available_models()
                ],
            }
        except (OSError, ValueError) as exc:
            raise WorkflowUnavailableError(str(exc)) from exc

    def close(self):
        """Stop owned process groups and close the shared model handle."""
        with self._process_groups_lock:
            process_groups = tuple(self._process_groups)
        group_error = None
        for owner in process_groups:
            try:
                owner.close()
            except Exception as exc:
                group_error = group_error or exc
            finally:
                with self._process_groups_lock:
                    self._process_groups.discard(owner)
        with self._model_lock:
            handles = tuple(self._model_handles.values())
            self._model_handles.clear()
            self._model_handle = None
            close_error = None
            for handle in handles:
                try:
                    handle.close()
                except Exception as exc:
                    close_error = close_error or exc
            if close_error is not None:
                raise close_error
        if group_error is not None:
            raise group_error
