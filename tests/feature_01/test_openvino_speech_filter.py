"""OpenVINO no-speech decoding configuration and adapter behavior."""

from types import SimpleNamespace

import pytest

from speech_to_text.features.model_deployment.runtime import CTranslate2Adapter, OpenVINOAdapter


def test_openvino_speech_filter_defaults_and_flow_snapshot_are_detached(api):
    supplied = {
        "enabled": True,
        "no_speech_threshold": 0.72,
        "logprob_threshold": -0.8,
    }
    options = api.normalize_openvino_speech_filter(supplied)
    assert options == supplied
    options["enabled"] = False
    assert supplied["enabled"] is True
    assert api.normalize_openvino_speech_filter() == {
        "enabled": False,
        "no_speech_threshold": 0.6,
        "logprob_threshold": -1.0,
    }

    decoding = api.normalize_decoding_options({"beam_size": 2})
    assert decoding == {
        "beam_size": 2,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        "no_repeat_ngram_size": 3,
        "repetition_penalty": 1.0,
        "openvino_speech_filter": {
            "enabled": False,
            "no_speech_threshold": 0.6,
            "logprob_threshold": -1.0,
        },
    }
    flow = api.flow_config({"decoding_options": {"openvino_speech_filter": supplied}})
    supplied["enabled"] = False
    assert flow["decoding_options"]["openvino_speech_filter"]["enabled"] is True
    flow["decoding_options"]["openvino_speech_filter"]["enabled"] = False
    assert api.normalize_decoding_options()["openvino_speech_filter"]["enabled"] is False


@pytest.mark.parametrize(
    "value",
    [
        True,
        [],
        {"unknown": 1},
        {"enabled": 1},
        {"no_speech_threshold": True},
        {"no_speech_threshold": "0.5"},
        {"no_speech_threshold": None},
        {"no_speech_threshold": float("nan")},
        {"no_speech_threshold": float("inf")},
        {"no_speech_threshold": 10**1000},
        {"no_speech_threshold": -0.01},
        {"no_speech_threshold": 1.01},
        {"logprob_threshold": True},
        {"logprob_threshold": "-1"},
        {"logprob_threshold": None},
        {"logprob_threshold": float("nan")},
        {"logprob_threshold": float("-inf")},
        {"logprob_threshold": -(10**1000)},
        {"logprob_threshold": 0.01},
    ],
)
def test_openvino_speech_filter_rejects_invalid_values(api, value):
    with pytest.raises(api.ConfigurationError):
        api.normalize_openvino_speech_filter(value)


def test_decoding_options_reject_explicit_null_openvino_filter(api):
    with pytest.raises(api.ConfigurationError, match="openvino_speech_filter"):
        api.normalize_decoding_options({"openvino_speech_filter": None})


def _openvino_adapter(generated):
    calls = []

    class Processor:
        tokenizer = None

        def __call__(self, audio, **kwargs):
            calls.append(("process", audio, kwargs))
            return SimpleNamespace(input_features="features")

        def batch_decode(self, ids, *, skip_special_tokens):
            calls.append(("decode", ids, skip_special_tokens))
            return generated

    class Model:
        config = SimpleNamespace(max_target_positions=448)
        generation_config = None

        def prepare_inputs_for_generation(self, decoder_input_ids, **kwargs):
            calls.append(("prepare", decoder_input_ids, kwargs))
            return {"decoder_input_ids": decoder_input_ids}

        def generate(self, features, **kwargs):
            calls.append(("generate", features, kwargs))
            if kwargs.get("return_timestamps"):
                prepared = self.prepare_inputs_for_generation(
                    inputs=features, input_ids="decoder-prefix"
                )
                calls.append(("prepared", prepared))
            return [] if generated == [""] else "token-ids"

    adapter = OpenVINOAdapter.__new__(OpenVINOAdapter)
    adapter.processor = Processor()
    adapter.model = Model()
    return adapter, calls


def _decoding(api, *, enabled=False):
    return api.normalize_decoding_options(
        {
            "openvino_speech_filter": {
                "enabled": enabled,
                "no_speech_threshold": 0.71,
                "logprob_threshold": -0.65,
            }
        }
    )


def test_openvino_enabled_filter_sets_scoring_kwargs_and_keeps_auto_language(api):
    adapter, calls = _openvino_adapter([" recognized speech "])
    original_prepare = adapter.model.prepare_inputs_for_generation.__func__
    result = adapter.transcribe([0.0], language="auto", decoding_options=_decoding(api, enabled=True))
    kwargs = next(call[2] for call in calls if call[0] == "generate")
    prepared = next(call[1] for call in calls if call[0] == "prepared")

    assert result == "recognized speech"
    assert kwargs["task"] == "transcribe"
    assert "language" not in kwargs
    assert kwargs["return_timestamps"] is True
    assert kwargs["no_speech_threshold"] == 0.71
    assert kwargs["logprob_threshold"] == -0.65
    assert kwargs["return_dict_in_generate"] is False
    assert kwargs["max_new_tokens"] == 444
    assert prepared == {"decoder_input_ids": "decoder-prefix"}
    assert adapter.model.prepare_inputs_for_generation.__func__ is original_prepare


def test_openvino_disabled_filter_preserves_generation_kwargs(api):
    adapter, calls = _openvino_adapter([" transcript "])
    assert adapter.transcribe([0.0], language="th", decoding_options=_decoding(api)) == "transcript"
    kwargs = next(call[2] for call in calls if call[0] == "generate")
    assert kwargs["language"] == "th"
    assert kwargs["task"] == "transcribe"
    assert not {
        "return_timestamps",
        "no_speech_threshold",
        "logprob_threshold",
        "return_dict_in_generate",
    }.intersection(kwargs)


def test_openvino_filter_can_return_an_empty_transcript(api):
    adapter, calls = _openvino_adapter([""])
    result = adapter.transcribe([0.0], language="th", decoding_options=_decoding(api, enabled=True))
    assert result == ""
    assert isinstance(result, str)
    assert any(call[0] == "decode" for call in calls)


def test_ctranslate2_keeps_existing_native_generation_options(api):
    observed = {}

    class Model:
        def transcribe(self, audio, **kwargs):
            observed.update(kwargs)
            return iter([SimpleNamespace(text=" native defaults ")]), object()

    adapter = CTranslate2Adapter.__new__(CTranslate2Adapter)
    adapter.model = Model()
    assert adapter.transcribe([0.0], language="auto", decoding_options=_decoding(api, enabled=True)) == "native defaults"
    assert observed == {
        "language": None,
        "beam_size": 1,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        "no_repeat_ngram_size": 3,
        "repetition_penalty": 1.0,
    }
