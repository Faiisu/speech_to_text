"""Primary and archived model roots remain discoverable and loadable."""

import json
import sys
from types import ModuleType

from speech_to_text.features import model_deployment as api
from speech_to_text.features.model_deployment.runtime import CTranslate2Adapter


def _converted(root, key):
    model = root / f"ctranslate2-{key}-int8"
    model.mkdir(parents=True)
    (model / "model.bin").write_bytes(b"converted weights")
    return model


def test_catalog_merges_primary_registry_and_legacy_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SPEECH_TO_TEXT_MODELS_DIR", raising=False)
    primary = tmp_path / "models"
    legacy = tmp_path / "legacies-poc" / "models"

    (primary / "sources" / "source-only").mkdir(parents=True)
    (primary / "models.local.json").write_text(
        json.dumps({"source-only": "org/source-only"})
    )
    (legacy / "models.local.json").parent.mkdir(parents=True)
    (legacy / "models.local.json").write_text(json.dumps({"legacy-model": "org/legacy-model"}))
    _converted(legacy, "legacy-model")

    models = {model["key"]: model for model in api.list_available_models()}

    assert {"source-only", "legacy-model"} <= models.keys()
    assert models["source-only"]["installed"] is False
    assert models["legacy-model"]["installed"] is True


def test_runtime_reads_legacy_models_and_prefers_primary_for_duplicate_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SPEECH_TO_TEXT_MODELS_DIR", raising=False)
    primary = tmp_path / "models"
    legacy = tmp_path / "legacies-poc" / "models"
    primary_model = _converted(primary, "shared")
    _converted(legacy, "shared")
    legacy_model = _converted(legacy, "legacy-only")
    models = {model["key"] for model in api.list_available_models()}
    observed = []

    class WhisperModel:
        def __init__(self, path, **kwargs):
            observed.append(path)

    faster_whisper = ModuleType("faster_whisper")
    faster_whisper.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", faster_whisper)

    CTranslate2Adapter({"model": "shared", "runtime": "ctranslate2", "precision": "int8"})
    CTranslate2Adapter({"model": "legacy-only", "runtime": "ctranslate2", "precision": "int8"})

    assert {"shared", "legacy-only"} <= models
    assert observed == [str(primary_model), str(legacy_model)]
