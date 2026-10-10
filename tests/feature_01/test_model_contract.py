"""Model selection, loading, ownership, and lifecycle through the public API."""

import os
import json
import multiprocessing

import numpy as np
import pytest

from .support import RuntimeFactory, ScriptedRuntime, wav_bytes


def test_catalog_has_control_panel_metadata_and_unavailable_reasons(api):
    models = api.list_available_models()
    assert isinstance(models, list)
    assert any(model["key"] == "turbo" for model in models)
    turbo = next(model for model in models if model["key"] == "turbo")
    assert any(runtime["key"] == "openvino-gpu" and "source" in runtime["precision_options"] for runtime in turbo["runtimes"])
    assert len({model["key"] for model in models}) == len(models)
    for model in models:
        assert {"key", "display_name", "repository", "installed", "downloadable", "languages", "runtimes"} <= model.keys()
        for runtime in model["runtimes"]:
            assert {"key", "compatible", "ready", "precision_options", "reason"} <= runtime.keys()
            if not runtime["ready"]:
                assert runtime["reason"]


def test_catalog_rescans_new_local_models_without_restart(api, tmp_path):
    catalog = tmp_path / "models.local.json"
    catalog.write_text("{}", encoding="utf-8")
    before = api.list_available_models(catalog_path=catalog)
    assert not any(model["repository"] == "openai/whisper-tiny" for model in before)
    catalog.write_text(json.dumps({"tiny": "openai/whisper-tiny"}), encoding="utf-8")
    after = api.list_available_models(catalog_path=catalog)
    assert any(model["repository"] == "openai/whisper-tiny" for model in after)


def test_catalog_merges_duplicate_repository_discoveries(api, tmp_path):
    catalog = tmp_path / "models.local.json"
    catalog.write_text(json.dumps({"turbo-alias": "typhoon-ai/typhoon-whisper-turbo"}), encoding="utf-8")
    models = api.list_available_models(catalog_path=catalog)
    assert len([model for model in models if model["repository"] == "typhoon-ai/typhoon-whisper-turbo"]) == 1


def test_catalog_requires_weight_artifacts_and_discovers_nested_precision_metadata(api, tmp_path, monkeypatch):
    root = tmp_path / "models"
    empty = root / "openvino-turbo-source"
    empty.mkdir(parents=True)
    monkeypatch.setenv("SPEECH_TO_TEXT_MODELS_DIR", str(root))
    models = api.list_available_models()
    turbo = next(model for model in models if model["key"] == "turbo")
    assert not turbo["installed"]

    converted = root / "openvino-custom-whisper-int8"
    converted.mkdir()
    (converted / "config.json").write_text(json.dumps({"model_type": "whisper", "is_multilingual": False}))
    (converted / "openvino_model.xml").write_text("model")
    (converted / "openvino_model.bin").write_bytes(b"weights")
    (root / "models.local.json").write_text(json.dumps({"custom-whisper": "org/custom-whisper"}))
    models = api.list_available_models()
    custom = next(model for model in models if model["key"] == "custom-whisper")
    assert custom["installed"]
    assert custom["languages"] == ["en"]
    assert custom["runtimes"][0]["key"] == "openvino-gpu"


def test_default_configuration_reaches_runtime_and_handle(api):
    runtime = ScriptedRuntime([])
    factory = RuntimeFactory(runtime)
    handle = api.load_model({}, runtime_factory=factory)
    try:
        assert factory.configs[0]["model"] == "turbo"
        assert factory.configs[0]["runtime"] == "openvino-gpu"
        assert factory.configs[0]["precision"] == "source"
        assert factory.configs[0]["enqueue_timeout_seconds"] == 30.0
        assert handle.state == "ready"
        assert handle.model == "turbo"
        assert handle.runtime == "openvino-gpu"
        assert handle.precision == "source"
    finally:
        handle.close()
    assert handle.state == "closed"
    assert runtime.closed


def test_selected_runtime_and_precision_are_not_silently_replaced(api):
    factory = RuntimeFactory(ScriptedRuntime([]))
    handle = api.load_model({"model": "turbo", "runtime": "ctranslate2", "precision": "int8"}, runtime_factory=factory)
    try:
        assert handle.runtime == "ctranslate2"
        assert handle.precision == "int8"
        assert factory.configs[0]["precision"] == "int8"
    finally:
        handle.close()


def test_mutating_callers_config_does_not_reconfigure_loaded_model(api):
    config = {"model": "turbo", "runtime": "openvino-gpu", "precision": "source"}
    handle = api.load_model(config, runtime_factory=RuntimeFactory(ScriptedRuntime([])))
    try:
        config["precision"] = "int8"
        assert handle.precision == "source"
    finally:
        handle.close()


def test_model_is_loaded_once_for_multiple_chunks_and_clips(api):
    runtime = ScriptedRuntime(["first", "second", "third"])
    factory = RuntimeFactory(runtime)
    handle = api.load_model({}, runtime_factory=factory)
    try:
        clip = wav_bytes(np.full(3200, 10000))
        assert api.transcribe_clip(clip, handle, {"chunk_seconds": 0.1}) == "first second"
        assert api.transcribe_clip(wav_bytes(np.full(1600, 10000)), handle, {"chunk_seconds": 0.1}) == "third"
        assert len(factory.configs) == 1
        assert not runtime.closed
    finally:
        handle.close()


def test_closed_handle_cannot_transcribe(api):
    handle = api.load_model({}, runtime_factory=RuntimeFactory(ScriptedRuntime([])))
    handle.close()
    handle.close()
    with pytest.raises(api.ModelClosedError):
        api.transcribe_clip(wav_bytes([10000] * 1600), handle, {})


@pytest.mark.parametrize("config", [
    {"unknown": True}, {"precision": "invalid"}, {"runtime": "invalid"},
    {"queue_capacity": 0}, {"enqueue_timeout_seconds": 0},
    {"runtime": "ctranslate2", "precision": "float16"},
])
def test_invalid_model_configuration_is_rejected_before_loading(api, config):
    factory = RuntimeFactory(ScriptedRuntime([]))
    with pytest.raises(api.ConfigurationError):
        api.load_model(config, runtime_factory=factory)
    assert factory.configs == []


def test_unknown_or_path_like_model_keys_are_rejected_before_factory(api):
    for model in ("../private", "not-in-catalog"):
        factory = RuntimeFactory(ScriptedRuntime([]))
        with pytest.raises(api.ConfigurationError):
            api.load_model(model_config={"model": model}, runtime_factory=factory)
        assert factory.configs == []


def test_load_failure_never_returns_ready_handle(api):
    def unavailable(config):
        raise RuntimeError("GPU unavailable")

    with pytest.raises(api.ModelLoadError, match="GPU unavailable"):
        api.load_model({}, runtime_factory=unavailable)


def _run_owned_model(result_queue):
    from speech_to_text.features import model_deployment as api

    factory = RuntimeFactory(ScriptedRuntime(["owned"]))
    handle = api.load_model({}, runtime_factory=factory)
    try:
        text = api.transcribe_clip(wav_bytes([10000] * 1600), handle, {"chunk_seconds": 0.1})
        result_queue.put((os.getpid(), text, len(factory.configs)))
    finally:
        handle.close()


def test_separate_input_processes_load_their_own_models(api):
    # A spawned interpreter must import the real module independently.
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    processes = [context.Process(target=_run_owned_model, args=(result_queue,)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        results = [result_queue.get(timeout=10) for _ in processes]
        for process in processes:
            process.join(timeout=3)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.pid is not None and process.is_alive():
                process.terminate()
                process.join(timeout=3)
        result_queue.close()
        result_queue.join_thread()
    assert [(text, loads) for _, text, loads in results] == [("owned", 1), ("owned", 1)]
    assert len({pid for pid, _, _ in results}) == 2
    assert all(pid != os.getpid() for pid, _, _ in results)
