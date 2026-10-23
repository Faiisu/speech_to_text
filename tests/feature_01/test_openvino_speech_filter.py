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
        "initial_prompt": "",
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


@pytest.mark.parametrize("value", [None, 1, True, [], {}])
def test_initial_prompt_requires_a_string(api, value):
    with pytest.raises(api.ConfigurationError, match="initial_prompt"):
        api.normalize_decoding_options({"initial_prompt": value})


def test_initial_prompt_is_trimmed_and_flow_settings_are_independent(api):
    flow = api.flow_config({"decoding_options": {"initial_prompt": "  room alarm  "}})
    other = api.flow_config({})

    assert flow["decoding_options"]["initial_prompt"] == "room alarm"
    assert other["decoding_options"]["initial_prompt"] == ""
    flow["decoding_options"]["initial_prompt"] = "changed"
    assert other["decoding_options"]["initial_prompt"] == ""

    assert api.normalize_decoding_options({"initial_prompt": " \t "})["initial_prompt"] == ""


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


def test_openvino_initial_prompt_passes_rank_one_ids_and_reserves_decoder_positions(api):
    observed = {}

    class Processor:
        def __call__(self, audio, **kwargs):
            return SimpleNamespace(input_features="features")

        def get_prompt_ids(self, text, *, return_tensors):
            observed["prompt_request"] = (text, return_tensors)
            return np.asarray([50258, 100, 101], dtype=np.int64)

        def batch_decode(self, ids, **kwargs):
            return [" transcript "]

    class Model:
        config = SimpleNamespace(max_target_positions=448)
        generation_config = None

        def generate(self, features, **kwargs):
            observed["kwargs"] = kwargs
            return "ids"

    import numpy as np

    adapter = OpenVINOAdapter.__new__(OpenVINOAdapter)
    adapter.processor, adapter.model = Processor(), Model()
    assert adapter.transcribe(
        [0.0], language="th", decoding_options=api.normalize_decoding_options(
            {"initial_prompt": " names "}
        )
    ) == "transcript"
    assert observed["prompt_request"] == ("names", "pt")
    assert observed["kwargs"]["prompt_ids"].shape == (3,)
    assert observed["kwargs"]["max_new_tokens"] == 441


def test_openvino_reuses_adapter_with_independent_prompts_and_language_budgets(api):
    calls = []
    original_generation_config = SimpleNamespace(
        language="th", forced_decoder_ids=[(1, 50259)]
    )

    class Tokenizer:
        def get_decoder_prompt_ids(self, *, task, language, no_timestamps):
            assert (task, language, no_timestamps) == ("transcribe", "en", True)
            return [(1, 50259), (2, 50359), (3, 50364)]

    class Processor:
        tokenizer = Tokenizer()

        def __call__(self, audio, **kwargs):
            return SimpleNamespace(input_features="features")

        def get_prompt_ids(self, text, *, return_tensors):
            assert return_tensors == "pt"
            return np.arange({"first": 2, "second": 4}[text], dtype=np.int64)

        def batch_decode(self, ids, **kwargs):
            return ["transcript"]

    class Model:
        config = SimpleNamespace(max_target_positions=448)
        generation_config = original_generation_config

        def generate(self, features, **kwargs):
            calls.append(kwargs)
            return "ids"

    import numpy as np

    adapter = OpenVINOAdapter.__new__(OpenVINOAdapter)
    adapter.processor, adapter.model = Processor(), Model()
    settings = [
        ("first", "auto"),
        ("second", "en"),
        ("", "auto"),
    ]
    for prompt, language in settings:
        options = api.normalize_decoding_options({"initial_prompt": prompt})
        assert adapter.transcribe([0.0], language=language, decoding_options=options) == "transcript"

    assert [call.get("prompt_ids") is not None for call in calls] == [True, True, False]
    assert [call["max_new_tokens"] for call in calls] == [442, 440, 444]
    assert calls[0]["prompt_ids"].shape == (2,)
    assert calls[1]["prompt_ids"].shape == (4,)
    assert calls[0]["generation_config"].language is None
    assert calls[0]["generation_config"].forced_decoder_ids is None
    assert calls[2]["generation_config"].language is None
    assert calls[2]["generation_config"].forced_decoder_ids is None
    assert original_generation_config.language == "th"
    assert original_generation_config.forced_decoder_ids == [(1, 50259)]


def test_openvino_initial_prompt_with_no_output_budget_fails_clearly(api):
    class Processor:
        def __call__(self, audio, **kwargs):
            return SimpleNamespace(input_features="features")

        def get_prompt_ids(self, text, *, return_tensors):
            return np.zeros((444,), dtype=np.int64)

        def batch_decode(self, ids, **kwargs):
            return ["unused"]

    class Model:
        config = SimpleNamespace(max_target_positions=448)
        generation_config = None

        def generate(self, features, **kwargs):
            raise AssertionError("must reject the prompt before generation")

    import numpy as np

    adapter = OpenVINOAdapter.__new__(OpenVINOAdapter)
    adapter.processor, adapter.model = Processor(), Model()
    with pytest.raises(ValueError, match="leaving no output token"):
        adapter.transcribe(
            [0.0], language="auto", decoding_options=api.normalize_decoding_options(
                {"initial_prompt": "too long"}
            )
        )


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


def test_ctranslate2_passes_only_nonempty_initial_prompt(api):
    calls = []

    class Model:
        def transcribe(self, audio, **kwargs):
            calls.append(kwargs)
            return iter([SimpleNamespace(text=" transcript ")]), object()

    adapter = CTranslate2Adapter.__new__(CTranslate2Adapter)
    adapter.model = Model()
    for prompt in ("", "names"):
        assert adapter.transcribe(
            [0.0], language="en", decoding_options=api.normalize_decoding_options(
                {"initial_prompt": prompt}
            )
        ) == "transcript"
    assert "initial_prompt" not in calls[0]
    assert calls[1]["initial_prompt"] == "names"
