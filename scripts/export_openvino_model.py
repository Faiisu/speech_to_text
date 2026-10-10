"""Export the pinned default Whisper checkpoint to OpenVINO IR on CPU."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

MODEL_REPOSITORY = "typhoon-ai/typhoon-whisper-turbo"
MODEL_REVISION = "3c03fa84c26f172944422ceb8a4e88a2dbc08b10"
REQUIRED_FILES = (
    "openvino_encoder_model.xml",
    "openvino_encoder_model.bin",
    "openvino_decoder_model.xml",
    "openvino_decoder_model.bin",
)
EXPORT_METADATA = ".speech_to_text_export.json"


def has_valid_export(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    try:
        metadata = json.loads((directory / EXPORT_METADATA).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if metadata != {"repository": MODEL_REPOSITORY, "revision": MODEL_REVISION}:
        return False
    if not all((directory / name).is_file() for name in REQUIRED_FILES):
        return False
    return all(
        (directory / name).stat().st_size > 0
        for name in REQUIRED_FILES
        if name.endswith(".bin")
    )


def export_model(destination: Path) -> None:
    from huggingface_hub import snapshot_download
    from optimum.intel import OVModelForSpeechSeq2Seq
    from transformers import AutoProcessor

    if destination.is_symlink():
        raise RuntimeError(f"Refusing to replace model destination symlink: {destination}")
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not destination.is_dir():
        raise RuntimeError(f"Model destination exists and is not a directory: {destination}")
    if has_valid_export(destination):
        print(f"Using existing OpenVINO export at {destination}")
        return

    source = snapshot_download(MODEL_REPOSITORY, revision=MODEL_REVISION)
    model = OVModelForSpeechSeq2Seq.from_pretrained(
        source,
        export=True,
        device="CPU",
        load_in_8bit=False,
    )
    processor = AutoProcessor.from_pretrained(source, local_files_only=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    backup = destination.with_name(f".{destination.name}.{os.getpid()}.previous")
    try:
        model.save_pretrained(staging)
        processor.save_pretrained(staging)
        missing = [
            name
            for name in REQUIRED_FILES
            if not (staging / name).is_file()
            or (name.endswith(".bin") and (staging / name).stat().st_size == 0)
        ]
        if missing:
            raise RuntimeError(f"OpenVINO export is missing required files: {missing}")
        (staging / EXPORT_METADATA).write_text(
            json.dumps(
                {"repository": MODEL_REPOSITORY, "revision": MODEL_REVISION},
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        for path in staging.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
            elif path.is_file():
                path.chmod(0o644)
        staging.chmod(0o755)

        if backup.exists():
            raise RuntimeError(f"Refusing to replace existing model backup: {backup}")
        if destination.exists():
            destination.rename(backup)
        try:
            staging.rename(destination)
        except Exception:
            if backup.exists() and not destination.exists():
                backup.rename(destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    export_model(args.destination)


if __name__ == "__main__":
    main()
