"""Process-local service facade for backend-owned workflow operations."""

from __future__ import annotations

import inspect
import multiprocessing as mp
import os
import threading
from queue import Empty

from speech_to_text.features.model_deployment import (
    AudioInputError,
    ConfigurationError,
    ModelLoadError,
    RTSPCaptureUnavailableError,
    VADUnavailableError,
    list_available_models,
    load_model,
    normalize_decoding_options,
    normalize_vad_options as normalize_feature_vad_options,
    start_multiprocess_microphone_flows,
    create_rtsp_secret_source_factory,
    normalize_rtsp_source,
    RTSPSourceConfiguration,
)
from speech_to_text.features.word_matching import (
    KeywordMatch,
    WordMatchingConfig,
    WordMatchingError,
)

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


def _isolated_clip_worker(
    result_queue,
    clip,
    keywords,
    flow_config,
    model_config,
    audio_preprocessor,
    runtime_factory,
):
    service = TranscriptionService(
        model_config=model_config, runtime_factory=runtime_factory
    )

    def record_measurement(measurement):
        result_queue.put(("measurement", measurement))

    try:
        result = service.transcribe_clip(
            clip,
            keywords,
            flow_config,
            measurement_callback=record_measurement,
            audio_preprocessor=audio_preprocessor,
        )
        outcome = {
            "ok": True,
            "transcript": result.transcript,
            "matches": [
                {"keyword": item.keyword, "count": item.count}
                for item in result.matches
            ],
            "source_id": result.source_id,
            "latest_rtf": result.latest_rtf,
        }
    except Exception as exc:
        outcome = {"ok": False, "error": (type(exc).__name__, str(exc))}
    finally:
        try:
            service.close()
        except Exception as exc:
            if "outcome" not in locals() or outcome.get("ok"):
                outcome = {"ok": False, "error": (type(exc).__name__, str(exc))}
    result_queue.put(("result", outcome))


def prepare_rtsp_configuration(rtsp_url=None, rtsp_url_secret_ref=None, rtsp_username=None, rtsp_password=None):
    """Validate and normalize a direct URL or approved reference for one operation."""
    if (rtsp_url is None) == (rtsp_url_secret_ref is None):
        raise WorkflowConfigurationError("Select exactly one RTSP URL or secret reference")
    if rtsp_url_secret_ref is not None:
        if rtsp_username is not None or rtsp_password not in (None, ""):
            raise WorkflowConfigurationError("Credential fields cannot be combined with an RTSP secret reference")
        try:
            factory = create_rtsp_secret_source_factory(rtsp_url_secret_ref)
            return RTSPSourceConfiguration(None, None, False, factory, None, rtsp_url_secret_ref)
        except ConfigurationError as exc:
            raise WorkflowConfigurationError(str(exc)) from exc
        except AudioInputError as exc:
            raise WorkflowUnavailableError(str(exc)) from exc
    try:
        config = normalize_rtsp_source(rtsp_url, rtsp_username, rtsp_password)
        return config
    except ConfigurationError as exc:
        raise WorkflowConfigurationError(str(exc)) from exc


def prepare_rtsp_source(rtsp_url=None, rtsp_url_secret_ref=None, rtsp_username=None, rtsp_password=None):
    """Validate RTSP source configuration and prepare its start-time snapshot."""
    if (rtsp_url is None) == (rtsp_url_secret_ref is None):
        raise WorkflowConfigurationError(
            "Select exactly one RTSP URL or secret reference"
        )
    try:
        return prepare_rtsp_configuration(rtsp_url, rtsp_url_secret_ref, rtsp_username, rtsp_password).audio_source_factory
    except ConfigurationError as exc:
        raise WorkflowConfigurationError(str(exc)) from exc
    except AudioInputError as exc:
        raise WorkflowUnavailableError(str(exc)) from exc


def normalize_profile_decoding_options(options=None):
    """Validate and default saved profile decoding settings at the workflow boundary."""
    try:
        return normalize_decoding_options(options)
    except ConfigurationError as exc:
        raise WorkflowConfigurationError(str(exc)) from exc


def normalize_vad_options(value=None):
    """Validate and complete VAD settings at the workflow boundary."""
    try:
        return normalize_feature_vad_options(value)
    except ConfigurationError as exc:
        raise WorkflowConfigurationError(str(exc)) from exc


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

    def __init__(self, model_config=None, *, runtime_factory=None):
        self._model_config = None if model_config is None else dict(model_config)
        self._runtime_factory = runtime_factory
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
        self._isolated_processes = set()
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
                    handle = (
                        load_model(config)
                        if self._runtime_factory is None
                        else load_model(config, runtime_factory=self._runtime_factory)
                    )
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

    def _start_shared_microphone(
        self,
        device,
        lease,
        matching,
        flow_config,
        forwarder=None,
        audio_source_factory=None,
        audio_preprocessor=None,
    ):
        holder = {"workflow": None}

        def finished():
            with self._shared_workflows_lock:
                workflow = holder["workflow"]
                if workflow is not None:
                    self._shared_workflows.discard(workflow)
                lease.release()

        source_options = {}
        if audio_source_factory is not None:
            if not self._supports_keyword(start_microphone_and_forward, "audio_source_factory"):
                raise WorkflowUnavailableError(
                    "The configured workflow adapter cannot start an RTSP audio source"
                )
            source_options["audio_source_factory"] = audio_source_factory
        if audio_preprocessor is not None:
            if not self._supports_keyword(start_microphone_and_forward, "audio_preprocessor"):
                raise WorkflowUnavailableError(
                    "The configured workflow adapter cannot apply audio preprocessing"
                )
            source_options["audio_preprocessor"] = audio_preprocessor

        if self._supports_keyword(start_microphone_and_forward, "on_finished"):
            workflow = start_microphone_and_forward(
                device,
                lease.handle,
                forwarder,
                matching,
                flow_config,
                on_finished=finished,
                **source_options,
            )
        else:
            # Keep compatibility with injected workflow factories that predate
            # the completion callback. The watcher only owns lease release.
            workflow = start_microphone_and_forward(
                device,
                lease.handle,
                forwarder,
                matching,
                flow_config,
                **source_options,
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
    def _matching_config(keywords, language="th"):
        if isinstance(keywords, (str, bytes)):
            raise WorkflowConfigurationError(
                "keywords must be a non-empty sequence of strings"
            )
        try:
            return WordMatchingConfig(keywords=tuple(keywords), language=language)
        except (WordMatchingError, TypeError) as exc:
            raise WorkflowConfigurationError(str(exc)) from exc

    def validate_keywords(self, keywords, language="th"):
        """Validate API keyword input without exposing feature configuration types."""
        return self._matching_config(keywords, language).keywords

    def validate_decoding_options(self, options=None):
        """Validate saved profile decoding settings using Feature 01 rules."""
        return normalize_profile_decoding_options(options)

    def transcribe_clip(
        self,
        clip,
        keywords,
        flow_config=None,
        *,
        model=None,
        runtime=None,
        measurement_callback=None,
        forwarder=None,
        audio_preprocessor=None,
        execution_mode="shared",
    ):
        """Transcribe and match one clip, with optional configured output."""
        flow_config = {} if flow_config is None else dict(flow_config)
        flow_config["vad"] = normalize_vad_options(flow_config.get("vad"))
        matching = self._matching_config(keywords, flow_config.get("language", "th"))
        if execution_mode not in ("shared", "per_workflow_process"):
            raise WorkflowConfigurationError(
                "execution_mode must be 'shared' or 'per_workflow_process'"
            )
        if execution_mode == "per_workflow_process":
            if forwarder is not None:
                raise WorkflowConfigurationError(
                    "per_workflow_process clip transcription does not support output destinations"
                )
            return self._transcribe_clip_in_process(
                clip, matching, flow_config, model, runtime, audio_preprocessor,
                measurement_callback,
            )
        try:
            lease = self._acquire_model_config(
                self._effective_model_config(model, runtime)
            )
            return transcribe_clip_and_forward(
                clip,
                lease.handle,
                forwarder,
                matching,
                flow_config,
                measurement_callback=measurement_callback,
                **(
                    {"audio_preprocessor": audio_preprocessor}
                    if audio_preprocessor is not None
                    else {}
                ),
            )
        except (ConfigurationError, WordMatchingError, TypeError) as exc:
            raise WorkflowConfigurationError(str(exc)) from exc
        except AudioInputError as exc:
            raise WorkflowInputError(str(exc)) from exc
        except VADUnavailableError as exc:
            raise WorkflowUnavailableError(str(exc)) from exc
        except ModelLoadError as exc:
            raise WorkflowUnavailableError(str(exc)) from exc
        finally:
            if "lease" in locals():
                lease.release()

    def _transcribe_clip_in_process(
        self, clip, matching, flow_config, model, runtime, audio_preprocessor,
        measurement_callback,
    ):
        from .api import PipelineResult

        context = mp.get_context("spawn")
        with self._model_users_changed:
            if self._closing:
                raise WorkflowUnavailableError("Transcription service is closing")
            result_queue = context.Queue()
            process = context.Process(
                target=_isolated_clip_worker,
                args=(
                    result_queue,
                    clip,
                    matching.keywords,
                    flow_config,
                    self._effective_model_config(model, runtime),
                    audio_preprocessor,
                    self._runtime_factory,
                ),
                name=f"stt-clip-{flow_config.get('source_id', 'trial')}",
            )
            self._isolated_processes.add(process)
        try:
            process.start()
            outcome = None
            while outcome is None:
                try:
                    message_type, message = result_queue.get(timeout=0.1)
                except Empty:
                    if not process.is_alive():
                        break
                    continue
                if message_type == "measurement":
                    if measurement_callback is not None:
                        measurement_callback(message)
                else:
                    outcome = message
            process.join()
            if process.exitcode != 0:
                raise WorkflowUnavailableError(
                    f"Isolated transcription process exited with code {process.exitcode}"
                )
            if outcome is None:
                raise WorkflowUnavailableError(
                    "Isolated transcription process exited without a result"
                )
            if not outcome["ok"]:
                error_type, message = outcome["error"]
                error_class = {
                    "WorkflowConfigurationError": WorkflowConfigurationError,
                    "WorkflowInputError": WorkflowInputError,
                    "WorkflowUnavailableError": WorkflowUnavailableError,
                }.get(error_type, WorkflowUnavailableError)
                raise error_class(message)
            matches = tuple(KeywordMatch(**item) for item in outcome["matches"])
            return PipelineResult(
                outcome["transcript"],
                matches,
                outcome["source_id"],
                None,
                None,
                outcome["latest_rtf"],
            )
        finally:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join()
                process.close()
            result_queue.close()
            result_queue.join_thread()
            with self._model_users_changed:
                self._isolated_processes.discard(process)
                self._model_users_changed.notify_all()

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
        forwarder=None,
        audio_source_factory=None,
        audio_preprocessor=None,
    ):
        """Start one microphone workflow in shared or child-process mode."""
        flow_config = {} if flow_config is None else dict(flow_config)
        flow_config["vad"] = normalize_vad_options(flow_config.get("vad"))
        matching = self._matching_config(keywords, flow_config.get("language", "th"))
        if execution_mode not in ("shared", "per_workflow_process"):
            raise WorkflowConfigurationError(
                "execution_mode must be 'shared' or 'per_workflow_process'"
            )
        try:
            if audio_source_factory is not None:
                preflight = getattr(audio_source_factory, "preflight", None)
                if callable(preflight):
                    preflight()
            with self._microphone_start_lock:
                if execution_mode == "shared":
                    lease = self._acquire_model_config(
                        self._effective_model_config(model, runtime)
                    )
                    try:
                        return self._start_shared_microphone(
                            device,
                            lease,
                            matching,
                            flow_config,
                            forwarder,
                            audio_source_factory,
                            audio_preprocessor,
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
                    **(
                        {"audio_source_factory": audio_source_factory}
                        if audio_source_factory is not None
                        else {}
                    ),
                    **(
                        {"audio_preprocessor": audio_preprocessor}
                        if audio_preprocessor is not None
                        else {}
                    ),
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
                        forwarder,
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
        except RTSPCaptureUnavailableError as exc:
            raise WorkflowUnavailableError(str(exc)) from exc
        except AudioInputError as exc:
            if audio_source_factory is not None:
                raise WorkflowUnavailableError(str(exc)) from exc
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
            default_config = self._effective_model_config()
            return {
                "default_model": default_config["model"],
                "default_runtime": default_config["runtime"],
                "default_precision": default_config["precision"],
                "models": [
                {
                    "key": item["key"],
                    "display_name": item["display_name"],
                    "repository": item.get("repository"),
                    "revision": item.get("revision"),
                    "installed": item["installed"],
                    "ready": any(runtime["ready"] for runtime in item["runtimes"]),
                    "runtimes": [
                        {
                            **runtime,
                            "default_precision": self._effective_model_config(
                                runtime=runtime["key"]
                            )["precision"],
                        }
                        for runtime in item["runtimes"]
                    ],
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
            while (
                any(self._model_users.values())
                or self._models_closing
                or self._isolated_processes
            ):
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
