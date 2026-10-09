"""Convert a Typhoon Whisper model for an optimised runtime.

PyTorch runs the Hugging Face weights directly. OpenVINO and CTranslate2 need
their own formats, so the weights have to be converted once per machine before
those runtimes can be selected:

    uv run python convert_model.py --runtime ctranslate2 --model turbo
    uv run python convert_model.py --runtime openvino    --model turbo

The OpenVINO conversion also takes --precision int8 / int4, which compresses
the weights on the way out. Those land beside the uncompressed build under
their own key (turbo-int8, turbo-int4), so all three can be benchmarked
against each other rather than one replacing the other.

Converted models land in models/ and are reused after that. Expect the first
run to download several GB.
"""

import argparse
import shutil
import subprocess
import sys

from model_catalog import MODELS_DIR, converted_dir, model_repos, resolve_repo


# Weight-only compression: the weights are stored narrower and expanded back
# on the fly, so it always shrinks the model and only sometimes speeds it up —
# it pays where inference is memory-bound (the decoder), not where it is
# compute-bound (the encoder). Hence a flag to measure with rather than a
# default. int4 is asymmetric with a group size, which is the usual accuracy/
# size compromise; ratio<1 would leave some layers at 8-bit.
# "source" keeps whatever dtype the checkpoint holds — bf16 for the Typhoon
# fine-tunes, not fp32, which is why this is not called that.
PRECISIONS = ("source", "int8", "int4")


def _weight_config(precision: str):
    from optimum.intel import OVWeightQuantizationConfig

    if precision == "int8":
        return OVWeightQuantizationConfig(bits=8, sym=True)
    return OVWeightQuantizationConfig(bits=4, sym=False, group_size=128, ratio=1.0)


def convert_openvino(model_key: str, precision: str = "source") -> None:
    try:
        from optimum.intel import OVModelForSpeechSeq2Seq
    except ImportError:
        sys.exit(
            "optimum-intel isn't installed. Install it first:\n"
            "    uv sync --extra openvino"
        )
    # optimum-intel imports fine without a usable OpenVINO backend: it hands
    # back a dummy class that only raises once from_pretrained() is called,
    # after the "this takes a while" banner has already printed. Ask openvino
    # directly instead, so a broken install says so before any download.
    try:
        import openvino  # noqa: F401
    except ImportError:
        sys.exit(
            "openvino isn't installed (optimum-intel is, but its OpenVINO backend "
            "is missing). Install the extra:\n    uv sync --extra openvino"
        )
    if OVModelForSpeechSeq2Seq.__module__.startswith("optimum.intel.utils.dummy"):
        sys.exit(
            "optimum-intel can't see the installed openvino. This is usually a\n"
            "version mismatch — optimum-intel < 1.21 probes for openvino.runtime,\n"
            "which openvino 2026 removed. Upgrade it:\n"
            "    uv sync --extra openvino --upgrade-package optimum-intel"
        )
    from transformers import AutoProcessor

    repo = resolve_repo(model_key)
    # Both OpenVINO devices load the same IR and converted_dir() maps them both
    # to models/openvino-<key>, so there is one directory, not one per device.
    # (An earlier version wrote a "GPU copy" and a "CPU copy"; because both
    # names resolve to the same path, it deleted the freshly written IR and
    # then failed copying from the directory it had just removed.)
    #
    # A compressed build gets its own key — models/openvino-turbo-int8 — which
    # the catalogue discovers as the model "turbo-int8". That makes it a thing
    # you can select and benchmark next to the uncompressed build, instead of an
    # invisible replacement for it.
    target_key = model_key if precision == "source" else f"{model_key}-{precision}"
    target = converted_dir("openvino-gpu", target_key)

    extra = {} if precision == "source" else {"quantization_config": _weight_config(precision)}
    weights = "as published" if precision == "source" else precision
    print(f"Converting {repo} to OpenVINO IR, weights {weights} "
          "(this downloads the model and takes a while)...")
    model = OVModelForSpeechSeq2Seq.from_pretrained(repo, export=True, **extra)
    processor = AutoProcessor.from_pretrained(repo)

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(target)
    processor.save_pretrained(target)
    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    print(f"  wrote {target}  ({size / 1e6:.0f} MB, used by every openvino-* runtime)")
    if precision != "source":
        print(f"  benchmark it as:  --model {target_key} --runtime openvino-gpu")


def convert_ctranslate2(model_key: str) -> None:
    if shutil.which("ct2-transformers-converter") is None:
        sys.exit(
            "ct2-transformers-converter not found. Install faster-whisper first:\n"
            "    uv add faster-whisper"
        )

    repo = resolve_repo(model_key)
    target = converted_dir("ctranslate2", model_key)
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    print(f"Converting {repo} to CTranslate2 int8...")
    result = subprocess.run(
        [
            "ct2-transformers-converter",
            "--model", repo,
            "--output_dir", str(target),
            "--quantization", "int8",
            "--copy_files", "tokenizer.json", "preprocessor_config.json",
        ],
        check=False,
    )
    if result.returncode != 0:
        sys.exit(
            f"Conversion failed (exit {result.returncode}). If it complained about missing "
            "files, try again without --copy_files, or check the model card for which "
            "tokenizer files this fine-tune ships."
        )
    print(f"  wrote {target}")


# Community GGML conversions for whisper.cpp. Only turbo has one published;
# large-v3 would have to be converted with whisper.cpp's own script.
GGML_REPOS = {"turbo": "korakotlee/typhoon-whisper-turbo-ggml"}


def convert_whispercpp(model_key: str) -> None:
    repo = GGML_REPOS.get(model_key)
    if repo is None:
        sys.exit(
            f"No published GGML build for {model_key!r}. Only "
            f"{', '.join(GGML_REPOS)} has one. To make your own, use whisper.cpp's\n"
            "    models/convert-h5-to-ggml.py\n"
            f"against {resolve_repo(model_key)}, then drop the .bin into "
            f"{converted_dir('whispercpp', model_key)}."
        )

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        sys.exit("huggingface_hub isn't available — it ships with transformers, so check the venv.")

    target = converted_dir("whispercpp", model_key)
    target.mkdir(parents=True, exist_ok=True)

    print(f"Fetching {repo} (community GGML conversion, not published by typhoon-ai)...")
    snapshot_download(repo_id=repo, local_dir=str(target), allow_patterns=["*.bin"])

    weights = sorted(target.glob("*.bin"))
    if not weights:
        sys.exit(f"No .bin weights found in {repo} — check what that repo actually contains.")
    for path in weights:
        print(f"  {path.name}  ({path.stat().st_size / 1e6:.0f} MB)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--runtime", choices=["openvino", "ctranslate2", "whispercpp"], required=True
    )
    parser.add_argument("--model", choices=model_repos().keys(), required=True)
    parser.add_argument(
        "--precision",
        choices=PRECISIONS,
        default="source",
        help="OpenVINO only: compress the weights on the way out. int8/int4 are "
        "written as a separate model (turbo-int8, turbo-int4) so they can be "
        "compared against the uncompressed build (default: source, i.e. the "
        "checkpoint's own dtype).",
    )
    args = parser.parse_args()

    if args.precision != "source" and args.runtime != "openvino":
        parser.error(f"--precision is an OpenVINO option; {args.runtime} has a fixed format")

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    if args.runtime == "openvino":
        convert_openvino(args.model, args.precision)
    elif args.runtime == "whispercpp":
        convert_whispercpp(args.model)
    else:
        convert_ctranslate2(args.model)

    print("\nDone. Check it's picked up with:  uv run python runtimes.py")


if __name__ == "__main__":
    main()
