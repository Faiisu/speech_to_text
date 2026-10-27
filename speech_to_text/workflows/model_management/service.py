"""Bounded process-local jobs for model inspection and preparation."""

from __future__ import annotations

import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone


class ModelManagementInputError(ValueError):
    """A model-management request is invalid or unsupported."""


class ModelManagementUnavailableError(RuntimeError):
    """Model-management dependencies or storage are unavailable."""


class ModelManagementConflictError(RuntimeError):
    """A model-preparation job is already active."""


class ModelManagementNotFoundError(LookupError):
    """No locally managed artifacts or registry entry exist for the model key."""


def _timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class _Job:
    job_id: str
    repository: str
    model_key: str
    revision: str
    operation: str
    runtime: str
    precision: str
    created_at: str = field(default_factory=_timestamp)
    updated_at: str = field(default_factory=_timestamp)
    status: str = "queued"
    phase: str = "queued"
    error: str | None = None
    result: dict | None = None
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def snapshot(self):
        with self.lock:
            return {
                "job_id": self.job_id,
                "status": self.status,
                "phase": self.phase,
                "repository": self.repository,
                "model_key": self.model_key,
                "revision": self.revision,
                "operation": self.operation,
                "runtime": self.runtime,
                "precision": self.precision,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "error": self.error,
                "result": deepcopy(self.result),
            }


class ModelManagementService:
    """Run one preparation job at a time and retain bounded job history."""

    def __init__(
        self,
        *,
        max_history=32,
        inspector=None,
        preparer=None,
        deletion_planner=None,
        deleter=None,
    ):
        if not isinstance(max_history, int) or isinstance(max_history, bool) or max_history < 1:
            raise ValueError("max_history must be a positive integer")
        self.max_history = max_history
        self._inspector = inspector
        self._preparer = preparer
        self._deletion_planner = deletion_planner
        self._deleter = deleter
        self._lock = threading.RLock()
        self._jobs: dict[str, _Job] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="model-management")
        self._closed = False

    def inspect(self, repository, revision="main", token=None):
        self._validate_identity(repository, revision)
        try:
            inspector = self._get_inspector()
            return inspector(repository, revision=revision, token=token)
        except ValueError as exc:
            raise ModelManagementInputError(_safe_error(exc, token)) from exc
        except RuntimeError as exc:
            raise ModelManagementUnavailableError(_safe_error(exc, token)) from exc
        except Exception as exc:
            raise ModelManagementUnavailableError("Model inspection is unavailable") from exc

    def start(self, *, repository, model_key, revision="main", token=None,
              operation="convert", runtime="ctranslate2", precision=None):
        self._validate(repository, model_key, revision, operation, runtime, precision)
        runtime = runtime or "ctranslate2"
        precision = precision or ("bf16" if runtime == "transformers" else "int8" if runtime == "ctranslate2" else "source")
        with self._lock:
            if self._closed:
                raise ModelManagementUnavailableError("Model management is shutting down")
            if any(job.status in {"queued", "running"} for job in self._jobs.values()):
                raise ModelManagementConflictError("A model preparation job is already active")
            job = _Job(
                job_id=uuid.uuid4().hex,
                repository=repository,
                model_key=model_key,
                revision=revision,
                operation=operation,
                runtime=runtime,
                precision=precision,
            )
            self._jobs[job.job_id] = job
            self._trim_history()
            self._executor.submit(self._run, job, token)
            return job.snapshot()

    def list_jobs(self):
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda job: (job.created_at, job.job_id), reverse=True)
            return [job.snapshot() for job in jobs]

    def get_job(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
        return None if job is None else job.snapshot()

    def preview_deletion(self, model_key):
        self._validate_model_key(model_key)
        try:
            planner = self._get_deletion_planner()
            with self._lock:
                plan = planner(model_key)
                self._require_local_assets(plan)
                reason = self._active_job_reason()
            return self._with_blocker(plan, reason)
        except ValueError as exc:
            raise ModelManagementInputError(str(exc)) from exc
        except RuntimeError as exc:
            raise ModelManagementUnavailableError("Model storage is unavailable") from exc
        except ModelManagementNotFoundError:
            raise
        except ModelManagementInputError:
            raise
        except Exception as exc:
            raise ModelManagementUnavailableError("Model storage is unavailable") from exc

    def delete_local_model(self, model_key):
        self._validate_model_key(model_key)
        try:
            with self._lock:
                reason = self._active_job_reason()
                if reason:
                    raise ModelManagementConflictError(reason)
                plan = self._get_deletion_planner()(model_key)
                self._require_local_assets(plan)
                deleted = self._get_deleter()(model_key)
                if not isinstance(deleted, dict) or deleted.get("deleted") is not True:
                    raise ModelManagementUnavailableError("Model deletion did not complete")
            return deleted
        except ModelManagementConflictError:
            raise
        except ModelManagementNotFoundError:
            raise
        except ValueError as exc:
            raise ModelManagementInputError(str(exc)) from exc
        except RuntimeError as exc:
            raise ModelManagementUnavailableError("Model storage is unavailable") from exc
        except Exception as exc:
            raise ModelManagementUnavailableError("Model storage is unavailable") from exc

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            queued = [job for job in self._jobs.values() if job.status == "queued"]
        for job in queued:
            self._finish(job, status="failed", phase="interrupted", error="Model preparation stopped during shutdown")
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _get_inspector(self):
        if self._inspector is None:
            from speech_to_text.features.model_deployment import inspect_huggingface_model
            self._inspector = inspect_huggingface_model
        return self._inspector

    def _get_preparer(self):
        if self._preparer is None:
            from speech_to_text.features.model_deployment import prepare_huggingface_model
            self._preparer = prepare_huggingface_model
        return self._preparer

    def _get_deletion_planner(self):
        if self._deletion_planner is None:
            from speech_to_text.features.model_deployment import plan_local_model_deletion

            self._deletion_planner = plan_local_model_deletion
        return self._deletion_planner

    def _get_deleter(self):
        if self._deleter is None:
            from speech_to_text.features.model_deployment import delete_local_model

            self._deleter = delete_local_model
        return self._deleter

    def _active_job_reason(self):
        if any(job.status in {"queued", "running"} for job in self._jobs.values()):
            return "A model preparation job is queued or running."
        return None

    @staticmethod
    def _require_local_assets(plan):
        if not plan.get("files") and not plan.get("registered"):
            raise ModelManagementNotFoundError("No local model assets or registry entry found")

    @staticmethod
    def _with_blocker(plan, blocker):
        reason = "; ".join(item for item in (plan.get("reason"), blocker) if item)
        return {**plan, "deletable": bool(plan.get("deletable")) and not blocker, "reason": reason or None}

    def _run(self, job, token):
        with job.lock:
            if job.status != "queued":
                return
            job.status = "running"
            job.phase = "starting"
            job.updated_at = _timestamp()
        try:
            result = self._get_preparer()(
                job.repository,
                job.model_key,
                revision=job.revision,
                token=token,
                operation=job.operation,
                runtime=job.runtime,
                precision=job.precision,
                progress=lambda phase: self._set_phase(job, phase),
            )
        except ValueError as exc:
            self._finish(job, status="failed", phase="failed", error=_safe_error(exc, token))
        except RuntimeError as exc:
            self._finish(job, status="failed", phase="failed", error=_safe_error(exc, token))
        except Exception as exc:
            self._finish(job, status="failed", phase="failed", error="Model preparation failed")
        else:
            self._finish(job, status="completed", phase="completed", result=result)

    def _set_phase(self, job, phase):
        with job.lock:
            if job.status == "running":
                job.phase = str(phase)[:120]
                job.updated_at = _timestamp()

    def _finish(self, job, *, status, phase, error=None, result=None):
        with job.lock:
            job.status = status
            job.phase = phase
            job.error = error
            job.result = deepcopy(result)
            job.updated_at = _timestamp()
        with self._lock:
            self._trim_history()

    def _trim_history(self):
        terminal = [job for job in self._jobs.values() if job.status in {"completed", "failed"}]
        terminal.sort(key=lambda job: (job.created_at, job.job_id), reverse=True)
        for job in terminal[self.max_history:]:
            self._jobs.pop(job.job_id, None)

    @staticmethod
    def _validate(repository, model_key, revision, operation, runtime, precision):
        ModelManagementService._validate_identity(repository, revision)
        ModelManagementService._validate_model_key(model_key)
        if operation not in {"download", "convert"}:
            raise ModelManagementInputError("operation must be download or convert")
        if runtime not in {None, "ctranslate2", "openvino-cpu", "openvino-gpu", "transformers"}:
            raise ModelManagementInputError("runtime must be ctranslate2, openvino-cpu, openvino-gpu, or transformers")
        allowed = {
            "ctranslate2": {None, "int8", "float32"},
            "openvino-cpu": {None, "source", "bf16", "int8", "int4"},
            "openvino-gpu": {None, "source", "bf16", "int8", "int4"},
            "transformers": {None, "bf16", "float32"},
        }
        selected_runtime = runtime or "ctranslate2"
        if precision not in allowed[selected_runtime]:
            raise ModelManagementInputError("precision is not supported for the selected runtime")

    @staticmethod
    def _validate_model_key(model_key):
        if (
            not isinstance(model_key, str)
            or len(model_key) > 64
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", model_key)
            or ".." in model_key
        ):
            raise ModelManagementInputError("model_key must be a safe local model key")

    @staticmethod
    def _validate_identity(repository, revision):
        if not isinstance(repository, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}",
            repository,
        ):
            raise ModelManagementInputError("repository must be a Hugging Face owner/name ID")
        if (
            not isinstance(revision, str)
            or not revision.strip()
            or len(revision) > 256
            or "://" in revision
            or any(ord(char) < 32 or ord(char) == 127 for char in revision)
        ):
            raise ModelManagementInputError("revision must be a valid Hugging Face revision")


def _safe_error(exc, token):
    message = str(exc).strip() or "Model operation failed"
    if token:
        message = message.replace(token, "[redacted]")
    # Authenticated Hugging Face URLs can contain credentials in query parameters.
    message = re.sub(r"https?://\S+", "remote resource", message)
    return message[:500]
