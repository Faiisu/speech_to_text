"""Export the pinned default Whisper checkpoint to OpenVINO IR on CPU."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

MODEL_REPOSITORY = "typhoon-ai/typhoon-whisper-turbo"
MODEL_REVISION = "3c03fa84c26f172944422ceb8a4e88a2dbc08b10"


def export_model(destination: Path) -> None:
    from huggingface_hub import snapshot_download
    from optimum.intel import OVModelForSpeechSeq2Seq
    from transformers import AutoProcessor

    source = snapshot_download(MODEL_REPOSITORY, revision=MODEL_REVISION)
    model = OVModelForSpeechSeq2Seq.from_pretrained(
        source,
        export=True,
        device="CPU",
        load_in_8bit=False,
    )
    processor = AutoProcessor.from_pretrained(source, local_files_only=True)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(destination)
    processor.save_pretrained(destination)
    required = (
        "openvino_encoder_model.xml",
        "openvino_encoder_model.bin",
        "openvino_decoder_model.xml",
        "openvino_decoder_model.bin",
    )
    missing = [name for name in required if not (destination / name).is_file()]
    if missing:
        raise RuntimeError(f"OpenVINO export is missing required files: {missing}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    export_model(args.destination)


if __name__ == "__main__":
    main()
