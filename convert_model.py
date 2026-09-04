"""Convert a Typhoon Whisper model for an optimised runtime.

PyTorch runs the Hugging Face weights directly. OpenVINO and CTranslate2 need
their own formats, so the weights have to be converted once per machine before
those runtimes can be selected:

    uv run python convert_model.py --runtime ctranslate2 --model turbo
    uv run python convert_model.py --runtime openvino    --model turbo

Converted models land in models/ and are reused after that. Expect the first
run to download several GB.
"""

import argparse
import shutil
import subprocess
import sys

from model_catalog import MODELS_DIR, converted_dir, model_repos, resolve_repo


def convert_openvino(model_key: str) -> None:
    try:
        from optimum.intel import OVModelForSpeechSeq2Seq
    except ImportError:
        sys.exit(
            "optimum-intel isn't installed. Install it first:\n"
            '    uv add "optimum-intel[openvino]"'
        )
    from transformers import AutoProcessor

    repo = resolve_repo(model_key)
    # Both OpenVINO devices load the same IR and converted_dir() maps them both
    # to models/openvino-<key>, so there is one directory, not one per device.
    # (An earlier version wrote a "GPU copy" and a "CPU copy"; because both
    # names resolve to the same path, it deleted the freshly written IR and
    # then failed copying from the directory it had just removed.)
    target = converted_dir("openvino-gpu", model_key)

    print(f"Converting {repo} to OpenVINO IR (this downloads the model and takes a while)...")
    model = OVModelForSpeechSeq2Seq.from_pretrained(repo, export=True)
    processor = AutoProcessor.from_pretrained(repo)

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(target)
    processor.save_pretrained(target)
    print(f"  wrote {target}  (used by both openvino-gpu and openvino-cpu)")


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
    args = parser.parse_args()

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    if args.runtime == "openvino":
        convert_openvino(args.model)
    elif args.runtime == "whispercpp":
        convert_whispercpp(args.model)
    else:
        convert_ctranslate2(args.model)

    print("\nDone. Check it's picked up with:  uv run python runtimes.py")


if __name__ == "__main__":
    main()
