"""Safe local cleanup for managed model assets."""

import json
from pathlib import Path

import pytest

from speech_to_text.features import model_deployment as api
from speech_to_text.features.model_deployment import model_management
from speech_to_text.features.model_deployment.catalog import _asset_paths


def _file(path, payload=b"asset"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_delete_removes_exact_source_and_runtime_paths_and_preserves_registry(tmp_path):
    root = tmp_path / "models"
    key = "custom-whisper"
    source_file = root / "sources" / key / "model.safetensors"
    _file(source_file, b"source weights")
    runtime_files = []
    for family in ("openvino", "ctranslate2"):
        for directory in _asset_paths(root, family, key):
            artifact = directory / ("model.bin" if family == "ctranslate2" else "model.xml")
            _file(artifact, b"runtime")
            runtime_files.append(artifact)
    sibling = root / "ctranslate2-custom-whisper-extra-int8" / "model.bin"
    _file(sibling, b"keep")
    (root / "models.local.json").write_text(
        json.dumps({key: "org/custom-whisper", "other": "org/other"})
    )

    preview = api.plan_local_model_deletion(key, models_root=root)

    assert preview == {
        "model_key": key,
        "files": preview["files"],
        "total_bytes": len(b"source weights") + len(runtime_files) * len(b"runtime"),
        "registered": True,
        "deletable": True,
        "reason": None,
    }
    assert {item["kind"] for item in preview["files"]} == {"source", "runtime"}
    assert len(preview["files"]) == 1 + len(runtime_files)
    assert api.delete_local_model(key, models_root=root) == {**preview, "deleted": True}

    assert not (root / "sources" / key).exists()
    assert all(not directory.exists() for family in ("openvino", "ctranslate2") for directory in _asset_paths(root, family, key))
    assert sibling.read_bytes() == b"keep"
    assert json.loads((root / "models.local.json").read_text()) == {"other": "org/other"}


def test_default_root_deletion_does_not_touch_legacy_or_hugging_face_cache(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SPEECH_TO_TEXT_MODELS_DIR", raising=False)
    primary = tmp_path / "models"
    legacy_file = tmp_path / "legacies-poc" / "models" / "ctranslate2-legacy-key-int8" / "model.bin"
    cache_file = tmp_path / "hf-cache" / "models--org--legacy-key" / "snapshots" / "rev" / "model.safetensors"
    primary_file = primary / "ctranslate2-legacy-key-int8" / "model.bin"
    _file(legacy_file, b"legacy")
    _file(cache_file, b"cache")
    _file(primary_file, b"primary")
    monkeypatch.setenv("HF_HUB_CACHE", str(cache_file.parents[3]))

    result = api.delete_local_model("legacy-key")

    assert result["deleted"] is True
    assert not primary_file.exists()
    assert legacy_file.read_bytes() == b"legacy"
    assert cache_file.read_bytes() == b"cache"


@pytest.mark.parametrize("key", ["", "../outside", "a..b", "bad/key", ".hidden"])
def test_invalid_keys_are_rejected_without_mutation(tmp_path, key):
    marker = tmp_path / "models.local.json"
    marker.write_text('{"keep":"org/keep"}')

    with pytest.raises(api.ModelPreparationError):
        api.delete_local_model(key, models_root=tmp_path)

    assert marker.read_text() == '{"keep":"org/keep"}'


def test_symlink_inside_any_target_rejects_the_whole_delete_before_mutation(tmp_path):
    root = tmp_path / "models"
    key = "unsafe"
    ordinary = root / "sources" / key / "weights.safetensors"
    external = tmp_path / "outside.safetensors"
    _file(ordinary)
    _file(external, b"protected")
    link = root / "ctranslate2-unsafe-int8" / "external.safetensors"
    link.parent.mkdir(parents=True)
    link.symlink_to(external)
    registry = root / "models.local.json"
    registry.write_text(json.dumps({key: "org/unsafe", "keep": "org/keep"}))

    with pytest.raises(api.ModelPreparationError, match="symlink"):
        api.delete_local_model(key, models_root=root)

    assert ordinary.exists()
    assert link.is_symlink()
    assert external.read_bytes() == b"protected"
    assert json.loads(registry.read_text()) == {key: "org/unsafe", "keep": "org/keep"}


def test_ambiguous_precision_path_is_not_deleted_for_registered_suffixed_key(tmp_path):
    root = tmp_path / "models"
    target = root / "ctranslate2-model-int8" / "model.bin"
    _file(target, b"could belong to model-int8")
    registry = root / "models.local.json"
    registry.write_text(
        json.dumps({"model": "org/model", "model-int8": "org/model-int8"})
    )

    with pytest.raises(api.ModelPreparationError, match="ambiguous"):
        api.delete_local_model("model", models_root=root)

    assert target.read_bytes() == b"could belong to model-int8"
    assert json.loads(registry.read_text()) == {
        "model": "org/model",
        "model-int8": "org/model-int8",
    }


def test_generic_path_ambiguity_is_checked_when_deleting_suffixed_key(tmp_path):
    root = tmp_path / "models"
    target = root / "ctranslate2-model-int8" / "model.bin"
    _file(target, b"could belong to either key")
    registry = root / "models.local.json"
    registry.write_text(
        json.dumps({"model": "org/model", "model-int8": "org/model-int8"})
    )

    with pytest.raises(api.ModelPreparationError, match="ambiguous"):
        api.delete_local_model("model-int8", models_root=root)

    assert target.read_bytes() == b"could belong to either key"
    assert json.loads(registry.read_text()) == {
        "model": "org/model",
        "model-int8": "org/model-int8",
    }


def test_walk_error_fails_closed_for_plan_and_delete(tmp_path, monkeypatch):
    root = tmp_path / "models"
    key = "unreadable"
    source_file = root / "sources" / key / "weights.safetensors"
    _file(source_file)
    registry = root / "models.local.json"
    registry.write_text(json.dumps({key: "org/unreadable"}))
    real_walk = model_management.os.walk

    def fail_walk(path, **kwargs):
        if Path(path) == source_file.parent:
            raise PermissionError("simulated unreadable model directory")
        return real_walk(path, **kwargs)

    monkeypatch.setattr(model_management.os, "walk", fail_walk)

    with pytest.raises(api.ModelPreparationUnavailableError, match="inspect local model files"):
        api.plan_local_model_deletion(key, models_root=root)
    with pytest.raises(api.ModelPreparationUnavailableError, match="inspect local model files"):
        api.delete_local_model(key, models_root=root)

    assert source_file.read_bytes() == b"asset"
    assert json.loads(registry.read_text()) == {key: "org/unreadable"}


def test_failed_rollback_preserves_staged_model_data(tmp_path, monkeypatch):
    root = tmp_path / "models"
    key = "rollback"
    source_file = root / "sources" / key / "weights.safetensors"
    runtime = root / "ctranslate2-rollback-int8"
    _file(source_file, b"source must remain recoverable")
    _file(runtime / "model.bin", b"runtime")
    registry = root / "models.local.json"
    registry.write_text(json.dumps({key: "org/rollback"}))
    real_replace = model_management.os.replace

    def fail_forward_and_restore(source, destination):
        source = Path(source)
        destination = Path(destination)
        if source == runtime:
            raise OSError("simulated forward failure")
        if destination == root / "sources" / key and ".rollback-delete-" in str(source):
            raise OSError("simulated rollback failure")
        return real_replace(source, destination)

    monkeypatch.setattr(model_management.os, "replace", fail_forward_and_restore)

    with pytest.raises(api.ModelPreparationUnavailableError, match="Recovery data was preserved"):
        api.delete_local_model(key, models_root=root)

    staged_assets = list(root.glob(".rollback-delete-*/asset-*"))
    assert any((asset / "weights.safetensors").read_bytes() == b"source must remain recoverable" for asset in staged_assets)
    assert not source_file.exists()
    assert runtime.joinpath("model.bin").read_bytes() == b"runtime"
    assert json.loads(registry.read_text()) == {key: "org/rollback"}


def test_missing_local_assets_return_non_deletable_plan(tmp_path):
    plan = api.plan_local_model_deletion("missing", models_root=tmp_path)

    assert plan == {
        "model_key": "missing",
        "files": [],
        "total_bytes": 0,
        "registered": False,
        "deletable": False,
        "reason": "No local source, runtime files, or registry entry exists for this model key",
    }
    with pytest.raises(api.ModelPreparationError, match="No local"):
        api.delete_local_model("missing", models_root=tmp_path)


def test_explicit_root_is_hermetic_and_bad_registry_fails_preflight(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit"
    configured = tmp_path / "configured"
    _file(explicit / "sources" / "local" / "weights.safetensors")
    _file(configured / "sources" / "local" / "weights.safetensors")
    monkeypatch.setenv("SPEECH_TO_TEXT_MODELS_DIR", str(configured))
    (explicit / "models.local.json").write_text("[]")

    with pytest.raises(model_management.ModelPreparationError, match="registry"):
        api.delete_local_model("local", models_root=explicit)

    assert (explicit / "sources" / "local" / "weights.safetensors").exists()
    assert (configured / "sources" / "local" / "weights.safetensors").exists()

@pytest.mark.parametrize("key", ["model", "model-int8"])
def test_same_repository_precision_alias_is_ambiguous_for_either_key(tmp_path, key):
    root = tmp_path / "models"
    target = root / "ctranslate2-model-int8"
    _file(target / "model.bin", b"shared artifact")
    (target / ".model-deployment.json").write_text(
        json.dumps(
            {
                "repository": "org/shared",
                "runtime": "ctranslate2",
                "precision": "int8",
            }
        )
    )
    registry = root / "models.local.json"
    registry.write_text(
        json.dumps({"model": "org/shared", "model-int8": "org/shared"})
    )

    with pytest.raises(api.ModelPreparationError, match="ambiguous"):
        api.delete_local_model(key, models_root=root)

    assert (target / "model.bin").read_bytes() == b"shared artifact"
    assert json.loads(registry.read_text()) == {
        "model": "org/shared",
        "model-int8": "org/shared",
    }
