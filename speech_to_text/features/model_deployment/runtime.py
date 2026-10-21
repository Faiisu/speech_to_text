"""Lazy production model adapters. Test injection stays at this boundary."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import numpy as np

from .catalog import default_models_root, model_read_roots, resolve_repository
from .errors import ModelLoadError


def _root():
    return default_models_root()


def _roots():
    return model_read_roots()


class OpenVINOAdapter:
    def __init__(self, config, *, artifact_path=None):
        try:
            from optimum.intel import (
                OVModelForSpeechSeq2Seq,
                OVWeightQuantizationConfig,
            )
            from transformers import AutoProcessor
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise ModelLoadError(
                "OpenVINO runtime requires openvino, optimum-intel[openvino], transformers, and huggingface-hub; install the speech-to-text[openvino] extra"
            ) from exc
        try:
            repository = resolve_repository(config["model"])
        except ValueError as exc:
            repository = None
            if not config["model"]:
                raise ModelLoadError(str(exc)) from exc
        family = "openvino"
        requested_precision = config["precision"]
        roots = _roots()
        root = roots[0]
        candidates = []
        for candidate_root in roots:
            candidates.extend(
                (
                    candidate_root / f"{family}-{config['model']}-{requested_precision}",
                    candidate_root / family / f"{config['model']}-{requested_precision}",
                )
            )
            if requested_precision != "source":
                candidates.extend(
                    (candidate_root / f"{family}-{config['model']}", candidate_root / family / config["model"])
                )
        path = (
            Path(artifact_path)
            if artifact_path is not None
            else next((candidate for candidate in candidates if candidate.exists()), None)
        )
        try:
            device = "GPU" if config["runtime"] == "openvino-gpu" else "CPU"
            kwargs = {"device": device, "local_files_only": True, "trust_remote_code": False}
            if requested_precision == "int8":
                kwargs["quantization_config"] = OVWeightQuantizationConfig(bits=8)
            elif requested_precision == "int4":
                kwargs["quantization_config"] = OVWeightQuantizationConfig(bits=4)
            elif requested_precision == "bf16":
                import torch

                kwargs["torch_dtype"] = torch.bfloat16
            elif requested_precision == "source":
                # Recent Optimum Intel releases otherwise auto-quantize models
                # above one billion parameters while exporting.
                kwargs["load_in_8bit"] = False
            else:
                raise ModelLoadError(
                    f"OpenVINO precision {requested_precision!r} is unsupported"
                )
            if path is not None:
                self.processor = AutoProcessor.from_pretrained(
                    path, local_files_only=True, trust_remote_code=False
                )
                self.model = OVModelForSpeechSeq2Seq.from_pretrained(path, **kwargs)
            else:
                if repository is None:
                    raise ModelLoadError(
                        f"Converted OpenVINO weights for {config['model']} were not found under {root}"
                    )
                # Explicit model loading may install source weights; catalog scans remain offline.
                source_path = snapshot_download(repository)
                self.processor = AutoProcessor.from_pretrained(
                    source_path, local_files_only=True, trust_remote_code=False
                )
                self.model = OVModelForSpeechSeq2Seq.from_pretrained(
                    source_path, export=True, **kwargs
                )
            # Cache one precision-specific copy so repeated loads reuse the
            # selected conversion instead of silently loading source weights.
            if path is None:
                destination = root / f"{family}-{config['model']}-{requested_precision}"
                destination.mkdir(parents=True, exist_ok=True)
                self.model.save_pretrained(destination)
                self.processor.save_pretrained(destination)
        except Exception as exc:
            raise ModelLoadError(
                f"Unable to load {repository} with {config['runtime']} at {config['precision']} precision: {exc}"
            ) from exc

    def transcribe(self, audio, *, language, decoding_options):
        features = self.processor(
            audio, sampling_rate=16000, return_tensors="pt"
        ).input_features
        kwargs = {
            "max_new_tokens": self._max_new_tokens(language),
            "num_beams": decoding_options["beam_size"],
            "do_sample": decoding_options["temperature"] > 0,
            "temperature": decoding_options["temperature"],
            "condition_on_prev_tokens": decoding_options["condition_on_previous_text"],
            "no_repeat_ngram_size": decoding_options["no_repeat_ngram_size"],
            "repetition_penalty": decoding_options["repetition_penalty"],
        }
        kwargs["task"] = "transcribe"
        speech_filter = decoding_options.get("openvino_speech_filter", {})
        if speech_filter.get("enabled", False):
            kwargs.update(
                return_timestamps=True,
                no_speech_threshold=speech_filter["no_speech_threshold"],
                logprob_threshold=speech_filter["logprob_threshold"],
                return_dict_in_generate=False,
            )
        if language != "auto":
            kwargs["language"] = language
        elif getattr(self.model, "generation_config", None) is not None:
            # Auto must not inherit a checkpoint's fixed language or translation task.
            generation_config = deepcopy(self.model.generation_config)
            generation_config.language = None
            generation_config.forced_decoder_ids = None
            kwargs["generation_config"] = generation_config
        prepare_inputs = None
        local_prepare_inputs = None
        had_local_prepare_inputs = False
        if speech_filter.get("enabled", False):
            prepare_inputs = self.model.prepare_inputs_for_generation
            model_attributes = getattr(self.model, "__dict__", {})
            had_local_prepare_inputs = "prepare_inputs_for_generation" in model_attributes
            if had_local_prepare_inputs:
                local_prepare_inputs = model_attributes["prepare_inputs_for_generation"]

            def prepare_whisper_inputs(*args, **kwargs):
                # Transformers' Whisper scorer calls this with `input_ids`,
                # while Optimum OpenVINO requires `decoder_input_ids`.
                if "decoder_input_ids" not in kwargs and "input_ids" in kwargs:
                    kwargs["decoder_input_ids"] = kwargs["input_ids"]
                return prepare_inputs(*args, **kwargs)

            self.model.prepare_inputs_for_generation = prepare_whisper_inputs
        try:
            ids = self.model.generate(features, **kwargs)
        finally:
            if speech_filter.get("enabled", False):
                if had_local_prepare_inputs:
                    self.model.prepare_inputs_for_generation = local_prepare_inputs
                else:
                    del self.model.__dict__["prepare_inputs_for_generation"]
        return self.processor.batch_decode(ids, skip_special_tokens=True)[0].strip()

    def _max_new_tokens(self, language):
        config = self.model.config
        target_limit = getattr(config, "max_target_positions", None)
        if target_limit is None:
            generation_config = getattr(self.model, "generation_config", None)
            target_limit = getattr(generation_config, "max_length", 448)

        tokenizer = getattr(self.processor, "tokenizer", None)
        prompt_ids = None
        get_prompt_ids = getattr(tokenizer, "get_decoder_prompt_ids", None)
        if callable(get_prompt_ids) and language != "auto":
            prompt_ids = get_prompt_ids(
                task="transcribe", language=language, no_timestamps=True
            )
        if prompt_ids is not None:
            # get_decoder_prompt_ids omits the initial decoder start token.
            decoder_prefix_length = 1 + len(prompt_ids)
        else:
            generation_config = getattr(self.model, "generation_config", None)
            forced_decoder_ids = getattr(generation_config, "forced_decoder_ids", None)
            if forced_decoder_ids:
                decoder_prefix_length = max(
                    4, 1 + max(position for position, _ in forced_decoder_ids)
                )
            else:
                # Whisper's auto-language path still adds language, task, and
                # timestamp-control tokens after the decoder start token.
                decoder_prefix_length = 4

        max_new_tokens = int(target_limit) - decoder_prefix_length
        if max_new_tokens < 1:
            raise ValueError(
                f"Whisper decoder prompt uses {decoder_prefix_length} positions, "
                f"exceeding its {target_limit}-position target limit"
            )
        return max_new_tokens

    def close(self):
        self.model = None
        self.processor = None


class CTranslate2Adapter:
    def __init__(self, config, *, artifact_path=None):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ModelLoadError(
                "CTranslate2 runtime requires faster-whisper; install the speech-to-text[ctranslate2] extra"
            ) from exc
        model = config["model"]
        precision = config["precision"]
        roots = _roots()
        root = roots[0]
        candidates = []
        for candidate_root in roots:
            candidates.extend(
                (
                    candidate_root / f"ctranslate2-{model}-{precision}",
                    candidate_root / "ctranslate2" / f"{model}-{precision}",
                    candidate_root / f"ctranslate2-{model}",
                    candidate_root / "ctranslate2" / model,
                )
            )
        path = (
            Path(artifact_path)
            if artifact_path is not None
            else next((candidate for candidate in candidates if candidate.is_dir()), None)
        )
        if path is None:
            raise ModelLoadError(
                f"No CTranslate2 weights for {config['model']} at {precision} precision under {root}; convert/install the model first"
            )
        try:
            self.model = WhisperModel(
                str(path),
                device="cpu",
                compute_type=config["precision"],
                local_files_only=True,
            )
        except Exception as exc:
            raise ModelLoadError(
                f"Unable to load CTranslate2 model from {path}: {exc}"
            ) from exc

    def transcribe(self, audio, *, language, decoding_options):
        segments, _ = self.model.transcribe(
            np.asarray(audio, dtype=np.float32),
            language=None if language == "auto" else language,
            beam_size=decoding_options["beam_size"],
            temperature=decoding_options["temperature"],
            condition_on_previous_text=decoding_options["condition_on_previous_text"],
            no_repeat_ngram_size=decoding_options["no_repeat_ngram_size"],
            repetition_penalty=decoding_options["repetition_penalty"],
        )
        return "".join(segment.text for segment in segments).strip()

    def close(self):
        self.model = None


def create_runtime(config, *, artifact_path=None):
    try:
        if config["runtime"].startswith("openvino"):
            return OpenVINOAdapter(config, artifact_path=artifact_path)
        if config["runtime"] == "ctranslate2":
            return CTranslate2Adapter(config, artifact_path=artifact_path)
    except ModelLoadError:
        raise
    except Exception as exc:
        raise ModelLoadError(
            f"Unable to initialize {config['runtime']}: {exc}"
        ) from exc
    raise ModelLoadError(f"No production adapter is available for {config['runtime']}")
