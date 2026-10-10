"""Process-local service facade for backend-owned workflow operations."""

from __future__ import annotations

import inspect
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

    def abort(self):
        with self._lock:
            if self._closed:
                return
            try:
                self.group.abort()
            finally:
                self._closed = True


class _ModelLease:
    """Idempotent reference to a cached handle used by one active operation."""

    def __init__(self, service, cache_key, handle):
        self._service = service
        self.cache_key = cache_key
        self.handle = handle
        self._lock = threading.Lock()
        self._released = False

    @property
    def released(self):
        with self._lock:
            return self._released

    def release(self):
        with self._lock:
            if self._released:
                return
            self._released = True
        self._service._release_model(self.cache_key, self.handle)


class TranscriptionService:
    """Lazily own one model handle for clip and microphone workflows."""

    def __init__(self, model_config=None):
        self._model_config = None if model_config is None else dict(model_config)
        self._model_handle = None
        self._model_handles = {}
        self._model_users = {}
        self._models_closing = 0
        self._closing_model_keys = set()
        self._model_lock = threading.RLock()
        self._model_users_changed = threading.Condition(self._model_lock)
        self._closing = False
        self._microphone_start_lock = threading.Lock()
        self._process_groups_lock = threading.Lock()
        self._process_groups = set()
        self._shared_workflows_lock = threading.Lock()
        self._shared_workflows = set()

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
            "enqueue_timeout_seconds": 30.0,
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

    def resolve_model_config(self, model=None, runtime=None):
        """Resolve effective model settings without loading a model runtime."""
        return self._effective_model_config(model, runtime)

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
        with self._model_users_changed:
            while cache_key in self._closing_model_keys:
                self._model_users_changed.wait()
            if self._closing:
                raise WorkflowUnavailableError("Transcription service is closing")
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

    def _acquire_model_config(self, config):
        """Acquire one shared model lease for an operation or microphone workflow."""
        cache_key = tuple(sorted(config.items()))
        with self._model_lock:
            handle = self._get_model_config(config)
            self._model_users[cache_key] = self._model_users.get(cache_key, 0) + 1
        return _ModelLease(self, cache_key, handle)

    def _release_model(self, cache_key, handle):
        close_handle = False
        with self._model_users_changed:
            if self._model_handles.get(cache_key) is not handle:
                return
            users = self._model_users.get(cache_key, 0)
            if users <= 1:
                self._model_users.pop(cache_key, None)
                self._model_handles.pop(cache_key, None)
                if self._model_handle is handle:
                    self._model_handle = next(iter(self._model_handles.values()), None)
                close_handle = True
                self._models_closing += 1
                self._closing_model_keys.add(cache_key)
            else:
                self._model_users[cache_key] = users - 1
            self._model_users_changed.notify_all()
        if close_handle:
            try:
                handle.close()
            finally:
                with self._model_users_changed:
                    self._models_closing -= 1
                    self._closing_model_keys.discard(cache_key)
                    self._model_users_changed.notify_all()

    @staticmethod
    def _supports_keyword(callable_, name):
        try:
            parameters = inspect.signature(callable_).parameters.values()
        except (TypeError, ValueError):
            return True
        return any(
            parameter.name == name or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )

    def _start_shared_microphone(self, device, lease, matching, flow_config):
        holder = {"workflow": None}

        def finished():
            with self._shared_workflows_lock:
                workflow = holder["workflow"]
                if workflow is not None:
                    self._shared_workflows.discard(workflow)
                lease.release()

        if self._supports_keyword(start_microphone_and_forward, "on_finished"):
            workflow = start_microphone_and_forward(
                device,
                lease.handle,
                None,
                matching,
                flow_config,
                on_finished=finished,
            )
        else:
            # Keep compatibility with injected workflow factories that predate
            # the completion callback. The watcher only owns lease release.
            workflow = start_microphone_and_forward(
                device, lease.handle, None, matching, flow_config
            )
            threading.Thread(
                target=lambda: self._wait_and_release(workflow, finished),
                name=f"model-lease-{id(workflow):x}",
                daemon=True,
            ).start()
        holder["workflow"] = workflow
        with self._shared_workflows_lock:
            if not lease.released:
                self._shared_workflows.add(workflow)
        return workflow

    @staticmethod
    def _wait_and_release(workflow, finished):
        try:
            workflow.wait()
        finally:
            finished()

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

    def transcribe_clip(
        self, clip, keywords, flow_config=None, *, measurement_callback=None
    ):
        """Transcribe and match one clip without configuring external forwarding."""
        matching = self._matching_config(keywords)
        try:
            lease = self._acquire_model_config(self._effective_model_config())
            return transcribe_clip_and_forward(
                clip,
                lease.handle,
                None,
                matching,
                flow_config,
                measurement_callback=measurement_callback,
            )
        except (ConfigurationError, WordMatchingError, TypeError) as exc:
            raise WorkflowConfigurationError(str(exc)) from exc
        except AudioInputError as exc:
            raise WorkflowInputError(str(exc)) from exc
        except ModelLoadError as exc:
            raise WorkflowUnavailableError(str(exc)) from exc
        finally:
            if "lease" in locals():
                lease.release()

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
                    lease = self._acquire_model_config(
                        self._effective_model_config(model, runtime)
                    )
                    try:
                        return self._start_shared_microphone(
                            device, lease, matching, flow_config
                        )
                    except Exception:
                        lease.release()
                        raise

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

                def abort_group():
                    try:
                        owner.abort()
                    finally:
                        with self._process_groups_lock:
                            self._process_groups.discard(owner)

                try:
                    return forward_session(
                        group.sessions[0],
                        None,
                        matching,
                        on_finished=finish_group,
                        on_stop_error=abort_group,
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
        """Stop owned workflows and close cached shared model handles."""
        with self._microphone_start_lock:
            with self._model_lock:
                self._closing = True
            with self._shared_workflows_lock:
                shared_workflows = tuple(self._shared_workflows)
        for workflow in shared_workflows:
            try:
                workflow.stop()
            except Exception:
                pass
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
        with self._model_users_changed:
            while any(self._model_users.values()) or self._models_closing:
                self._model_users_changed.wait()
            handles = tuple(self._model_handles.values())
            self._model_handles.clear()
            self._model_users.clear()
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
