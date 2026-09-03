import argparse
import platform
import threading
import time
import uuid
from datetime import datetime, timezone

import numpy as np
import requests
import sounddevice as sd
import soundfile as sf
import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

DEFAULT_BACKEND_URL = "http://localhost:8000"

MODEL_REPOS = {
    "turbo": "typhoon-ai/typhoon-whisper-turbo",
    "large-v3": "typhoon-ai/typhoon-whisper-large-v3",
}

SAMPLE_RATE = 16_000
CHUNK_SECONDS = 5
OVERLAP_SECONDS = 1
STEP_SECONDS = CHUNK_SECONDS - OVERLAP_SECONDS
# Consecutive chunks are exactly STEP_SECONDS apart, so an overlap-caused
# duplicate detection always lands exactly one chunk later. The debounce
# window has to cover that full step, not just the overlap itself.
DEBOUNCE_SECONDS = STEP_SECONDS + 0.5
# Whisper-family models hallucinate plausible-sounding text from silence
# (there's no "say nothing" output). Skip transcribing a chunk entirely if
# its audio energy is below this RMS threshold, rather than trusting the
# model to recognize its own silence.
DEFAULT_SILENCE_RMS = 0.01
# Bound on how long to wait for the chunk-processing thread to notice a stop
# request and exit. Without this, a stuck or pathologically slow transcribe()
# call (observed in practice with a hung/slow inference call) would hang the
# CLI's "press Enter to stop" and, worse, the server's /stop indefinitely
# with zero feedback. Generous enough to cover one slow chunk (RTF > 1 has
# been observed on this hardware) without waiting forever on a genuine hang.
STOP_JOIN_TIMEOUT_SECONDS = 60


def pick_device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_pipeline(model_key: str, device: str):
    repo_id = MODEL_REPOS[model_key]
    dtype = torch.float16 if device == "mps" else torch.float32

    model = AutoModelForSpeechSeq2Seq.from_pretrained(repo_id, dtype=dtype)
    model.to(device)
    processor = AutoProcessor.from_pretrained(repo_id)

    return pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        chunk_length_s=30,
        device=device,
    )


def load_audio(path: str) -> np.ndarray:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sample_rate != SAMPLE_RATE:
        raise ValueError(
            f"Expected {SAMPLE_RATE}Hz audio, got {sample_rate}Hz. Resample the file first."
        )
    return audio


def transcribe(asr_pipeline, audio: np.ndarray) -> str:
    # Whisper-family models can get stuck regenerating the same phrase in a
    # loop when there's no clear speech to anchor generation (a distinct
    # failure mode from silence hallucination in ADR 0004 — this happens
    # even when is_silent() lets the chunk through). These generation
    # settings are the standard mitigation.
    result = asr_pipeline(
        {"array": audio, "sampling_rate": SAMPLE_RATE},
        generate_kwargs={"no_repeat_ngram_size": 3, "repetition_penalty": 1.3},
    )
    return result["text"].strip()


def is_silent(audio: np.ndarray, threshold: float) -> bool:
    rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
    return rms < threshold


def report_event(backend_url: str, word: str, model_key: str, session_id: str) -> None:
    try:
        requests.post(
            f"{backend_url}/events",
            json={
                "word": word,
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "model": model_key,
                "session_id": session_id,
            },
            timeout=2,
        )
    except requests.RequestException as exc:
        print(f"[backend] warning: failed to report event: {exc}")


def spot_keywords(
    text: str,
    keywords: list[str],
    last_alerted: dict[str, float],
    now: float,
    *,
    model_key: str,
    session_id: str,
    backend_url: str,
    on_event=None,
) -> None:
    lowered = text.lower()
    for keyword in keywords:
        if keyword.lower() not in lowered:
            continue
        last = last_alerted.get(keyword)
        if last is not None and now - last < DEBOUNCE_SECONDS:
            continue
        last_alerted[keyword] = now
        report_event(backend_url, keyword, model_key, session_id)
        if on_event:
            on_event({"type": "keyword", "keyword": keyword, "time": now})


def run_replay_session(
    runtime,
    audio: np.ndarray,
    keywords: list[str],
    model_key: str,
    backend_url: str,
    silence_threshold: float,
    stop_event: threading.Event,
    session_id: str,
    on_event=None,
) -> np.ndarray:
    """Feed a file through the same chunking a live session uses.

    Emits exactly the events run_recording_session emits, so anything watching
    the feed can't tell the difference — but the audio comes from disk, so the
    run is repeatable and needs no microphone. Chunks are processed as fast as
    the model manages rather than paced to real time; the reported latency and
    RTF are still the real per-chunk figures.
    """
    chunk_samples = CHUNK_SECONDS * SAMPLE_RATE
    step_samples = chunk_samples - OVERLAP_SECONDS * SAMPLE_RATE
    last_alerted: dict[str, float] = {}

    def emit(event: dict) -> None:
        if on_event:
            on_event(event)

    start = 0
    while start + chunk_samples <= audio.size:
        if stop_event.is_set():
            break

        chunk = audio[start : start + chunk_samples]
        chunk_time = start / SAMPLE_RATE

        if is_silent(chunk, silence_threshold):
            emit({"type": "chunk", "time": chunk_time, "text": None, "silent": True})
            start += step_samples
            continue

        chunk_start = time.perf_counter()
        text = runtime.transcribe(chunk)
        latency = time.perf_counter() - chunk_start
        emit(
            {
                "type": "chunk",
                "time": chunk_time,
                "text": text,
                "silent": False,
                "latency": latency,
                "rtf": latency / CHUNK_SECONDS,
            }
        )
        if keywords:
            spot_keywords(
                text,
                keywords,
                last_alerted,
                chunk_time,
                model_key=model_key,
                session_id=session_id,
                backend_url=backend_url,
                on_event=emit,
            )
        start += step_samples

    return audio


def run_recording_session(
    runtime,
    keywords: list[str],
    model_key: str,
    backend_url: str,
    mic_device: int | str | None,
    silence_threshold: float,
    stop_event: threading.Event,
    session_id: str,
    on_event=None,
) -> np.ndarray:
    """Records from the mic and transcribes chunks until stop_event is set.

    on_event, if given, is called with a dict for every chunk/silence/keyword
    event as it happens (used by server.py to stream live updates; the CLI
    passes a callback that just prints, to keep its existing output).

    Returns the full recording for a final full-clip reference pass.
    """
    frames: list[np.ndarray] = []
    lock = threading.Lock()

    def callback(indata, frame_count, time_info, status) -> None:
        with lock:
            frames.append(indata.copy())

    chunk_samples = CHUNK_SECONDS * SAMPLE_RATE
    step_samples = chunk_samples - OVERLAP_SECONDS * SAMPLE_RATE
    last_alerted: dict[str, float] = {}

    def emit(event: dict) -> None:
        if on_event:
            on_event(event)

    def chunk_loop() -> None:
        next_start = 0
        while not stop_event.is_set():
            with lock:
                buffer = (
                    np.concatenate(frames, axis=0).flatten()
                    if frames
                    else np.empty(0, dtype="float32")
                )
            if len(buffer) < next_start + chunk_samples:
                time.sleep(0.1)
                continue

            chunk = buffer[next_start : next_start + chunk_samples]
            chunk_time = next_start / SAMPLE_RATE

            if is_silent(chunk, silence_threshold):
                emit({"type": "chunk", "time": chunk_time, "text": None, "silent": True})
                next_start += step_samples
                continue

            chunk_start = time.perf_counter()
            text = runtime.transcribe(chunk)
            latency = time.perf_counter() - chunk_start
            rtf = latency / CHUNK_SECONDS
            emit(
                {
                    "type": "chunk",
                    "time": chunk_time,
                    "text": text,
                    "silent": False,
                    "latency": latency,
                    "rtf": rtf,
                }
            )
            if keywords:
                spot_keywords(
                    text,
                    keywords,
                    last_alerted,
                    chunk_time,
                    model_key=model_key,
                    session_id=session_id,
                    backend_url=backend_url,
                    on_event=emit,
                )
            next_start += step_samples

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        callback=callback,
        device=mic_device,
    )
    with stream:
        worker = threading.Thread(target=chunk_loop, daemon=True)
        worker.start()
        stop_event.wait()
        worker.join(timeout=STOP_JOIN_TIMEOUT_SECONDS)
        if worker.is_alive():
            emit(
                {
                    "type": "warning",
                    "message": (
                        f"chunk processing did not stop within {STOP_JOIN_TIMEOUT_SECONDS}s "
                        "of the stop request (likely a stuck or very slow transcription call). "
                        "Returning the audio captured so far; the background thread may still "
                        "be running and holding the model/microphone."
                    ),
                }
            )

    with lock:
        full_audio = (
            np.concatenate(frames, axis=0).flatten()
            if frames
            else np.empty(0, dtype="float32")
        )

    if full_audio.size == 0:
        raise RuntimeError(
            "No audio was captured. Check that this process has microphone "
            "permission and the selected input device is correct."
        )

    return full_audio


def record_with_streaming(
    runtime,
    keywords: list[str],
    model_key: str,
    backend_url: str,
    mic_device: int | str | None,
    auto_start: bool,
    silence_threshold: float,
) -> np.ndarray:
    """CLI wrapper: prints terminal output, start/stop driven by Enter keypresses."""
    if auto_start:
        print("Recording starts now (--auto-start). Press Enter to stop.")
    else:
        input("Press Enter to start recording...")

    session_id = str(uuid.uuid4())
    print(f"[session] {session_id}")

    def on_event(event: dict) -> None:
        if event["type"] == "chunk":
            if event["silent"]:
                print(f"[chunk @ {event['time']:.1f}s] (silence, skipped)")
            else:
                print(
                    f"[chunk @ {event['time']:.1f}s] {event['text']} "
                    f"(latency {event['latency']:.2f}s, RTF {event['rtf']:.2f})"
                )
        elif event["type"] == "keyword":
            print(f'[keyword detected] "{event["keyword"]}" at {event["time"]:.1f}s')
        elif event["type"] == "warning":
            print(f"[warning] {event['message']}")

    stop_event = threading.Event()

    def wait_for_enter() -> None:
        input("Recording... press Enter to stop.")
        stop_event.set()

    threading.Thread(target=wait_for_enter, daemon=True).start()

    return run_recording_session(
        runtime,
        keywords,
        model_key,
        backend_url,
        mic_device,
        silence_threshold,
        stop_event,
        session_id,
        on_event=on_event,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=MODEL_REPOS.keys(), required=False)
    parser.add_argument(
        "--file", help="Path to a 16kHz mono WAV file. Omit to record live from the microphone."
    )
    parser.add_argument(
        "--keywords",
        help="Comma-separated keywords/phrases to spot during live recording, e.g. 'สวัสดี,ขอบคุณครับ'",
    )
    parser.add_argument(
        "--backend-url",
        default=DEFAULT_BACKEND_URL,
        help=f"Backend URL to report detected keywords to (default: {DEFAULT_BACKEND_URL})",
    )
    parser.add_argument(
        "--mic-device",
        help="Input device index or name substring to record from (see --list-devices). "
        "Omit to use the system default input.",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available audio input/output devices (by index) and exit.",
    )
    parser.add_argument(
        "--auto-start",
        action="store_true",
        help="Start recording immediately once the model is loaded, without waiting for Enter. "
        "Still requires Enter to stop.",
    )
    parser.add_argument(
        "--runtime",
        default="pytorch",
        choices=["pytorch", "openvino-gpu", "openvino-cpu", "ctranslate2", "whispercpp"],
        help="How to execute the model (default: pytorch). Run "
        "'uv run python runtimes.py' to see which are usable on this machine.",
    )
    parser.add_argument(
        "--silence-threshold",
        type=float,
        default=DEFAULT_SILENCE_RMS,
        help="RMS energy below which a chunk is treated as silence and skipped, "
        f"to avoid Whisper hallucinating text from silence (default: {DEFAULT_SILENCE_RMS}). "
        "Lower it if quiet speech is being skipped; raise it if background noise still "
        "triggers hallucinated transcripts.",
    )
    args = parser.parse_args()

    if args.list_devices:
        print(sd.query_devices())
        return

    if args.model is None:
        parser.error("--model is required unless --list-devices is given")

    mic_device: int | str | None = args.mic_device
    if mic_device is not None:
        try:
            mic_device = int(mic_device)
        except ValueError:
            pass  # treat as a device-name substring instead

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] if args.keywords else []

    # imported here rather than at module scope: runtimes.py imports from this
    # module, so a top-level import would be circular
    from runtimes import load_runtime

    print(f"[platform] {platform.system()} {platform.machine()}")
    print(f"[model] loading {MODEL_REPOS[args.model]} via {args.runtime}...")
    runtime = load_runtime(args.runtime, args.model)
    print(f"[runtime] {runtime.description}")

    audio = (
        load_audio(args.file)
        if args.file
        else record_with_streaming(
            runtime,
            keywords,
            args.model,
            args.backend_url,
            mic_device,
            args.auto_start,
            args.silence_threshold,
        )
    )

    if is_silent(audio, args.silence_threshold):
        print("[reference transcript] (silence, skipped)")
    else:
        start = time.perf_counter()
        text = runtime.transcribe(audio)
        elapsed = time.perf_counter() - start
        print(f"[reference transcript] {text}")
        print(f"[latency] {elapsed:.2f}s")


if __name__ == "__main__":
    main()
