"""Production adapter API contracts with external libraries isolated at their boundary."""

import json
import sys
import queue
from types import ModuleType, SimpleNamespace

import pytest

from speech_to_text.features.model_deployment import capacity
from speech_to_text.features.model_deployment.errors import ModelLoadError
from speech_to_text.features.model_deployment.runtime import CTranslate2Adapter, OpenVINOAdapter
from speech_to_text.features.model_deployment.process_topology import ProcessFlowGroup


def test_source_precision_conversion_is_cached_and_reused(tmp_path, monkeypatch):
    root = tmp_path / "models"
    root.mkdir()
    (root / "models.local.json").write_text(json.dumps({"custom-whisper": "org/custom-whisper"}))
    monkeypatch.setenv("SPEECH_TO_TEXT_MODELS_DIR", str(root))
    calls = {"snapshot": 0, "export": 0}

    class Processor:
        @classmethod
        def from_pretrained(cls, path, **kwargs):
            return cls()

        def save_pretrained(self, path):
            (path / "processor.json").write_text("{}")

    class Model:
        @classmethod
        def from_pretrained(cls, path, **kwargs):
            if kwargs.get("export"):
                calls["export"] += 1
                assert kwargs["load_in_8bit"] is False
            return cls()

        def save_pretrained(self, path):
            (path / "openvino_model.xml").write_text("converted")

    optimum = ModuleType("optimum")
    optimum.__path__ = []
    intel = ModuleType("optimum.intel")
    intel.OVModelForSpeechSeq2Seq = Model
    intel.OVWeightQuantizationConfig = lambda **kwargs: kwargs
    transformers = ModuleType("transformers")
    transformers.AutoProcessor = Processor
    hub = ModuleType("huggingface_hub")

    def snapshot_download(repository):
        calls["snapshot"] += 1
        assert repository == "org/custom-whisper"
        source = tmp_path / "snapshot"
        source.mkdir(exist_ok=True)
        return str(source)

    hub.snapshot_download = snapshot_download
    monkeypatch.setitem(sys.modules, "optimum", optimum)
    monkeypatch.setitem(sys.modules, "optimum.intel", intel)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    config = {"model": "custom-whisper", "runtime": "openvino-gpu", "precision": "source"}

    OpenVINOAdapter(config)
    cached = root / "openvino-custom-whisper-source"
    assert (cached / "openvino_model.xml").is_file()
    assert (cached / "processor.json").is_file()
    OpenVINOAdapter(config)

    assert calls == {"snapshot": 1, "export": 1}


def test_openvino_precision_selects_requested_quantization(tmp_path, monkeypatch):
    root = tmp_path / "models"
    converted = root / "openvino-turbo-int8"
    converted.mkdir(parents=True)
    monkeypatch.setenv("SPEECH_TO_TEXT_MODELS_DIR", str(root))
    observed = {}

    class Processor:
        @classmethod
        def from_pretrained(cls, path, **kwargs):
            return cls()

    class Model:
        @classmethod
        def from_pretrained(cls, path, **kwargs):
            observed.update(kwargs)
            return cls()

    optimum = ModuleType("optimum")
    optimum.__path__ = []
    intel = ModuleType("optimum.intel")
    intel.OVModelForSpeechSeq2Seq = Model
    intel.OVWeightQuantizationConfig = lambda **kwargs: ("quant", kwargs)
    transformers = ModuleType("transformers")
    transformers.AutoProcessor = Processor
    hub = ModuleType("huggingface_hub")
    hub.snapshot_download = lambda repository: (_ for _ in ()).throw(AssertionError("must use converted weights"))
    monkeypatch.setitem(sys.modules, "optimum", optimum)
    monkeypatch.setitem(sys.modules, "optimum.intel", intel)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)

    OpenVINOAdapter({"model": "turbo", "runtime": "openvino-gpu", "precision": "int8"})

    assert observed == {"device": "GPU", "quantization_config": ("quant", {"bits": 8})}


def test_openvino_generation_budget_includes_whisper_decoder_prompt():
    observed = {}

    class Tokenizer:
        def get_decoder_prompt_ids(self, *, task, language, no_timestamps):
            assert (task, language, no_timestamps) == ("transcribe", "th", True)
            return [(1, 10), (2, 11), (3, 12)]

    class Processor:
        tokenizer = Tokenizer()

        def __call__(self, audio, **kwargs):
            return SimpleNamespace(input_features="features")

        def batch_decode(self, ids, *, skip_special_tokens):
            return [" transcript "]

    class Model:
        config = SimpleNamespace(max_target_positions=448)

        def generate(self, features, **kwargs):
            observed.update(features=features, kwargs=kwargs)
            return "ids"

    adapter = OpenVINOAdapter.__new__(OpenVINOAdapter)
    adapter.processor = Processor()
    adapter.model = Model()

    transcript = adapter.transcribe(
        [0.0], language="th", decoding_options={
            "beam_size": 1,
            "temperature": 0.0,
            "condition_on_previous_text": False,
            "no_repeat_ngram_size": 0,
            "repetition_penalty": 1.0,
        }
    )

    assert transcript == "transcript"
    assert observed["kwargs"]["max_new_tokens"] == 444


def test_openvino_auto_language_budget_reserves_whisper_prompt_without_forced_ids():
    observed = {}

    class Processor:
        def __call__(self, audio, **kwargs):
            return SimpleNamespace(input_features="features")

        def batch_decode(self, ids, *, skip_special_tokens):
            return ["transcript"]

    class Model:
        config = SimpleNamespace()

        def generate(self, features, **kwargs):
            observed.update(kwargs)
            return "ids"

    adapter = OpenVINOAdapter.__new__(OpenVINOAdapter)
    adapter.processor = Processor()
    adapter.model = Model()

    transcript = adapter.transcribe(
        [0.0], language="auto", decoding_options={
            "beam_size": 1,
            "temperature": 0.0,
            "condition_on_previous_text": False,
            "no_repeat_ngram_size": 0,
            "repetition_penalty": 1.0,
        }
    )

    assert transcript == "transcript"
    assert observed["max_new_tokens"] == 444


def test_openvino_auto_language_budget_includes_unforced_timestamp_token():
    observed = {}

    class Processor:
        def __call__(self, audio, **kwargs):
            return SimpleNamespace(input_features="features")

        def batch_decode(self, ids, *, skip_special_tokens):
            return ["transcript"]

    class Model:
        config = SimpleNamespace(max_target_positions=448)
        generation_config = SimpleNamespace(forced_decoder_ids=[(1, None), (2, 50359)])

        def generate(self, features, **kwargs):
            observed.update(kwargs)
            return "ids"

    adapter = OpenVINOAdapter.__new__(OpenVINOAdapter)
    adapter.processor = Processor()
    adapter.model = Model()

    transcript = adapter.transcribe(
        [0.0], language="auto", decoding_options={
            "beam_size": 1,
            "temperature": 0.0,
            "condition_on_previous_text": False,
            "no_repeat_ngram_size": 0,
            "repetition_penalty": 1.0,
        }
    )

    assert transcript == "transcript"
    assert observed["max_new_tokens"] == 444


def test_capacity_tool_reports_missing_prerequisites_without_fake_measurements(monkeypatch):
    monkeypatch.setattr(capacity, "start_multiprocess_microphone_flows",
                        lambda *args, **kwargs: (_ for _ in ()).throw(ModelLoadError("OpenVINO GPU unavailable")))
    args = SimpleNamespace(model="turbo", runtime="openvino-gpu", precision="source",
        queue_capacity=6, enqueue_timeout=1, language="th", chunk_seconds=5,
        silence_threshold=0.05, topology="shared-model", device=["target microphone"],
        duration_seconds=10, stop_timeout=10)

    result = capacity.run_benchmark(args)

    assert result["status"] == "unavailable"
    assert result["prerequisite_error"] == "OpenVINO GPU unavailable"
    assert result["measurements"] is None


@pytest.mark.parametrize("field,value", [
    ("duration_seconds", float("nan")),
    ("duration_seconds", float("inf")),
    ("stop_timeout", float("nan")),
    ("stop_timeout", float("inf")),
    ("enqueue_timeout", float("nan")),
])
def test_capacity_rejects_nonfinite_timeouts_before_start(field, value, monkeypatch):
    args = SimpleNamespace(model="turbo", runtime="openvino-gpu", precision="source",
        queue_capacity=6, enqueue_timeout=1, language="th", chunk_seconds=5,
        silence_threshold=0.05, topology="shared-model", device=["target microphone"],
        duration_seconds=10, stop_timeout=10)
    setattr(args, field, value)
    monkeypatch.setattr(capacity, "start_multiprocess_microphone_flows",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must validate before startup")))

    with pytest.raises(ValueError, match="finite"):
        capacity.run_benchmark(args)


@pytest.mark.parametrize("status,verdict,expected", [
    ("unavailable", None, 2),
    ("completed", "fail", 1),
    ("completed", "inconclusive-insufficient-workload", 3),
    ("completed", "pass", 0),
])
def test_capacity_cli_exit_code_matches_evidence_status(status, verdict, expected, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["capacity", "--topology", "shared-model", "--device", "mic"])
    monkeypatch.setattr(capacity, "run_benchmark", lambda args: {"status": status, "realtime_verdict": verdict})

    assert capacity.main() == expected
    assert json.loads(capsys.readouterr().out)["status"] == status


def test_capacity_cli_rejects_nan_timeout_before_running(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["capacity", "--topology", "shared-model", "--device", "mic",
                                       "--stop-timeout", "nan"])
    monkeypatch.setattr(capacity, "run_benchmark",
                        lambda args: (_ for _ in ()).throw(AssertionError("must validate before running")))

    with pytest.raises(SystemExit) as error:
        capacity.main()

    assert error.value.code == 2


def test_capacity_failure_uses_abort_cleanup(monkeypatch):
    class FailedGroup:
        _input_queue = None
        _model_process = None
        sessions = []

        def __init__(self):
            self.aborted = False

        def stop(self, *, timeout):
            raise TimeoutError("stop timed out")

        def abort(self, *, timeout):
            self.aborted = True

    group = FailedGroup()
    monkeypatch.setattr(capacity, "start_multiprocess_microphone_flows", lambda *args, **kwargs: group)
    args = SimpleNamespace(model="turbo", runtime="openvino-gpu", precision="source",
        queue_capacity=6, enqueue_timeout=1, language="th", chunk_seconds=5,
        silence_threshold=0.05, topology="shared-model", device=["target microphone"],
        duration_seconds=0.001, stop_timeout=1)

    with pytest.raises(TimeoutError, match="stop timed out"):
        capacity.run_benchmark(args)
    assert group.aborted


def test_process_group_abort_terminates_and_joins_owned_processes():
    class FakeProcess:
        def __init__(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.alive = False

        def kill(self):
            self.alive = False

        def join(self, timeout=None):
            pass

    class FakeManager:
        stopped = False

        def shutdown(self):
            self.stopped = True

    class FakeContext:
        Queue = staticmethod(queue.Queue)

    capture, model, manager = FakeProcess(), FakeProcess(), FakeManager()
    session = SimpleNamespace(source_id="mic", process=capture)
    group = ProcessFlowGroup([session], FakeContext(), queue.Queue(), model_process=model, manager=manager)

    group.abort(timeout=1)

    assert not capture.is_alive()
    assert not model.is_alive()
    assert manager.stopped


def test_ctranslate2_adapter_loads_precision_suffixed_converted_cache(tmp_path, monkeypatch):
    root = tmp_path / "models"
    converted = root / "ctranslate2-custom-int8"
    converted.mkdir(parents=True)
    (converted / "model.bin").write_bytes(b"weights")
    monkeypatch.setenv("SPEECH_TO_TEXT_MODELS_DIR", str(root))
    observed = {}

    class WhisperModel:
        def __init__(self, path, *, device, compute_type):
            observed.update(path=path, device=device, compute_type=compute_type)

    faster = ModuleType("faster_whisper")
    faster.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", faster)

    adapter = CTranslate2Adapter({"model": "custom", "runtime": "ctranslate2", "precision": "int8"})

    assert adapter.model is not None
    assert observed == {"path": str(converted), "device": "cpu", "compute_type": "int8"}
