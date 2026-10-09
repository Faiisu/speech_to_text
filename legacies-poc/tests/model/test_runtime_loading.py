import pytest
from pathlib import Path

import runtimes


def test_unknown_model_fails_before_any_runtime_loader(monkeypatch):
    monkeypatch.setattr(runtimes, "discover", lambda: {"turbo": {}})
    monkeypatch.setattr(runtimes, "PyTorchRuntime", lambda *a, **k: pytest.fail("loaded unknown model"))
    with pytest.raises(ValueError, match="Unknown model 'missing'"):
        runtimes.load_runtime("pytorch", "missing")


def test_missing_runtime_weights_propagate_as_load_failure(monkeypatch):
    monkeypatch.setattr(runtimes, "discover", lambda: {"turbo": {}})
    monkeypatch.setattr(runtimes, "converted_dir", lambda *args: Path("/missing"))
    with pytest.raises(FileNotFoundError, match="No converted model"):
        runtimes.load_runtime("ctranslate2", "turbo")


def test_engine_keeps_runtime_load_failure_observable(monkeypatch):
    from stations.config import Settings
    from stations.engine import Engine

    def fail(*args):
        raise OSError("accelerator unavailable")

    monkeypatch.setattr("stations.engine.load_runtime", fail)
    engine = Engine(Settings(runtime="pytorch"))
    with pytest.raises(OSError, match="accelerator unavailable"):
        engine.load_model()
    assert engine._runtime is None


def test_pytorch_runtime_passes_fixed_language_to_production_pipeline(monkeypatch):
    import numpy as np
    import transcribe

    calls = []

    class Pipeline:
        def __call__(self, audio, generate_kwargs):
            calls.append((audio, generate_kwargs))
            return {"text": " สวัสดีครับ "}

    monkeypatch.setattr(transcribe, "pick_device", lambda: "cpu")
    monkeypatch.setattr(transcribe, "load_pipeline", lambda model, device: Pipeline())
    runtime = runtimes.PyTorchRuntime("turbo")
    audio = np.ones(16_000, dtype="float32")

    assert runtime.transcribe(audio, "th") == "สวัสดีครับ"
    assert calls[0][0]["sampling_rate"] == 16_000
    assert calls[0][1]["language"] == "th"
    assert calls[0][1]["task"] == "transcribe"
