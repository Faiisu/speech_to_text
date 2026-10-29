"""Hugging Face model management public API and publication boundaries."""

import json

import pytest

from speech_to_text.features import model_deployment as api
from speech_to_text.features.model_deployment import model_management


WHISPER = {"model_type": "whisper", "architectures": ["WhisperForConditionalGeneration"]}
SOURCE_FILES = {
    "config.json",
    "model.safetensors",
    "preprocessor_config.json",
    "tokenizer.json",
}


def _remote(monkeypatch, config=WHISPER):
    monkeypatch.setattr(model_management, "_config", lambda repository, revision, token: ("commit-sha", dict(config), set(SOURCE_FILES)))
    monkeypatch.setattr(
        model_management,
        "_runtime_status",
        lambda runtime: {
            "key": runtime,
            "available": True,
            "precision_options": ["int8", "float32"] if runtime == "ctranslate2" else ["source", "bf16", "int8", "int4"],
            "reason": None,
        },
    )


def _download(repository, sha, token, destination):
    destination.mkdir(parents=True)
    (destination / "config.json").write_text(json.dumps(WHISPER))
    (destination / "model.safetensors").write_bytes(b"safe weights")
    (destination / ".model-deployment.json").write_text(json.dumps({"repository": repository, "revision": sha}))


def test_inspection_reports_pinned_sha_and_runtime_options_without_weights(monkeypatch):
    _remote(monkeypatch)

    result = api.inspect_huggingface_model("org/whisper", "release")

    assert result["revision"] == "commit-sha"
    assert result["compatible"] is True
    assert [item["key"] for item in result["runtimes"]] == ["openvino-cpu", "openvino-gpu", "ctranslate2"]
    assert "available" in result["runtimes"][0]


def test_inspection_marks_non_whisper_architecture_unsupported(monkeypatch):
    _remote(monkeypatch, {"model_type": "llama", "architectures": ["LlamaForCausalLM"]})

    result = api.inspect_huggingface_model("org/not-whisper")

    assert result["compatible"] is False
    assert result["reasons"]


def test_download_publishes_source_and_registry_without_runtime_readiness(tmp_path, monkeypatch):
    _remote(monkeypatch)
    monkeypatch.setattr(model_management, "_download_source", _download)

    result = api.prepare_huggingface_model(
        "org/whisper", "custom-whisper", operation="download", models_root=tmp_path
    )

    assert result["downloaded"] is True
    assert result["converted"] is False
    assert result["verified"] is False
    assert (tmp_path / "sources/custom-whisper/model.safetensors").is_file()
    assert json.loads((tmp_path / "models.local.json").read_text()) == {"custom-whisper": "org/whisper"}
    assert not (tmp_path / "ctranslate2-custom-whisper-int8").exists()


def test_gemma_download_includes_required_chat_template(tmp_path, monkeypatch):
    repository = "google/gemma-4-E4B-it"
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    config = {
        "model_type": "gemma4",
        "architectures": ["Gemma4ForConditionalGeneration"],
        "audio_config": {},
    }
    (snapshot / "config.json").write_text(json.dumps(config))
    (snapshot / "processor_config.json").write_text("{}")
    (snapshot / "tokenizer.json").write_text("{}")
    (snapshot / "model.safetensors").write_bytes(b"safe weights")
    (snapshot / "chat_template.jinja").write_text("{{ messages }}")
    monkeypatch.setattr(
        model_management,
        "_hub",
        lambda: (None, None, lambda **kwargs: snapshot),
    )

    destination = tmp_path / "source"
    model_management._download_source(repository, "commit-sha", None, destination)

    assert (destination / "chat_template.jinja").read_text() == "{{ messages }}"
    assert "Checkpoint is missing chat_template.jinja" in model_management._gemma_reasons(
        config, {"config.json", "processor_config.json", "tokenizer.json", "model.safetensors"}
    )


def test_failed_gemma_source_repair_keeps_existing_source_unchanged(tmp_path, monkeypatch):
    repository = "google/gemma-4-E4B-it"
    source = tmp_path / "sources" / "gemma-4-e4b"
    source.mkdir(parents=True)
    config = {
        "model_type": "gemma4",
        "architectures": ["Gemma4ForConditionalGeneration"],
        "audio_config": {},
    }
    (source / "config.json").write_text(json.dumps(config))
    (source / "processor_config.json").write_text("{}")
    (source / "tokenizer.json").write_text("{}")
    (source / "model.safetensors").write_bytes(b"old safe weights")
    (source / ".model-deployment.json").write_text(
        json.dumps({"repository": repository, "revision": "commit-sha"})
    )
    template = tmp_path / "chat_template.jinja"
    template.write_text("{{ messages }}")
    monkeypatch.setattr(
        model_management,
        "_config",
        lambda repository, revision, token: (
            "commit-sha",
            config,
            {
                "config.json",
                "processor_config.json",
                "tokenizer.json",
                "model.safetensors",
                "chat_template.jinja",
            },
        ),
    )
    monkeypatch.setattr(
        model_management,
        "_runtime_status",
        lambda runtime: {"available": True, "reason": None},
    )

    def download_template(**kwargs):
        assert kwargs == {
            "repo_id": repository,
            "filename": "chat_template.jinja",
            "revision": "commit-sha",
            "token": None,
        }
        return template

    monkeypatch.setattr(model_management, "_hub", lambda: (None, download_template, None))
    verified_paths = []

    def fail_verification(path, key, runtime, precision):
        verified_paths.append(path)
        assert (path / "chat_template.jinja").read_text() == "{{ messages }}"
        assert not (source / "chat_template.jinja").exists()
        raise api.ModelPreparationUnavailableError("verification failed")

    monkeypatch.setattr(model_management, "_verify", fail_verification)

    with pytest.raises(api.ModelPreparationUnavailableError, match="verification failed"):
        api.prepare_huggingface_model(
            repository, "gemma-4-e4b", runtime="transformers", models_root=tmp_path
        )

    assert verified_paths and verified_paths[0].name == "source"
    assert (source / "model.safetensors").read_bytes() == b"old safe weights"
    assert (source / ".model-deployment.json").is_file()
    assert not (source / "chat_template.jinja").exists()
    assert not (tmp_path / "models.local.json").exists()
    assert not list(tmp_path.glob(".gemma-4-e4b-stage-*"))


def test_managed_multilingual_tokenizer_keeps_language_selection(tmp_path):
    root = tmp_path / "models"
    source = root / "sources" / "custom"
    source.mkdir(parents=True)
    (root / "models.local.json").write_text(json.dumps({"custom": "org/custom"}))
    (source / "config.json").write_text(json.dumps({"model_type": "whisper"}))
    (source / "tokenizer.json").write_text(
        json.dumps({"added_tokens": [{"content": "<|en|>"}, {"content": "<|th|>"}]})
    )

    model = next(item for item in api.list_available_models(catalog_path=root / "models.local.json") if item["key"] == "custom")

    assert "th" in model["languages"]


def test_catalog_marks_generic_artifact_precision_as_unknown(tmp_path, monkeypatch):
    root = tmp_path / "models"
    generic = root / "ctranslate2-custom"
    generic.mkdir(parents=True)
    (generic / "model.bin").write_bytes(b"converted")
    (root / "models.local.json").write_text(json.dumps({"custom": "org/custom"}))
    monkeypatch.setenv("SPEECH_TO_TEXT_MODELS_DIR", str(root))

    model = next(item for item in api.list_available_models() if item["key"] == "custom")
    runtime = next(item for item in model["runtimes"] if item["key"] == "ctranslate2")

    assert runtime["has_generic_artifact"] is True
    assert runtime["installed_precisions"] == []


def test_catalog_reads_pinned_source_identity_for_registered_source_only_model(tmp_path):
    root = tmp_path / "models"
    source = root / "sources" / "custom"
    source.mkdir(parents=True)
    (root / "models.local.json").write_text(json.dumps({"custom": "org/custom"}))
    (source / ".model-deployment.json").write_text(
        json.dumps({"repository": "org/custom", "revision": "9f3a1c2"})
    )

    model = next(item for item in api.list_available_models(catalog_path=root / "models.local.json") if item["key"] == "custom")

    assert model["installed"] is False
    assert model["repository"] == "org/custom"
    assert model["revision"] == "9f3a1c2"


def test_catalog_uses_registry_repository_and_ignores_mismatched_revision(tmp_path):
    root = tmp_path / "models"
    source = root / "sources" / "custom"
    source.mkdir(parents=True)
    (root / "models.local.json").write_text(json.dumps({"custom": "org/canonical"}))
    (source / ".model-deployment.json").write_text(
        json.dumps({"repository": "org/other", "revision": "wrong-revision"})
    )

    model = next(item for item in api.list_available_models(catalog_path=root / "models.local.json") if item["key"] == "custom")

    assert model["repository"] == "org/canonical"
    assert model["revision"] is None


def test_catalog_recovers_converted_only_identity_from_legacy_export_marker(tmp_path):
    root = tmp_path / "models"
    artifact = root / "ctranslate2-legacy-int8"
    artifact.mkdir(parents=True)
    (artifact / "model.bin").write_bytes(b"converted")
    (artifact / ".speech_to_text_export.json").write_text(
        json.dumps({"repository": "org/legacy-whisper", "revision": "ab91fe2"})
    )

    model = next(item for item in api.list_available_models(catalog_path=root / "models.local.json") if item["key"] == "legacy")

    assert model["repository"] == "org/legacy-whisper"
    assert model["revision"] == "ab91fe2"


def test_conversion_verifies_staged_model_before_publishing(tmp_path, monkeypatch):
    _remote(monkeypatch)
    monkeypatch.setattr(model_management, "_download_source", _download)
    verified = []

    def convert(source, destination, runtime, precision):
        destination.mkdir(parents=True)
        (destination / "model.bin").write_bytes(b"converted")

    monkeypatch.setattr(model_management, "_convert", convert)
    monkeypatch.setattr(model_management, "_verify", lambda path, key, runtime, precision: verified.append(path))

    result = api.prepare_huggingface_model("org/whisper", "custom", models_root=tmp_path)

    assert result["verified"] is True
    assert verified and verified[0].name == "artifact"
    assert (tmp_path / "ctranslate2-custom-int8/model.bin").is_file()


def test_failed_verification_does_not_publish_artifact_or_registry(tmp_path, monkeypatch):
    _remote(monkeypatch)
    monkeypatch.setattr(model_management, "_download_source", _download)

    def convert(source, destination, runtime, precision):
        destination.mkdir(parents=True)
        (destination / "model.bin").write_bytes(b"converted")

    monkeypatch.setattr(model_management, "_convert", convert)
    monkeypatch.setattr(model_management, "_verify", lambda *args: (_ for _ in ()).throw(api.ModelPreparationUnavailableError("verification failed")))

    with pytest.raises(api.ModelPreparationUnavailableError, match="verification failed"):
        api.prepare_huggingface_model("org/whisper", "custom", models_root=tmp_path)

    assert not (tmp_path / "ctranslate2-custom-int8").exists()
    assert not (tmp_path / "models.local.json").exists()
    assert not (tmp_path / "sources/custom").exists()
    assert not list(tmp_path.glob(".custom-stage-*"))


def test_invalid_key_and_repository_conflict_are_rejected(tmp_path, monkeypatch):
    _remote(monkeypatch)
    with pytest.raises(api.ModelPreparationError):
        api.prepare_huggingface_model("org/whisper", "../outside", operation="download", models_root=tmp_path)

    (tmp_path / "models.local.json").write_text(json.dumps({"custom": "other/repository"}))
    with pytest.raises(api.ModelPreparationError, match="different repository"):
        api.prepare_huggingface_model("org/whisper", "custom", operation="download", models_root=tmp_path)


def test_remote_failure_does_not_echo_access_token(monkeypatch):
    secret = "hf_secret_value"

    class HfApi:
        def model_info(self, **kwargs):
            assert kwargs["token"] == secret
            raise RuntimeError(secret)

    monkeypatch.setattr(model_management, "_hub", lambda: (HfApi, None, None))

    with pytest.raises(api.ModelPreparationUnavailableError) as error:
        api.inspect_huggingface_model("org/whisper", token=secret)
    assert secret not in str(error.value)
