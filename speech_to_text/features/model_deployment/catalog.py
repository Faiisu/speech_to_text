"""Filesystem-only model/runtime discovery; calling this never downloads weights."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

BUILTINS = {
    "turbo": "typhoon-ai/typhoon-whisper-turbo",
    "gemma-4-e2b": "google/gemma-4-E2B-it",
    "gemma-4-e4b": "google/gemma-4-E4B-it",
}
GEMMA_REPOSITORIES = frozenset((BUILTINS["gemma-4-e2b"], BUILTINS["gemma-4-e4b"]))
RUNTIMES = ("openvino-gpu", "openvino-cpu", "ctranslate2", "transformers")
PRECISION_SUFFIXES = ("-source", "-bf16", "-int8", "-int4", "-float32")
WHISPER_LANGUAGE_CODES = tuple(
    "af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo fr gl gu ha haw he hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo lt lv mg mi mk ml mn mr ms mt my ne nl nn no oc pa pl ps pt ro sa sd si sk sl sn so sq sr su sv sw ta te tg th tk tl tr tt uk ur uz vi yi yo yue zh".split()
)


def _asset_paths(root, family, model_key):
    paths = [root / f"{family}-{model_key}", root / family / model_key]
    for precision in ("source", "bf16", "int8", "int4", "float32"):
        paths.extend(
            (
                root / f"{family}-{model_key}-{precision}",
                root / family / f"{model_key}-{precision}",
            )
        )
    return paths


def _has_weights(path, family):
    if not path.is_dir():
        return False
    if family == "ctranslate2":
        artifact = path / "model.bin"
        return artifact.is_file() and artifact.stat().st_size > 0
    if family == "transformers":
        return any(path.glob("*.safetensors"))
    return any(
        xml.with_suffix(".bin").is_file() and xml.with_suffix(".bin").stat().st_size > 0
        for xml in path.glob("*.xml")
    )


def _languages(root, key, repo, *, allow_cache=True, roots=None):
    roots = roots or [root]
    configs = [
        path / filename
        for model_root in roots
        for family in ("openvino", "ctranslate2")
        for path in _asset_paths(model_root, family, key)
        for filename in ("config.json", "generation_config.json", "tokenizer.json")
    ]
    configs.extend(
        model_root / "sources" / key / filename
        for model_root in roots
        for filename in ("config.json", "generation_config.json", "tokenizer.json")
    )
    if repo and allow_cache:
        try:
            from huggingface_hub.constants import HF_HUB_CACHE

            cached = (
                Path(HF_HUB_CACHE) / f"models--{repo.replace('/', '--')}" / "snapshots"
            )
            if cached.is_dir():
                configs.extend(cached.glob("*/config.json"))
        except ImportError:
            pass
    multilingual = key == "turbo" or bool(repo and not repo.casefold().endswith(".en"))
    explicitly_english = bool(repo and repo.casefold().endswith(".en"))
    for path in configs:
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if "is_multilingual" in config:
            multilingual = bool(config["is_multilingual"])
            explicitly_english = not multilingual
            break
        language_ids = config.get("lang_to_id")
        if isinstance(language_ids, dict):
            languages = {
                code
                for code in WHISPER_LANGUAGE_CODES
                if any(token in (code, f"<|{code}|>") for token in language_ids)
            }
            if languages:
                multilingual = len(languages) > 1
                explicitly_english = languages == {"en"}
                break
        if path.name == "tokenizer.json":
            tokens = set()
            added_tokens = config.get("added_tokens", [])
            for item in added_tokens if isinstance(added_tokens, list) else []:
                content = item.get("content") if isinstance(item, dict) else None
                if isinstance(content, str):
                    tokens.add(content)
            model_config = config.get("model")
            vocab = model_config.get("vocab", {}) if isinstance(model_config, dict) else {}
            if isinstance(vocab, dict):
                tokens.update(key for key in vocab if isinstance(key, str))
            languages = {
                code
                for code in WHISPER_LANGUAGE_CODES
                if f"<|{code}|>" in tokens
            }
            if languages:
                multilingual = len(languages) > 1
                explicitly_english = languages == {"en"}
                break
    return (
        ["en"]
        if explicitly_english or not multilingual
        else [*WHISPER_LANGUAGE_CODES, "auto"]
    )


def _module_ready(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _gemma_runtime_ready():
    try:
        from importlib.metadata import version
        from transformers import AutoModelForMultimodalLM  # noqa: F401
        import accelerate  # noqa: F401
        import librosa  # noqa: F401
        import torch  # noqa: F401
        import torchvision  # noqa: F401

        transformers_version = tuple(
            int(part) for part in version("transformers").split(".")[:3]
        )
        torch_version = tuple(int(part) for part in version("torch").split(".")[:2])
        return transformers_version >= (5, 10, 1) and torch_version >= (2, 4)
    except Exception:
        return False


def model_read_roots(*, catalog_path=None):
    """Return model roots in lookup order; explicit roots stay hermetic."""
    if catalog_path is not None:
        return [Path(catalog_path).parent]
    env = os.environ.get("SPEECH_TO_TEXT_MODELS_DIR")
    if env:
        return [Path(env)]
    primary = Path.cwd() / "models"
    legacy = Path.cwd() / "legacies-poc" / "models"
    return [primary] if primary == legacy else [primary, legacy]


def default_models_root():
    """Return the primary root used for writes and runtime-local preferences."""
    return model_read_roots()[0]


def _registry(path):
    if path is None:
        roots = model_read_roots()
        root = roots[0]
        data = {}
        for model_root in reversed(roots):
            data.update(_read_registry_file(model_root / "models.local.json"))
        return root, data
    else:
        registry = Path(path)
        root = registry.parent
    return root, _read_registry_file(registry)


def _read_registry_file(registry):
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read model registry {registry}: {exc}") from exc
    if not isinstance(data, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in data.items()
    ):
        raise ValueError(
            f"Model registry {registry} must contain a JSON object of model keys to repository strings"
        )
    return data


def _runtime_metadata(key, root, model_key, repo, *, roots=None):
    roots = roots or [root]
    if key == "transformers":
        is_gemma = repo in GEMMA_REPOSITORIES
        source_paths = [model_root / "sources" / model_key for model_root in roots]
        source_ready = any(_has_weights(path, "transformers") for path in source_paths)
        dependencies_ready = _gemma_runtime_ready()
        return {
            "key": key,
            "compatible": is_gemma,
            "ready": bool(is_gemma and source_ready and dependencies_ready),
            "precision_options": (["bf16"] if repo == BUILTINS["gemma-4-e2b"] else ["bf16", "float32"]) if is_gemma else [],
            "has_generic_artifact": False,
            "installed_precisions": (["bf16"] if repo == BUILTINS["gemma-4-e2b"] else ["bf16", "float32"]) if is_gemma and source_ready else [],
            "reason": None if is_gemma and source_ready and dependencies_ready else (
                "Transformers runtime supports Gemma 4 E2B and E4B only" if not is_gemma else
                "Gemma source weights are not prepared under the models directory" if not source_ready else
                "Install the speech-to-text[gemma4] extra to use the Transformers runtime"
            ),
        }
    ov = key.startswith("openvino")
    module = "openvino" if ov else "ctranslate2"
    family = "openvino" if ov else "ctranslate2"
    converted = any(
        _has_weights(path, family)
        for model_root in roots
        for path in _asset_paths(model_root, family, model_key)
    )
    has_generic_artifact = any(
        _has_weights(path, family)
        for model_root in roots
        for path in (model_root / f"{family}-{model_key}", model_root / family / model_key)
    )
    installed_precisions = [
        precision
        for precision in ("source", "bf16", "int8", "int4", "float32")
        if any(
            _has_weights(path, family)
            for model_root in roots
            for path in (
                model_root / f"{family}-{model_key}-{precision}",
                model_root / family / f"{model_key}-{precision}",
            )
        )
    ]
    ready_lib = _module_ready(module)
    device_ready = True
    if ov and ready_lib:
        ready_lib = _module_ready("optimum.intel") and _module_ready("transformers")
        try:
            from openvino import Core

            devices = Core().available_devices
            device_ready = (
                any(device == "CPU" for device in devices)
                if key == "openvino-cpu"
                else any(device.startswith("GPU") for device in devices)
            )
        except Exception:
            device_ready = False
    else:
        ready_lib = ready_lib and _module_ready("faster_whisper")
    ready = ready_lib and device_ready and converted
    if ov and not device_ready and _module_ready("openvino"):
        reason = f"OpenVINO device required by {key} is not available"
    elif not ready_lib:
        reason = f"Runtime dependencies for {key} are not installed or the device is unsupported"
    elif not converted:
        reason = f"No converted {family} weights found under {root}"
    else:
        reason = None
    precision_options = _precision_options(key)
    # CTranslate2's published model conversion is int8; only expose options
    # the selected adapter can honor with the converted asset.
    compatible = (bool(repo) or converted) and (ov or key == "ctranslate2")
    return {
        "key": key,
        "compatible": compatible,
        "ready": bool(ready),
        "precision_options": precision_options,
        "has_generic_artifact": has_generic_artifact,
        "installed_precisions": [
            precision
            for precision in installed_precisions
            if precision in precision_options
        ],
        "reason": reason if compatible else "Runtime/model combination is unsupported",
    }


def _precision_options(runtime):
    return ["int8", "float32"] if runtime == "ctranslate2" else ["source", "bf16", "int8", "int4"]


def _read_provenance(path):
    if path.is_symlink() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    repository = data.get("repository")
    revision = data.get("revision")
    return {
        "repository": repository if isinstance(repository, str) and repository else None,
        "revision": revision if isinstance(revision, str) and revision else None,
    }


def _model_provenance(model, roots):
    """Read source identity from managed and legacy markers without following symlinks."""
    key = model["key"]
    markers = []
    for root in roots:
        source = root / "sources" / key
        if source.is_dir() and not source.is_symlink():
            markers.append(source / ".model-deployment.json")
    for family in ("openvino", "ctranslate2"):
        for root in roots:
            for artifact in _asset_paths(root, family, key):
                if artifact.is_dir() and not artifact.is_symlink():
                    markers.extend((artifact / ".model-deployment.json", artifact / ".speech_to_text_export.json"))

    records = [record for marker in markers if (record := _read_provenance(marker))]
    repository = model.get("repository")
    if not repository:
        repository = next((record["repository"] for record in records if record["repository"]), None)
    revision = next(
        (
            record["revision"]
            for record in records
            if record["revision"] and (not repository or record["repository"] == repository)
        ),
        None,
    )
    model["repository"] = repository
    model["revision"] = revision


def list_models(*, catalog_path=None):
    root, local = _registry(catalog_path)
    roots = model_read_roots(catalog_path=catalog_path)
    entries = {}
    # An explicit registry is intentionally hermetic apart from builtin candidates.
    sources = [BUILTINS, local]
    if catalog_path is None:
        # Local Hugging Face cache discovery is optional and remains filesystem-only.
        try:
            from huggingface_hub.constants import HF_HUB_CACHE

            cache = Path(HF_HUB_CACHE)
            for model_dir in cache.glob("models--*") if cache.is_dir() else ():
                repo = "/".join(model_dir.name.split("--")[1:])
                snapshots = model_dir / "snapshots"
                config_paths = (
                    snapshots.glob("*/config.json") if snapshots.is_dir() else ()
                )
                is_whisper = False
                for config_path in config_paths:
                    try:
                        is_whisper = (
                            json.loads(config_path.read_text(encoding="utf-8")).get(
                                "model_type"
                            )
                            == "whisper"
                        )
                    except (OSError, json.JSONDecodeError):
                        continue
                    if is_whisper:
                        break
                if repo and is_whisper:
                    sources.append({repo.rsplit("/", 1)[-1].replace("_", "-"): repo})
        except ImportError:
            pass
    for source in sources:
        for key, repo in source.items():
            # Aliases to one repository merge into its first catalog entry.
            target = next(
                (
                    candidate
                    for candidate, info in entries.items()
                    if info["repository"] == repo
                ),
                key,
            )
            if target not in entries:
                entries[target] = {
                    "key": target,
                    "display_name": target.replace("-", " ").title(),
                    "repository": repo,
                    "installed": False,
                    "downloadable": True,
                    "languages": ["th", "en", "auto"] if repo in GEMMA_REPOSITORIES else _languages(
                        root, target, repo, allow_cache=catalog_path is None, roots=roots
                    ),
                    "runtimes": [],
                }
    converted_keys = set()
    for model_root in roots:
        if not model_root.is_dir():
            continue
        for item in model_root.iterdir():
            if item.is_dir():
                for prefix in ("openvino-", "ctranslate2-"):
                    if item.name.startswith(prefix):
                        model_key = item.name[len(prefix) :]
                        for suffix in PRECISION_SUFFIXES:
                            if model_key.endswith(suffix):
                                model_key = model_key[: -len(suffix)]
                                break
                        family = prefix[:-1]
                        if _has_weights(item, family):
                            converted_keys.add(model_key)
        for family in ("openvino", "ctranslate2"):
            folder = model_root / family
            if folder.is_dir():
                for item in folder.iterdir():
                    if not item.is_dir():
                        continue
                    model_key = item.name
                    for suffix in PRECISION_SUFFIXES:
                        if model_key.endswith(suffix):
                            model_key = model_key[: -len(suffix)]
                            break
                    if _has_weights(item, family):
                        converted_keys.add(model_key)
    for model in entries.values():
        model["runtimes"] = [
            _runtime_metadata(runtime, root, model["key"], model["repository"], roots=roots)
            for runtime in RUNTIMES
            if runtime != "transformers" or model["repository"] in GEMMA_REPOSITORIES
        ]
        has_gemma_source = model["repository"] in GEMMA_REPOSITORIES and any(
            _has_weights(model_root / "sources" / model["key"], "transformers")
            for model_root in roots
        )
        model["installed"] = model["key"] in converted_keys or has_gemma_source or any(
            r["ready"] for r in model["runtimes"]
        )
    # Converted-only models are discoverable even without their source repository.
    for key in sorted(converted_keys):
        if key in entries:
            continue
        model = {
            "key": key,
            "display_name": key.replace("-", " ").title(),
            "repository": None,
            "installed": True,
            "downloadable": False,
            "languages": _languages(root, key, None, allow_cache=catalog_path is None, roots=roots),
            "runtimes": [],
        }
        model["runtimes"] = [_runtime_metadata(r, root, key, None, roots=roots) for r in RUNTIMES]
        entries[key] = model
    for model in entries.values():
        _model_provenance(model, roots)
    return sorted(entries.values(), key=lambda item: item["key"])


def resolve_repository(model_key):
    for item in list_models():
        if item["key"] == model_key:
            return item["repository"]
    raise ValueError(
        f"Unknown model key {model_key!r}; call list_available_models() to see locally known models"
    )
