"""Opt-in live proofs. Doubles cannot satisfy these tests."""

import hashlib
import os
from pathlib import Path
import time
import unicodedata
import wave

import pytest

from .support import collect_until_completed, texts


pytestmark = pytest.mark.hardware


@pytest.fixture(autouse=True)
def require_hardware_opt_in():
    if os.environ.get("FEATURE01_RUN_HARDWARE") != "1":
        pytest.skip("Set FEATURE01_RUN_HARDWARE=1 to run real hardware/model proofs")


def normalized(text):
    return "".join(
        character for character in unicodedata.normalize("NFC", text).casefold()
        if not character.isspace() and not unicodedata.category(character).startswith("P")
    )


def character_error_rate(reference, hypothesis):
    expected, actual = normalized(reference), normalized(hypothesis)
    assert expected, "Verified reference text must not be empty"
    previous = list(range(len(actual) + 1))
    for row, expected_character in enumerate(expected, 1):
        current = [row]
        for column, actual_character in enumerate(actual, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (expected_character != actual_character),
            ))
        previous = current
    return previous[-1] / len(expected)


def test_real_openvino_clip_meets_reference_quality_and_realtime_gate(api, record_property):
    audio_path = os.environ.get("FEATURE01_REFERENCE_AUDIO")
    text_path = os.environ.get("FEATURE01_REFERENCE_TEXT")
    if not audio_path or not text_path or os.environ.get("FEATURE01_REFERENCE_VERIFIED") != "1":
        pytest.skip("Set reference WAV/text paths and FEATURE01_REFERENCE_VERIFIED=1 after independently checking the reference")
    clip = Path(audio_path)
    reference = Path(text_path).read_text(encoding="utf-8")
    with wave.open(str(clip), "rb") as audio:
        duration = audio.getnframes() / audio.getframerate()
    assert duration > 0
    handle = api.load_model({})
    try:
        assert handle.runtime == "openvino-gpu"
        assert handle.model == "turbo"
        assert handle.precision == "source"
        started = time.perf_counter()
        transcript = api.transcribe_clip(clip, handle, {})
        elapsed = time.perf_counter() - started
        cer = character_error_rate(reference, transcript)
        for key, value in {
            "clip_sha256": hashlib.sha256(clip.read_bytes()).hexdigest(),
            "reference_sha256": hashlib.sha256(Path(text_path).read_bytes()).hexdigest(),
            "model": handle.model, "runtime": handle.runtime, "precision": handle.precision,
            "transcript": transcript, "cer": cer, "rtf": elapsed / duration,
        }.items():
            record_property(key, value)
        assert cer <= 0.20, f"CER {cer:.1%} exceeds the 20% reference-quality gate"
        assert elapsed / duration < 1, "The configured runtime cannot sustain one input at audio pace"
    finally:
        handle.close()


def test_real_microphone_produces_text_and_stops_cleanly(api, record_property):
    device = os.environ.get("FEATURE01_MICROPHONE_DEVICE") or None
    runtime = os.environ.get("FEATURE01_MICROPHONE_RUNTIME", "openvino-gpu")
    precision = "int8" if runtime == "ctranslate2" else "source"
    handle = api.load_model({"runtime": runtime, "precision": precision})
    session = None
    try:
        session = api.start_microphone_flow(device, handle, {"source_id": "hardware-proof"})
        # Speak a Thai phrase during this explicit live-proof window.
        time.sleep(6)
        session.stop(timeout=30)
        events = collect_until_completed(session, timeout=30)
        record_property("device", device or "OS default")
        record_property("runtime", runtime)
        record_property("events", repr(events))
        assert any(text.strip() for text in texts(events)), "No speech transcript was observed; speak during the proof window"
        assert not any(event["type"] == "error" for event in events)
        assert events[-1]["status"] == "stopped"
    finally:
        if session is not None:
            session.stop(timeout=30)
        handle.close()
