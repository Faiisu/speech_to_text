"""Filesystem-only model/runtime discovery; calling this never downloads weights."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

BUILTINS = {"turbo": "typhoon-ai/typhoon-whisper-turbo"}
RUNTIMES = ("openvino-gpu", "openvino-cpu", "ctranslate2")
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
    return any(
        xml.with_suffix(".bin").is_file() and xml.with_suffix(".bin").stat().st_size > 0
        for xml in path.glob("*.xml")
    )


def _languages(root, key, repo, *, allow_cache=True):
    configs = [
        path / "config.json"
        for family in ("openvino", "ctranslate2")
        for path in _asset_paths(root, family, key)
    ]
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
    multilingual = key == "turbo"
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


def default_models_root():
    env = os.environ.get("SPEECH_TO_TEXT_MODELS_DIR")
    if env:
        return Path(env)
    root = Path.cwd() / "models"
    legacy = Path.cwd() / "legacies-poc" / "models"
    if root.is_dir() and any(
        _has_weights(p, "ctranslate2") or _has_weights(p, "openvino")
        for p in root.iterdir()
    ):
        return root
    if legacy.is_dir() and any(
        _has_weights(p, "ctranslate2") or _has_weights(p, "openvino")
        for p in legacy.iterdir()
    ):
        return legacy
    return root


def _registry(path):
    if path is None:
        root = default_models_root()
        registry = root / "models.local.json"
    else:
        registry = Path(path)
        root = registry.parent
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
    return root, data


def _runtime_metadata(key, root, model_key, repo):
    ov = key.startswith("openvino")
    module = "openvino" if ov else "ctranslate2"
    family = "openvino" if ov else "ctranslate2"
    converted = any(
        _has_weights(path, family) for path in _asset_paths(root, family, model_key)
    )
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
    precisions = ["source", "bf16", "int8", "int4"] if ov else ["int8", "float32"]
    # CTranslate2's published model conversion is int8; only expose options
    # the selected adapter can honor with the converted asset.
    compatible = (bool(repo) or converted) and (ov or key == "ctranslate2")
    return {
        "key": key,
        "compatible": compatible,
        "ready": bool(ready),
        "precision_options": precisions,
        "reason": reason if compatible else "Runtime/model combination is unsupported",
    }


def list_models(*, catalog_path=None):
    root, local = _registry(catalog_path)
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
                    "languages": _languages(
                        root, target, repo, allow_cache=catalog_path is None
                    ),
                    "runtimes": [],
                }
    converted_keys = set()
    if root.is_dir():
        for item in root.iterdir():
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
            folder = root / family
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
            _runtime_metadata(runtime, root, model["key"], model["repository"])
            for runtime in RUNTIMES
        ]
        model["installed"] = model["key"] in converted_keys or any(
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
            "languages": _languages(root, key, None, allow_cache=catalog_path is None),
            "runtimes": [],
        }
        model["runtimes"] = [_runtime_metadata(r, root, key, None) for r in RUNTIMES]
        entries[key] = model
    return sorted(entries.values(), key=lambda item: item["key"])


def resolve_repository(model_key):
    for item in list_models():
        if item["key"] == model_key:
            return item["repository"]
    raise ValueError(
        f"Unknown model key {model_key!r}; call list_available_models() to see locally known models"
    )
