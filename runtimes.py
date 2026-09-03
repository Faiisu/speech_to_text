"""Interchangeable ways of running the same Typhoon Whisper model.

The model is always one of MODEL_REPOS; what changes here is the machinery
that executes it, because the right choice depends entirely on the hardware:

  pytorch          PyTorch — Apple MPS if present, otherwise CPU float32.
                   Works everywhere. Slowest option on an Intel box.
  openvino-gpu     OpenVINO on an Intel integrated GPU.
  openvino-cpu     OpenVINO on CPU.
  ctranslate2      CTranslate2 int8 on CPU. Wants AVX-VNNI to be worth it.

OpenVINO and CTranslate2 need the weights converted first — see
convert_model.py. Everything reports *why* it is unavailable rather than
failing at the point of use.
"""

from __future__ import annotations

import importlib.util
import platform
from pathlib import Path

import numpy as np

from transcribe import MODEL_REPOS, SAMPLE_RATE

MODELS_DIR = Path(__file__).parent / "models"

# Generation settings that stop Whisper looping the same phrase when there's
# no clear speech to anchor on (see ADR 0004). Applied wherever the runtime
# exposes the knobs.
NO_REPEAT_NGRAM_SIZE = 3
REPETITION_PENALTY = 1.3


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def converted_dir(runtime: str, model_key: str) -> Path:
    """Where convert_model.py puts the converted weights for this combination."""
    family = "openvino" if runtime.startswith("openvino") else runtime
    return MODELS_DIR / f"{family}-{model_key}"


class Runtime:
    """Wraps one loaded model so callers only need .transcribe(audio)."""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    def transcribe(self, audio: np.ndarray) -> str:
        raise NotImplementedError


class PyTorchRuntime(Runtime):
    def __init__(self, model_key: str):
        from transcribe import load_pipeline, pick_device

        device = pick_device()
        self._pipeline = load_pipeline(model_key, device)
        super().__init__("pytorch", f"PyTorch on {device}")

    def transcribe(self, audio: np.ndarray) -> str:
        result = self._pipeline(
            {"array": audio, "sampling_rate": SAMPLE_RATE},
            generate_kwargs={
                "no_repeat_ngram_size": NO_REPEAT_NGRAM_SIZE,
                "repetition_penalty": REPETITION_PENALTY,
            },
        )
        return result["text"].strip()


class OpenVINORuntime(Runtime):
    def __init__(self, model_key: str, device: str):
        from optimum.intel import OVModelForSpeechSeq2Seq
        from transformers import AutoProcessor

        path = converted_dir(f"openvino-{device.lower()}", model_key)
        if not path.exists():
            raise FileNotFoundError(
                f"No converted model at {path}. Run:\n"
                f"    uv run python convert_model.py --runtime openvino --model {model_key}"
            )

        self._model = OVModelForSpeechSeq2Seq.from_pretrained(path, device=device)
        self._processor = AutoProcessor.from_pretrained(path)
        super().__init__(f"openvino-{device.lower()}", f"OpenVINO on {device}")

    def transcribe(self, audio: np.ndarray) -> str:
        features = self._processor(
            audio, sampling_rate=SAMPLE_RATE, return_tensors="pt"
        ).input_features
        tokens = self._model.generate(
            features,
            no_repeat_ngram_size=NO_REPEAT_NGRAM_SIZE,
            repetition_penalty=REPETITION_PENALTY,
        )
        return self._processor.batch_decode(tokens, skip_special_tokens=True)[0].strip()


class CTranslate2Runtime(Runtime):
    def __init__(self, model_key: str):
        from faster_whisper import WhisperModel

        path = converted_dir("ctranslate2", model_key)
        if not path.exists():
            raise FileNotFoundError(
                f"No converted model at {path}. Run:\n"
                f"    uv run python convert_model.py --runtime ctranslate2 --model {model_key}"
            )

        self._model = WhisperModel(str(path), device="cpu", compute_type="int8")
        super().__init__("ctranslate2", "CTranslate2 int8 on CPU")

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _ = self._model.transcribe(
            audio,
            language="th",
            no_repeat_ngram_size=NO_REPEAT_NGRAM_SIZE,
            repetition_penalty=REPETITION_PENALTY,
        )
        return "".join(segment.text for segment in segments).strip()


def _intel_gpu_present() -> bool:
    return any(Path("/dev/dri").glob("renderD*")) if Path("/dev/dri").exists() else False


def probe(model_key: str = "turbo") -> list[dict]:
    """What can actually run here, and for anything that can't, why not."""
    entries = []

    entries.append(
        {
            "name": "pytorch",
            "label": "PyTorch (CPU / Apple GPU)",
            "available": True,
            "reason": "always available; slowest option on an Intel machine",
            "needs_conversion": False,
        }
    )

    has_openvino = _installed("openvino") and _installed("optimum")
    for device, label in (("GPU", "OpenVINO · Intel GPU"), ("CPU", "OpenVINO · CPU")):
        name = f"openvino-{device.lower()}"
        converted = converted_dir(name, model_key).exists()
        if not has_openvino:
            reason = "openvino + optimum-intel not installed"
        elif device == "GPU" and not _intel_gpu_present():
            reason = "no Intel GPU render node (/dev/dri/renderD*) on this machine"
        elif not converted:
            reason = f"model not converted yet — convert_model.py --runtime openvino --model {model_key}"
        else:
            reason = "ready"
        entries.append(
            {
                "name": name,
                "label": label,
                "available": reason == "ready",
                "reason": reason,
                "needs_conversion": True,
            }
        )

    converted = converted_dir("ctranslate2", model_key).exists()
    if not _installed("faster_whisper"):
        reason = "faster-whisper not installed"
    elif not converted:
        reason = f"model not converted yet — convert_model.py --runtime ctranslate2 --model {model_key}"
    else:
        reason = "ready"
    entries.append(
        {
            "name": "ctranslate2",
            "label": "CTranslate2 int8 · CPU",
            "available": reason == "ready",
            "reason": reason,
            "needs_conversion": True,
        }
    )

    return entries


def load_runtime(name: str, model_key: str) -> Runtime:
    if name not in {"pytorch", "openvino-gpu", "openvino-cpu", "ctranslate2"}:
        raise ValueError(f"Unknown runtime {name!r}")
    if model_key not in MODEL_REPOS:
        raise ValueError(f"Unknown model {model_key!r}")

    if name == "pytorch":
        return PyTorchRuntime(model_key)
    if name == "openvino-gpu":
        return OpenVINORuntime(model_key, "GPU")
    if name == "openvino-cpu":
        return OpenVINORuntime(model_key, "CPU")
    return CTranslate2Runtime(model_key)


def main() -> None:
    print(f"{platform.system()} {platform.machine()}\n")
    for entry in probe():
        mark = "ready" if entry["available"] else "  -  "
        print(f"  [{mark}] {entry['label']:28} {entry['reason']}")


if __name__ == "__main__":
    main()
