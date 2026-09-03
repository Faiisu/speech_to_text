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

from runtimes import MODELS_DIR, converted_dir
from transcribe import MODEL_REPOS


def convert_openvino(model_key: str) -> None:
    try:
        from optimum.intel import OVModelForSpeechSeq2Seq
    except ImportError:
        sys.exit(
            "optimum-intel isn't installed. Install it first:\n"
            '    uv add "optimum-intel[openvino]"'
        )
    from transformers import AutoProcessor

    repo = MODEL_REPOS[model_key]
    # both OpenVINO devices load the same converted IR; keep one copy per device
    # name so probe() can look for exactly what it will load
    targets = [converted_dir("openvino-gpu", model_key), converted_dir("openvino-cpu", model_key)]

    print(f"Converting {repo} to OpenVINO IR (this downloads the model and takes a while)...")
    model = OVModelForSpeechSeq2Seq.from_pretrained(repo, export=True)
    processor = AutoProcessor.from_pretrained(repo)

    primary = targets[0]
    primary.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(primary)
    processor.save_pretrained(primary)
    print(f"  wrote {primary}")

    # the CPU variant is byte-identical; copy so either can be selected
    secondary = targets[1]
    if secondary.exists():
        shutil.rmtree(secondary)
    shutil.copytree(primary, secondary)
    print(f"  wrote {secondary}")


def convert_ctranslate2(model_key: str) -> None:
    if shutil.which("ct2-transformers-converter") is None:
        sys.exit(
            "ct2-transformers-converter not found. Install faster-whisper first:\n"
            "    uv add faster-whisper"
        )

    repo = MODEL_REPOS[model_key]
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", choices=["openvino", "ctranslate2"], required=True)
    parser.add_argument("--model", choices=MODEL_REPOS.keys(), required=True)
    args = parser.parse_args()

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    if args.runtime == "openvino":
        convert_openvino(args.model)
    else:
        convert_ctranslate2(args.model)

    print("\nDone. Check it's picked up with:  uv run python runtimes.py")


if __name__ == "__main__":
    main()
