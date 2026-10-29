"""Hugging Face Whisper inspection, conversion, and safe local publication."""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from .catalog import BUILTINS, _asset_paths
from .config import flow_config

_MODEL_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_PRECISION_OPTIONS = {
    "openvino-cpu": ["source", "bf16", "int8", "int4"],
    "openvino-gpu": ["source", "bf16", "int8", "int4"],
    "ctranslate2": ["int8", "float32"],
    "transformers": ["bf16", "float32"],
}
_SOURCE_FILES = [
    "config.json",
    "generation_config.json",
    "preprocessor_config.json",
    "processor_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "added_tokens.json",
    "vocab.json",
    "merges.txt",
    "normalizer.json",
    "model.safetensors",
    "model.safetensors.index.json",
    "*.safetensors",
]
_GEMMA_SOURCE_FILES = [
    "config.json", "generation_config.json", "processor_config.json",
    "preprocessor_config.json", "tokenizer.json", "tokenizer_config.json",
    "special_tokens_map.json", "added_tokens.json", "vocab.json", "merges.txt",
    "tokenizer.model", "*.model", "*.safetensors", "model.safetensors.index.json",
    "*.safetensors.index.json", "*.json",
]


class ModelPreparationError(ValueError):
    """The request or source checkpoint is invalid or unsupported."""


class ModelPreparationUnavailableError(RuntimeError):
    """A required remote service, dependency, device, or storage resource is unavailable."""


def _check_repository(repository, revision):
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        raise ModelPreparationError("repository must be a Hugging Face owner/name")
    if not isinstance(revision, str) or not revision or len(revision) > 256:
        raise ModelPreparationError("revision must be a non-empty string of at most 256 characters")


def _hub():
    try:
        from huggingface_hub import HfApi, snapshot_download
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise ModelPreparationUnavailableError(
            "Hugging Face support requires huggingface-hub; install the model-management dependencies"
        ) from exc
    return HfApi, hf_hub_download, snapshot_download


def _config(repository, revision, token):
    HfApi, hf_hub_download, _ = _hub()
    try:
        info = HfApi().model_info(
            repo_id=repository, revision=revision, token=token, files_metadata=False
        )
        sha = getattr(info, "sha", None)
        files = {getattr(item, "rfilename", "") for item in (getattr(info, "siblings", None) or ())}
        if not isinstance(sha, str) or not sha:
            raise ValueError("missing commit")
        path = hf_hub_download(
            repo_id=repository, filename="config.json", revision=sha, token=token
        )
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        raise ModelPreparationUnavailableError(
            "Unable to inspect the Hugging Face repository. Check its ID, revision, access permissions, and token."
        ) from None
    if not isinstance(config, dict):
        raise ModelPreparationError("The repository config is not a JSON object")
    return sha, config, files


def _whisper_reasons(config, files=None):
    reasons = []
    if config.get("model_type") != "whisper":
        reasons.append("Checkpoint architecture is not Transformers Whisper")
    architectures = config.get("architectures")
    if architectures and "WhisperForConditionalGeneration" not in architectures:
        reasons.append("Checkpoint architecture is not supported Transformers Whisper")
    if config.get("is_encoder_decoder") is False:
        reasons.append("Checkpoint is not an encoder-decoder speech model")
    if files is not None:
        if not any(name.endswith(".safetensors") for name in files):
            reasons.append("Checkpoint does not provide safetensors weights")
        if "preprocessor_config.json" not in files:
            reasons.append("Checkpoint is missing preprocessor_config.json")
        if not (
            "tokenizer.json" in files
            or {"tokenizer_config.json", "vocab.json", "merges.txt"}.issubset(files)
        ):
            reasons.append("Checkpoint is missing supported Whisper tokenizer files")
    return reasons


def _gemma_reasons(config, files=None):
    reasons = []
    if config.get("model_type") != "gemma4" or "Gemma4ForConditionalGeneration" not in (config.get("architectures") or []):
        reasons.append("Checkpoint is not a Transformers Gemma 4 conditional-generation model")
    if not isinstance(config.get("audio_config"), dict):
        reasons.append("Checkpoint does not include Gemma 4 audio support")
    if files is not None:
        if not any(name.endswith(".safetensors") for name in files):
            reasons.append("Checkpoint does not provide safetensors weights")
        if not ({"processor_config.json", "preprocessor_config.json", "feature_extractor_config.json"} & files):
            reasons.append("Checkpoint is missing its audio processor configuration")
        if not ("tokenizer.json" in files or "tokenizer.model" in files):
            reasons.append("Checkpoint is missing supported tokenizer files")
    return reasons


def _module(name):
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def _runtime_status(runtime):
    if runtime == "transformers":
        ready = all(_module(name) for name in ("transformers", "torch", "accelerate", "librosa", "torchvision"))
        if ready:
            try:
                from importlib.metadata import version
                from transformers import AutoModelForMultimodalLM  # noqa: F401
                transformers_version = tuple(
                    int(part) for part in version("transformers").split(".")[:3]
                )
                torch_version = tuple(int(part) for part in version("torch").split(".")[:2])
                ready = transformers_version >= (5, 10, 1) and torch_version >= (2, 4)
            except Exception:
                ready = False
        reason = None if ready else "Install transformers>=5.10.1, torch>=2.4, accelerate, librosa, and torchvision to use Gemma 4"
        return {"key": runtime, "available": ready, "precision_options": _PRECISION_OPTIONS[runtime], "reason": reason}
    if runtime == "ctranslate2":
        ready = all(_module(name) for name in ("ctranslate2", "ctranslate2.converters", "faster_whisper", "transformers", "torch", "safetensors"))
        if ready:
            try:
                from ctranslate2.converters import TransformersConverter  # noqa: F401
                from faster_whisper import WhisperModel  # noqa: F401
            except ImportError:
                ready = False
        reason = None if ready else "Install ctranslate2, faster-whisper, and transformers to use this runtime"
        return {"key": runtime, "available": ready, "precision_options": _PRECISION_OPTIONS[runtime], "reason": reason}
    if runtime in ("openvino-cpu", "openvino-gpu"):
        ready = all(_module(name) for name in ("openvino", "optimum.intel", "transformers", "torch", "safetensors"))
        device_ready = False
        if ready:
            try:
                from openvino import Core
                from optimum.intel import OVModelForSpeechSeq2Seq  # noqa: F401
                from transformers import AutoProcessor  # noqa: F401

                devices = Core().available_devices
                device_ready = (
                    any(device == "CPU" for device in devices)
                    if runtime == "openvino-cpu"
                    else any(device.startswith("GPU") for device in devices)
                )
            except Exception:
                device_ready = False
        ready = ready and device_ready
        reason = None if ready else f"Install openvino and optimum-intel and make the {runtime.removeprefix('openvino-').upper()} device available"
        return {"key": runtime, "available": ready, "precision_options": _PRECISION_OPTIONS[runtime], "reason": reason}
    raise ModelPreparationError("runtime must be 'openvino-cpu', 'openvino-gpu', or 'ctranslate2'")


def inspect_huggingface_model(repository, revision="main", token=None) -> dict:
    """Inspect one remote checkpoint without downloading model weights."""
    _check_repository(repository, revision)
    if token is not None and not isinstance(token, str):
        raise ModelPreparationError("token must be a string")
    sha, config, files = _config(repository, revision, token)
    is_gemma = repository == "google/gemma-4-E4B-it"
    reasons = _gemma_reasons(config, files) if is_gemma else _whisper_reasons(config, files)
    runtimes = (
        [_runtime_status("transformers")]
        if is_gemma
        else [_runtime_status("openvino-cpu"), _runtime_status("openvino-gpu"), _runtime_status("ctranslate2")]
    )
    return {
        "repository": repository,
        "revision": sha,
        "compatible": not reasons,
        "reasons": reasons,
        "model_type": config.get("model_type"),
        "runtimes": runtimes,
    }


def _models_root(models_root):
    if models_root is not None:
        root = Path(models_root)
    else:
        configured = os.environ.get("SPEECH_TO_TEXT_MODELS_DIR")
        root = Path(configured) if configured else Path.cwd() / "models"
    if root.exists() and root.is_symlink():
        raise ModelPreparationError("models_root must not be a symlink")
    return root


def _read_registry(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelPreparationUnavailableError("Unable to read the local model registry") from exc
    if not isinstance(data, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in data.items()):
        raise ModelPreparationError("The local model registry must map model keys to repository IDs")
    return data


def _metadata_matches(path, repository, sha, runtime=None, precision=None):
    marker = path / ".model-deployment.json"
    try:
        metadata = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        metadata.get("repository") == repository
        and metadata.get("revision") == sha
        and (runtime is None or metadata.get("runtime") == runtime)
        and (precision is None or metadata.get("precision") == precision)
    )


def _download_source(repository, sha, token, destination):
    _, _, snapshot_download = _hub()
    try:
        allow_patterns = (
            _GEMMA_SOURCE_FILES
            if repository == "google/gemma-4-E4B-it"
            else _SOURCE_FILES
        )
        snapshot = Path(snapshot_download(
            repo_id=repository,
            revision=sha,
            token=token,
            allow_patterns=allow_patterns,
            local_files_only=False,
        ))
        config = json.loads((snapshot / "config.json").read_text(encoding="utf-8"))
        files = {path.relative_to(snapshot).as_posix() for path in snapshot.rglob("*") if path.is_file()}
        is_gemma = repository == "google/gemma-4-E4B-it"
        reasons = _gemma_reasons(config, files) if is_gemma else _whisper_reasons(config, files)
        if reasons:
            raise ModelPreparationError("Downloaded source is unsupported: " + "; ".join(reasons))
        destination.mkdir(parents=True)
        for source_file in snapshot.rglob("*"):
            if not source_file.is_file():
                continue
            relative = source_file.relative_to(snapshot)
            allowed_files = _GEMMA_SOURCE_FILES if is_gemma else _SOURCE_FILES
            if not any(relative.match(pattern) for pattern in allowed_files):
                continue
            target_file = destination / relative
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, target_file)
        (destination / ".model-deployment.json").write_text(
            json.dumps({"repository": repository, "revision": sha}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except ModelPreparationError:
        raise
    except Exception:
        raise ModelPreparationUnavailableError(
            "Unable to download the pinned model safetensors snapshot. Check repository access and available storage."
        ) from None


def _convert(source, destination, runtime, precision):
    try:
        if runtime == "ctranslate2":
            from ctranslate2.converters import TransformersConverter
            from transformers import AutoTokenizer

            converter = TransformersConverter(
                str(source),
                trust_remote_code=False,
                copy_files=[
                    name
                    for name in ("tokenizer.json", "preprocessor_config.json", "tokenizer_config.json", "vocab.json", "merges.txt")
                    if (source / name).is_file()
                ],
            )
            converter.convert(str(destination), quantization=precision, force=True)
            if not (destination / "tokenizer.json").is_file():
                tokenizer = AutoTokenizer.from_pretrained(
                    str(source), local_files_only=True, trust_remote_code=False, use_fast=True
                )
                tokenizer.save_pretrained(destination)
            if not (destination / "preprocessor_config.json").is_file():
                shutil.copyfile(source / "preprocessor_config.json", destination / "preprocessor_config.json")
            return
        from optimum.intel import OVModelForSpeechSeq2Seq, OVWeightQuantizationConfig
        from transformers import AutoProcessor

        destination.mkdir(parents=True, exist_ok=True)
        kwargs = {"export": True, "device": "CPU", "local_files_only": True, "trust_remote_code": False, "use_safetensors": True}
        if precision == "int8":
            kwargs["quantization_config"] = OVWeightQuantizationConfig(bits=8)
        elif precision == "int4":
            kwargs["quantization_config"] = OVWeightQuantizationConfig(bits=4)
        elif precision == "bf16":
            import torch

            kwargs["torch_dtype"] = torch.bfloat16
        else:
            kwargs["load_in_8bit"] = False
        model = OVModelForSpeechSeq2Seq.from_pretrained(str(source), **kwargs)
        processor = AutoProcessor.from_pretrained(str(source), local_files_only=True, trust_remote_code=False)
        model.save_pretrained(destination)
        processor.save_pretrained(destination)
    except ImportError as exc:
        raise ModelPreparationUnavailableError(f"The {runtime} conversion dependencies are not installed") from exc
    except OSError:
        raise ModelPreparationUnavailableError(
            f"Unable to write {runtime} conversion files. Check that the models directory is writable and has free space."
        ) from None
    except Exception as exc:
        raise ModelPreparationUnavailableError(f"Unable to convert the Whisper checkpoint for {runtime}") from exc


def _verify(path, model_key, runtime, precision):
    from .runtime import create_runtime

    config = {"model": model_key, "runtime": runtime, "precision": precision}
    impl = None
    try:
        impl = create_runtime(config, artifact_path=path)
        options = flow_config({})["decoding_options"]
        impl.transcribe([0.0] * 16000, language="en", decoding_options=options)
    except Exception as exc:
        detail = str(exc).strip() or "no further error details were provided"
        raise ModelPreparationUnavailableError(
            f"The converted {runtime} model failed its local inference smoke test "
            f"({type(exc).__name__}: {detail})"
        ) from exc
    finally:
        close = getattr(impl, "close", None)
        if close:
            close()


def prepare_huggingface_model(
    repository,
    model_key,
    revision="main",
    token=None,
    operation="convert",
    runtime="ctranslate2",
    precision=None,
    progress=None,
    models_root=None,
) -> dict:
    """Download a pinned source checkpoint and optionally convert and verify it."""
    _check_repository(repository, revision)
    if (
        not isinstance(model_key, str)
        or not _MODEL_KEY.fullmatch(model_key)
        or ".." in model_key
    ):
        raise ModelPreparationError("model_key must be a safe local key of at most 64 characters without '..'")
    if token is not None and not isinstance(token, str):
        raise ModelPreparationError("token must be a string")
    if progress is not None and not callable(progress):
        raise ModelPreparationError("progress must be callable")
    if operation not in ("download", "convert"):
        raise ModelPreparationError("operation must be 'download' or 'convert'")
    if runtime not in ("openvino-cpu", "openvino-gpu", "ctranslate2", "transformers"):
        raise ModelPreparationError("runtime must be 'openvino-cpu', 'openvino-gpu', 'ctranslate2', or 'transformers'")
    default_precision = "bf16" if runtime == "transformers" else "int8" if runtime == "ctranslate2" else "source"
    precision = default_precision if precision is None else precision
    if precision not in _PRECISION_OPTIONS[runtime]:
        raise ModelPreparationError(f"precision is unsupported for {runtime}")
    if progress:
        progress("inspecting")
    sha, config, files = _config(repository, revision, token)
    is_gemma = repository == "google/gemma-4-E4B-it"
    if is_gemma:
        reasons = _gemma_reasons(config, files)
        if reasons:
            raise ModelPreparationError("Unsupported Gemma checkpoint: " + "; ".join(reasons))
        if operation == "convert" and runtime != "transformers":
            raise ModelPreparationError("Gemma 4 E4B supports only the Transformers runtime; no conversion is offered")
    elif runtime == "transformers":
        raise ModelPreparationError("The Transformers runtime currently supports only google/gemma-4-E4B-it")
    else:
        reasons = _whisper_reasons(config, files)
        if reasons:
            raise ModelPreparationError("Unsupported checkpoint: " + "; ".join(reasons))
    root = _models_root(models_root)
    registry_path = root / "models.local.json"
    if registry_path.is_symlink():
        raise ModelPreparationError("The local model registry must not be a symlink")
    registry = _read_registry(registry_path)
    registered_repo = registry.get(model_key)
    if registered_repo and registered_repo != repository:
        raise ModelPreparationError("model_key is already registered to a different repository")
    if model_key in BUILTINS and BUILTINS[model_key] != repository:
        raise ModelPreparationError("model_key is a built-in alias for a different repository")
    duplicate_key = next(
        (key for key, value in {**BUILTINS, **registry}.items() if key != model_key and value == repository),
        None,
    )
    if duplicate_key:
        raise ModelPreparationError(f"Repository is already registered as model_key {duplicate_key!r}")

    source_path = root / "sources" / model_key
    if (root / "sources").is_symlink():
        raise ModelPreparationError("The model sources directory must not be a symlink")
    if source_path.is_symlink():
        raise ModelPreparationError("The model source target must not be a symlink")
    source_exists = source_path.exists()
    if source_exists and not _metadata_matches(source_path, repository, sha):
        raise ModelPreparationError("An existing source for this model_key has different repository or revision metadata")
    target = root / (("ctranslate2" if runtime == "ctranslate2" else "openvino") + f"-{model_key}-{precision}") if runtime != "transformers" else source_path
    if target.is_symlink():
        raise ModelPreparationError("The converted model target must not be a symlink")
    artifact_exists = target.exists()
    artifact_runtime = "openvino" if runtime.startswith("openvino") else runtime
    if runtime != "transformers" and artifact_exists and not _metadata_matches(target, repository, sha, artifact_runtime, precision):
        raise ModelPreparationError("An existing runtime artifact conflicts with this repository or conversion")
    runtime_status = _runtime_status(runtime) if operation == "convert" else None
    if runtime_status and not runtime_status["available"]:
        raise ModelPreparationUnavailableError(runtime_status["reason"])

    try:
        root.mkdir(parents=True, exist_ok=True)
        stage_root = Path(tempfile.mkdtemp(prefix=f".{model_key}-stage-", dir=root))
    except OSError:
        raise ModelPreparationUnavailableError(
            "Unable to create model staging storage. Check that the models directory is writable and has free space."
        ) from None
    staged_source = stage_root / "source"
    staged_artifact = stage_root / "artifact"
    staged_registry = stage_root / "models.local.json"
    published = []
    try:
        if not source_exists:
            if progress:
                progress("downloading")
            _download_source(repository, sha, token, staged_source)
        if operation == "convert":
            if runtime != "transformers" and not artifact_exists:
                if progress:
                    progress("converting")
                _convert(source_path if source_exists else staged_source, staged_artifact, runtime, precision)
                (staged_artifact / ".model-deployment.json").write_text(
                    json.dumps({"repository": repository, "revision": sha, "runtime": artifact_runtime, "precision": precision}, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            if progress:
                progress("verifying")
            if runtime == "transformers":
                verify_path = source_path if source_exists else staged_source
            else:
                verify_path = target if artifact_exists else staged_artifact
            _verify(verify_path, model_key, runtime, precision)
        registry[model_key] = repository
        staged_registry.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        if not source_exists:
            os.replace(staged_source, source_path)
            published.append(source_path)
        if operation == "convert" and runtime != "transformers" and not artifact_exists:
            os.replace(staged_artifact, target)
            published.append(target)
        os.replace(staged_registry, registry_path)
    except OSError:
        for path in reversed(published):
            shutil.rmtree(path, ignore_errors=True)
        raise ModelPreparationUnavailableError(
            "Unable to publish model files. Check that the models directory is writable and has free space."
        ) from None
    except Exception:
        for path in reversed(published):
            shutil.rmtree(path, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
    if progress:
        progress("completed")
    return {
        "model_key": model_key,
        "repository": repository,
        "revision": sha,
        "downloaded": not source_exists,
        "converted": operation == "convert" and runtime != "transformers",
        "verified": operation == "convert",
        "runtime": runtime,
        "precision": precision,
        "reason": None,
    }


def _deletion_targets(root, model_key):
    """Return the exact source and runtime directories owned by one key."""
    return [
        (root / "sources" / model_key, "source"),
        *((path, "runtime") for family in ("openvino", "ctranslate2") for path in _asset_paths(root, family, model_key)),
    ]


def _safe_deletion_plan(model_key, models_root):
    if (
        not isinstance(model_key, str)
        or not _MODEL_KEY.fullmatch(model_key)
        or ".." in model_key
    ):
        raise ModelPreparationError(
            "model_key must be a safe local key of at most 64 characters without '..'"
        )

    root = _models_root(models_root)
    if root.is_symlink():
        raise ModelPreparationError("models_root must not be a symlink")
    try:
        root_resolved = root.resolve()
    except (OSError, RuntimeError):
        raise ModelPreparationUnavailableError(
            "Unable to inspect the models directory. Check that it is accessible."
        ) from None

    registry_path = root / "models.local.json"
    if registry_path.is_symlink():
        raise ModelPreparationError("The local model registry must not be a symlink")
    if registry_path.exists() and not registry_path.is_file():
        raise ModelPreparationError("The local model registry must be a regular file")
    registry = _read_registry(registry_path)

    targets = _deletion_targets(root, model_key)
    # Precision-suffixed and generic names can alias when one registered key
    # ends in a supported precision. Resolve ownership from marker metadata;
    # otherwise refuse the ambiguous deletion in either direction.
    for other_key, other_repository in registry.items():
        if other_key == model_key:
            continue
        for family in ("openvino", "ctranslate2"):
            generic_paths = {root / f"{family}-{model_key}", root / family / model_key}
            if other_key.startswith(f"{model_key}-"):
                precision = other_key[len(model_key) + 1 :]
                if precision not in {"source", "bf16", "int8", "int4", "float32"}:
                    continue
                colliding_paths = {
                    root / f"{family}-{other_key}",
                    root / family / other_key,
                }
            elif model_key.startswith(f"{other_key}-"):
                precision = model_key[len(other_key) + 1 :]
                if precision not in {"source", "bf16", "int8", "int4", "float32"}:
                    continue
                colliding_paths = {
                    path
                    for path in _asset_paths(root, family, other_key)
                    if path in generic_paths
                }
            else:
                continue
            for target, kind in list(targets):
                if kind != "runtime" or target not in colliding_paths or not target.exists():
                    continue
                marker = target / ".model-deployment.json"
                try:
                    metadata = json.loads(marker.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    metadata = None
                except (OSError, json.JSONDecodeError):
                    metadata = None
                current_repository = registry.get(model_key)
                if (
                    isinstance(metadata, dict)
                    and current_repository is not None
                    and metadata.get("repository") == other_repository
                    and other_repository != current_repository
                ):
                    targets.remove((target, kind))
                    continue
                if not (
                    isinstance(metadata, dict)
                    and current_repository is not None
                    and current_repository != other_repository
                    and metadata.get("repository") == current_repository
                    and metadata.get("runtime") == family
                ):
                    raise ModelPreparationError(
                        "A runtime path is ambiguous with another registered model key; no files were changed"
                    )
    files = []
    present_targets = []
    for target, kind in targets:
        try:
            relative = target.relative_to(root)
            cursor = root
            for part in relative.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise ModelPreparationError(
                        "A model deletion target contains a symlink; no files were changed"
                    )
            if not target.exists():
                continue
            if not target.is_dir():
                raise ModelPreparationError(
                    "A model deletion target is not a directory; no files were changed"
                )
            if not target.resolve().is_relative_to(root_resolved):
                raise ModelPreparationError(
                    "A model deletion target escapes the models directory"
                )
            present_targets.append((target, kind))
            def fail_on_walk_error(error):
                raise error

            for directory, dirnames, filenames in os.walk(
                target, followlinks=False, onerror=fail_on_walk_error
            ):
                current = Path(directory)
                for name in [*dirnames, *filenames]:
                    item = current / name
                    if item.is_symlink():
                        raise ModelPreparationError(
                            "A model deletion target contains a symlink; no files were changed"
                        )
                for name in filenames:
                    item = current / name
                    if not item.is_file():
                        raise ModelPreparationError(
                            "A model deletion target contains a special file; no files were changed"
                        )
                    stat = item.stat()
                    if not item.resolve().is_relative_to(root_resolved):
                        raise ModelPreparationError(
                            "A model deletion file escapes the models directory"
                        )
                    files.append(
                        {
                            "path": item.relative_to(root).as_posix(),
                            "kind": kind,
                            "bytes": stat.st_size,
                        }
                    )
        except ModelPreparationError:
            raise
        except OSError:
            raise ModelPreparationUnavailableError(
                "Unable to inspect local model files. Check that the models directory is accessible."
            ) from None

    files.sort(key=lambda item: (item["path"], item["kind"]))
    registered = model_key in registry
    deletable = bool(present_targets or registered)
    reason = None if deletable else "No local source, runtime files, or registry entry exists for this model key"
    return {
        "model_key": model_key,
        "files": files,
        "total_bytes": sum(item["bytes"] for item in files),
        "registered": registered,
        "deletable": deletable,
        "reason": reason,
    }, root, present_targets, registry, registry_path


def plan_local_model_deletion(model_key, *, models_root=None) -> dict:
    """Preview local files and registry state owned by a model key."""
    plan, _, _, _, _ = _safe_deletion_plan(model_key, models_root)
    return plan


def delete_local_model(model_key, *, models_root=None) -> dict:
    """Delete one model's exact local source/runtime assets and registry entry."""
    plan, root, targets, registry, registry_path = _safe_deletion_plan(
        model_key, models_root
    )
    if not plan["deletable"]:
        raise ModelPreparationError(plan["reason"])

    stage = None
    moved = []
    registry_changed = False
    try:
        stage = Path(tempfile.mkdtemp(prefix=f".{model_key}-delete-", dir=root))
        for index, (target, _) in enumerate(targets):
            if not target.exists():
                continue
            staged = stage / f"asset-{index}"
            os.replace(target, staged)
            moved.append((target, staged))

        if model_key in registry:
            updated = dict(registry)
            del updated[model_key]
            staged_registry = stage / "models.local.json"
            staged_registry.write_text(
                json.dumps(updated, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(staged_registry, registry_path)
            registry_changed = True
    except OSError:
        rollback_failed = False
        if registry_changed:
            try:
                rollback_registry = stage / "models.local.rollback.json"
                rollback_registry.write_text(
                    json.dumps(registry, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                os.replace(rollback_registry, registry_path)
            except OSError:
                rollback_failed = True
        for target, staged in reversed(moved):
            try:
                if staged.exists():
                    os.replace(staged, target)
            except OSError:
                rollback_failed = True
        if stage is not None and not rollback_failed:
            shutil.rmtree(stage, ignore_errors=True)
        if rollback_failed:
            raise ModelPreparationUnavailableError(
                "Model deletion could not be completed or rolled back. Recovery data was preserved in temporary model storage; check models directory permissions."
            ) from None
        raise ModelPreparationUnavailableError(
            "Unable to stage model deletion. Check that the models directory is writable and has free space."
        ) from None

    try:
        shutil.rmtree(stage)
    except OSError:
        raise ModelPreparationUnavailableError(
            "Model assets were removed from their active paths, but temporary deletion data could not be cleaned up. Check models directory permissions."
        ) from None

    return {**plan, "deleted": True}
