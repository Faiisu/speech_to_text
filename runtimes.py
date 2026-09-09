"""Interchangeable ways of running the same Typhoon Whisper model.

The model is whichever model_catalog offers; what changes here is the machinery
that executes it, because the right choice depends entirely on the hardware:

  pytorch          PyTorch — Apple MPS if present, otherwise CPU float32.
                   Works everywhere. Slowest option on an Intel box.
  openvino-gpu     OpenVINO on an Intel integrated GPU.
  openvino-cpu     OpenVINO on CPU.
  openvino-npu     OpenVINO on an Intel NPU (Core Ultra "AI Boost").
  ctranslate2      CTranslate2 int8 on CPU. Wants AVX-VNNI to be worth it.
  whispercpp       whisper.cpp via pywhispercpp, on GGML weights.

Everything except pytorch needs the weights converted first — see
convert_model.py. Everything reports *why* it is unavailable rather than
failing at the point of use.

Every runtime takes an optional `threads`. Each library has its own knob and
none of them read the others': torch.set_num_threads() does nothing to
CTranslate2, whisper.cpp or OpenVINO, and all three have to be told at
construction, not per call. Passing None leaves each library's own default
alone.
"""

from __future__ import annotations

import importlib.util
import platform
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

# converted_dir lives in model_catalog because the catalogue has to read those
# same directories to discover models. It also validates the key, which matters
# now that keys come from disk rather than a fixed literal.
from model_catalog import converted_dir, discover
from transcribe import DEFAULT_LANGUAGE, SAMPLE_RATE

# Off by default. These stop Whisper looping one phrase on unclear speech
# (ADR 0004) but they are blunt, and being on by default made every runtime
# except whispercpp — which cannot receive them — decode on different terms
# from it. Plain greedy is the neutral baseline; raise them per session when
# the audio actually loops. Applied wherever the runtime exposes the knobs.
NO_REPEAT_NGRAM_SIZE = 0
REPETITION_PENALTY = 1.0


@dataclass(frozen=True)
class Decoding:
    """The two anti-looping knobs, off unless a session asks for them.

    They were added against a real failure — room noise past the silence gate
    made Whisper regenerate one phrase dozens of times (ADR 0004) — but they
    are blunt: a repetition penalty punishes every token the model has already
    emitted, and Thai speech legitimately repeats (ครับ, สวัสดีครับ). On clean
    speech they can push the decoder off a correct word it has already used.

    whisper.cpp gets neither, because pywhispercpp exposes neither. Defaulting
    them on therefore meant the runtimes were never compared on equal terms;
    defaulting them off makes the comparison honest and leaves the guards as
    something to reach for on audio that actually loops.
    """

    no_repeat_ngram_size: int = NO_REPEAT_NGRAM_SIZE
    repetition_penalty: float = REPETITION_PENALTY

    def __post_init__(self) -> None:
        if self.no_repeat_ngram_size < 0:
            raise ValueError("no_repeat_ngram_size must be 0 (off) or more")
        if self.repetition_penalty < 1.0:
            raise ValueError("repetition_penalty must be at least 1.0 (1.0 = off)")

    @classmethod
    def off(cls) -> "Decoding":
        return cls(no_repeat_ngram_size=0, repetition_penalty=1.0)

    @property
    def active(self) -> bool:
        return self.no_repeat_ngram_size > 0 or self.repetition_penalty > 1.0

    def label(self) -> str:
        if not self.active:
            return "plain greedy (no repetition guards)"
        return (
            f"no_repeat_ngram={self.no_repeat_ngram_size}, "
            f"repetition_penalty={self.repetition_penalty:g}"
        )


DEFAULT_DECODING = Decoding()


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


class Runtime:
    """Wraps one loaded model so callers only need .transcribe(audio, language).

    `language` is a code from transcribe.LANGUAGES, or "auto" to let the model
    detect it. Every runtime must honour it identically: they used to disagree
    (two pinned Thai, three auto-detected), which quietly made their output —
    and therefore any benchmark comparing them — incomparable.
    """

    # whisper.cpp is the exception: pywhispercpp exposes no generation knobs,
    # so it always decodes with whisper.cpp's own defaults. Say so rather than
    # letting a comparison quietly mean two different things.
    honours_decoding = True

    def __init__(self, name: str, description: str, decoding=None):
        self.name = name
        self.description = description
        # The default for calls that don't name one. Decoding is not baked into
        # the loaded model — it is a generate() kwarg — so a caller that varies
        # it per call (the server, whose panel exposes both knobs) must not be
        # forced to load a second copy of the weights to change it.
        self.decoding = decoding or DEFAULT_DECODING

    def transcribe(
        self, audio: np.ndarray, language: str = DEFAULT_LANGUAGE, decoding=None
    ) -> str:
        raise NotImplementedError


class PyTorchRuntime(Runtime):
    def __init__(self, model_key: str, threads: int | None = None, decoding=None):
        import torch

        from transcribe import load_pipeline, pick_device

        if threads:
            torch.set_num_threads(threads)
        device = pick_device()
        self._pipeline = load_pipeline(model_key, device)
        super().__init__("pytorch", f"PyTorch on {device}", decoding)

    def transcribe(
        self, audio: np.ndarray, language: str = DEFAULT_LANGUAGE, decoding=None
    ) -> str:
        decoding = decoding or self.decoding
        generate_kwargs = {
            "no_repeat_ngram_size": decoding.no_repeat_ngram_size,
            "repetition_penalty": decoding.repetition_penalty,
        }
        if language != "auto":
            # "transcribe" rather than "translate": without it a pinned
            # non-English language can still be turned into English.
            generate_kwargs["language"] = language
            generate_kwargs["task"] = "transcribe"
        result = self._pipeline(
            {"array": audio, "sampling_rate": SAMPLE_RATE},
            generate_kwargs=generate_kwargs,
        )
        return result["text"].strip()


class OpenVINORuntime(Runtime):
    def __init__(self, model_key: str, device: str, threads: int | None = None, decoding=None):
        from optimum.intel import OVModelForSpeechSeq2Seq
        from transformers import AutoProcessor

        path = converted_dir(f"openvino-{device.lower()}", model_key)
        if not path.exists():
            raise FileNotFoundError(
                f"No converted model at {path}. Run:\n"
                f"    uv run python convert_model.py --runtime openvino --model {model_key}"
            )

        # INFERENCE_NUM_THREADS is a CPU-plugin property; the GPU and NPU
        # plugins reject config keys they don't own.
        ov_config = {"INFERENCE_NUM_THREADS": str(threads)} if threads and device == "CPU" else {}
        self._model = OVModelForSpeechSeq2Seq.from_pretrained(
            path, device=device, ov_config=ov_config
        )
        self._processor = AutoProcessor.from_pretrained(path)
        super().__init__(f"openvino-{device.lower()}", f"OpenVINO on {device}", decoding)

    def transcribe(
        self, audio: np.ndarray, language: str = DEFAULT_LANGUAGE, decoding=None
    ) -> str:
        decoding = decoding or self.decoding
        features = self._processor(
            audio, sampling_rate=SAMPLE_RATE, return_tensors="pt"
        ).input_features
        extra = {}
        if language != "auto":
            extra["language"] = language
            extra["task"] = "transcribe"
        tokens = self._model.generate(
            features,
            no_repeat_ngram_size=decoding.no_repeat_ngram_size,
            repetition_penalty=decoding.repetition_penalty,
            **extra,
        )
        return self._processor.batch_decode(tokens, skip_special_tokens=True)[0].strip()


class WhisperCppRuntime(Runtime):
    honours_decoding = False

    def __init__(self, model_key: str, threads: int | None = None, decoding=None):
        from pywhispercpp.model import Model

        path = converted_dir("whispercpp", model_key)
        weights = sorted(path.glob("*.bin")) if path.exists() else []
        if not weights:
            raise FileNotFoundError(
                f"No GGML weights in {path}. Run:\n"
                f"    uv run python convert_model.py --runtime whispercpp --model {model_key}"
            )

        # whisper.cpp defaults to min(4, hardware_concurrency) threads and
        # reads no environment variable, so on a 14-core box it quietly uses
        # four unless told otherwise.
        extra = {"n_threads": threads} if threads else {}
        self._model = Model(
            str(weights[0]),
            print_progress=False,
            print_realtime=False,
            **extra,
        )
        super().__init__("whispercpp", f"whisper.cpp (GGML) — {weights[0].name}", decoding)

    def transcribe(
        self, audio: np.ndarray, language: str = DEFAULT_LANGUAGE, decoding=None
    ) -> str:
        # `decoding` is accepted and ignored: pywhispercpp exposes no
        # generation knobs, which is what honours_decoding = False announces.
        # whisper.cpp spells auto-detection as the literal language "auto"
        segments = self._model.transcribe(audio, no_context=True, language=language)
        return "".join(segment.text for segment in segments).strip()


class CTranslate2Runtime(Runtime):
    def __init__(self, model_key: str, threads: int | None = None, decoding=None):
        from faster_whisper import WhisperModel

        path = converted_dir("ctranslate2", model_key)
        if not path.exists():
            raise FileNotFoundError(
                f"No converted model at {path}. Run:\n"
                f"    uv run python convert_model.py --runtime ctranslate2 --model {model_key}"
            )

        # cpu_threads=0 is faster-whisper's "decide for me"
        self._model = WhisperModel(
            str(path), device="cpu", compute_type="int8", cpu_threads=threads or 0
        )
        super().__init__("ctranslate2", "CTranslate2 int8 on CPU", decoding)

    def transcribe(
        self, audio: np.ndarray, language: str = DEFAULT_LANGUAGE, decoding=None
    ) -> str:
        decoding = decoding or self.decoding
        segments, _ = self._model.transcribe(
            audio,
            # faster-whisper detects the language when this is None
            language=None if language == "auto" else language,
            # faster-whisper defaults to beam_size=5; the PyTorch path decodes
            # greedily. Left alone that's ~5x the work AND makes any
            # cross-runtime timing comparison meaningless, so match greedy.
            beam_size=1,
            # faster-whisper otherwise re-decodes at escalating temperatures
            # whenever output trips its quality thresholds — up to 6 passes for
            # one chunk. The PyTorch path has no such fallback, so leaving it on
            # both inflates latency and makes runtimes incomparable.
            temperature=0.0,
            # each chunk is transcribed independently here, so carrying text
            # across calls would only help a hallucination propagate
            condition_on_previous_text=False,
            no_repeat_ngram_size=decoding.no_repeat_ngram_size,
            repetition_penalty=decoding.repetition_penalty,
        )
        return "".join(segment.text for segment in segments).strip()


@lru_cache(maxsize=1)
def _openvino_devices() -> tuple[str, ...]:
    """Devices OpenVINO can actually compile for, e.g. ("CPU", "GPU").

    A render node under /dev/dri isn't enough: the GPU plugin also needs an
    OpenCL runtime, and without it compile_model() dies with a bare
    "libOpenCL.so.1: cannot open shared object file" at load time. OpenVINO
    only lists a device once its plugin loads, so asking it is the same test
    the runtime will apply later. Cached because probe() runs per request.
    """
    try:
        import openvino

        return tuple(openvino.Core().available_devices)
    except Exception:
        return ()


def _render_nodes() -> list[Path]:
    return sorted(Path("/dev/dri").glob("renderD*")) if Path("/dev/dri").exists() else []


def _intel_gpu_present() -> bool:
    return bool(_render_nodes())


def _npu_present() -> bool:
    """The Meteor Lake NPU's kernel driver, which is separate from the
    userspace pieces OpenVINO's NPU plugin needs."""
    return Path("/dev/accel/accel0").exists()


def _render_node_readable() -> bool:
    """Can this *user* open the render node, not just: does it exist.

    It is root:render 0660 with an ACL for whoever is logged in at the
    console, so a service account that runs the app over SSH sees the node
    listed and still gets no GPU: the OpenCL driver silently fails to open
    it and OpenVINO lists CPU only.
    """
    import os

    return any(os.access(node, os.R_OK | os.W_OK) for node in _render_nodes())


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
    for device, label in (
        ("GPU", "OpenVINO · Intel GPU"),
        ("CPU", "OpenVINO · CPU"),
        ("NPU", "OpenVINO · Intel NPU"),
    ):
        name = f"openvino-{device.lower()}"
        converted = converted_dir(name, model_key).exists()
        if not has_openvino:
            reason = "openvino + optimum-intel not installed"
        elif not any(d.split(".")[0] == device for d in _openvino_devices()):
            if device == "GPU" and _intel_gpu_present():
                # The hardware is there; either the userspace driver is
                # missing or this account can't open the render node.
                if _render_node_readable():
                    reason = (
                        "Intel GPU present but OpenVINO can't use it — install the "
                        "OpenCL runtime (Linux: sudo apt install intel-opencl-icd)"
                    )
                else:
                    reason = (
                        "Intel GPU present but this user can't open /dev/dri/renderD* — "
                        "sudo usermod -aG render $USER, then log in again"
                    )
            elif device == "NPU" and _npu_present():
                reason = (
                    "Intel NPU present but OpenVINO can't use it — install the NPU "
                    "driver (Linux: intel-driver-compiler-npu, intel-level-zero-npu)"
                )
            else:
                reason = f"OpenVINO reports no {device} device on this machine"
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

    ggml = converted_dir("whispercpp", model_key)
    if not _installed("pywhispercpp"):
        reason = "pywhispercpp not installed"
    elif not (ggml.exists() and any(ggml.glob("*.bin"))):
        reason = f"weights not fetched yet — convert_model.py --runtime whispercpp --model {model_key}"
    else:
        reason = "ready"
    entries.append(
        {
            "name": "whispercpp",
            "label": "whisper.cpp (GGML) · CPU",
            "available": reason == "ready",
            "reason": reason,
            "needs_conversion": True,
        }
    )

    return entries


RUNTIME_NAMES = [
    "pytorch",
    "openvino-gpu",
    "openvino-cpu",
    "openvino-npu",
    "ctranslate2",
    "whispercpp",
]


def load_runtime(
    name: str,
    model_key: str,
    threads: int | None = None,
    decoding: Decoding | None = None,
) -> Runtime:
    """Load one runtime.

    `threads` is the library-specific thread count, or None to leave that
    library's own default alone. `decoding` is the repetition guard set, or
    None for the ADR 0004 defaults; Decoding.off() decodes plain greedy.
    """
    if name not in RUNTIME_NAMES:
        raise ValueError(f"Unknown runtime {name!r}")
    if model_key not in discover():
        raise ValueError(f"Unknown model {model_key!r}")

    if name == "pytorch":
        return PyTorchRuntime(model_key, threads, decoding)
    if name.startswith("openvino-"):
        return OpenVINORuntime(model_key, name.split("-")[1].upper(), threads, decoding)
    if name == "whispercpp":
        return WhisperCppRuntime(model_key, threads, decoding)
    return CTranslate2Runtime(model_key, threads, decoding)


def main() -> None:
    print(f"{platform.system()} {platform.machine()}\n")
    for entry in probe():
        mark = "ready" if entry["available"] else "  -  "
        print(f"  [{mark}] {entry['label']:28} {entry['reason']}")


if __name__ == "__main__":
    main()
