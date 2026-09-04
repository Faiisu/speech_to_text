import argparse
import platform
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import requests
import sounddevice as sd
import soundfile as sf
import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

from model_catalog import model_repos, resolve_repo

DEFAULT_BACKEND_URL = "http://localhost:8000"

# The set of runnable models is discovered per machine (builtin + models.local.json
# + the Hugging Face cache + converted weights) rather than listed here — see
# model_catalog.py. Call model_repos() at use time, never snapshot it at import,
# or a model installed while the server is running stays invisible.

# Which language the model is told to transcribe. Whisper supports ~99; these
# are the ones offered in the panel, Thai first because that's what the Typhoon
# fine-tunes are for. Adding another is one entry here — the panel reads this
# list over /languages rather than keeping its own copy.
#
# "auto" leaves detection to the model. That is genuinely risky here: detection
# runs per 5-second chunk, and a chunk of noise or a half-word can be read as
# another language, at which point Whisper *translates* instead of transcribing.
# Pin the language unless you actually need to handle mixed-language audio.
LANGUAGES = {
    "auto": "Auto-detect (per chunk)",
    "th": "Thai",
    "en": "English",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "ms": "Malay",
    "lo": "Lao",
    "my": "Burmese",
    "km": "Khmer",
    "hi": "Hindi",
    "ar": "Arabic",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "pt": "Portuguese",
    "ru": "Russian",
}
DEFAULT_LANGUAGE = "th"

SAMPLE_RATE = 16_000

# Whisper pads every input to a 30s mel window and truncates anything longer,
# so a chunk above this is audio the model never sees — silently, with no
# error. It is also the ceiling worth wanting: the encoder costs the same for
# a 5s chunk as for a 30s one, so a longer chunk spreads that fixed cost over
# more audio, and gives the model more of the context it was trained on.
MAX_CHUNK_SECONDS = 30.0


@dataclass(frozen=True)
class Chunking:
    """How the audio is cut up, which used to be three module constants.

    A live session and a benchmark can now disagree about it — and the same
    benchmark can sweep it — so it travels as a value instead. Everything
    derived from it (step, sample counts, the keyword debounce window) is
    computed here, because those derivations drifting apart is exactly how
    changing "just the chunk size" used to break keyword debouncing.
    """

    chunk_seconds: float = 5.0
    overlap_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.chunk_seconds <= 0:
            raise ValueError("chunk_seconds must be positive")
        if self.chunk_seconds > MAX_CHUNK_SECONDS:
            raise ValueError(
                f"chunk_seconds {self.chunk_seconds} exceeds Whisper's {MAX_CHUNK_SECONDS}s "
                "window — the audio past 30s would be dropped without an error"
            )
        if not 0 <= self.overlap_seconds < self.chunk_seconds:
            raise ValueError(
                f"overlap_seconds must be at least 0 and less than chunk_seconds "
                f"({self.chunk_seconds}); got {self.overlap_seconds}"
            )

    @property
    def step_seconds(self) -> float:
        return self.chunk_seconds - self.overlap_seconds

    @property
    def chunk_samples(self) -> int:
        return int(self.chunk_seconds * SAMPLE_RATE)

    @property
    def step_samples(self) -> int:
        return int(self.step_seconds * SAMPLE_RATE)

    @property
    def debounce_seconds(self) -> float:
        # Consecutive chunks are exactly one step apart, so an overlap-caused
        # duplicate detection always lands exactly one chunk later. The
        # debounce window has to cover that full step, not just the overlap.
        return self.step_seconds + 0.5

    def label(self) -> str:
        return f"{self.chunk_seconds:g}s chunks, {self.overlap_seconds:g}s overlap"


DEFAULT_CHUNKING = Chunking()
# Kept as module constants because they are the defaults everything still
# imports; the values now come from one place instead of three.
CHUNK_SECONDS = DEFAULT_CHUNKING.chunk_seconds
OVERLAP_SECONDS = DEFAULT_CHUNKING.overlap_seconds
STEP_SECONDS = DEFAULT_CHUNKING.step_seconds
DEBOUNCE_SECONDS = DEFAULT_CHUNKING.debounce_seconds
# Whisper-family models hallucinate plausible-sounding text from silence
# (there's no "say nothing" output). Skip transcribing a chunk entirely if
# its audio energy is below this RMS threshold, rather than trusting the
# model to recognize its own silence.
DEFAULT_SILENCE_RMS = 0.02
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
    repo_id = resolve_repo(model_key)
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
    debounce_seconds: float = DEBOUNCE_SECONDS,
) -> None:
    lowered = text.lower()
    for keyword in keywords:
        if keyword.lower() not in lowered:
            continue
        last = last_alerted.get(keyword)
        if last is not None and now - last < debounce_seconds:
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
    language: str = DEFAULT_LANGUAGE,
    chunking: Chunking = DEFAULT_CHUNKING,
) -> np.ndarray:
    """Feed a file through the same chunking a live session uses.

    Emits exactly the events run_recording_session emits, so anything watching
    the feed can't tell the difference — but the audio comes from disk, so the
    run is repeatable and needs no microphone. Chunks are processed as fast as
    the model manages rather than paced to real time; the reported latency and
    RTF are still the real per-chunk figures.
    """
    chunk_samples = chunking.chunk_samples
    step_samples = chunking.step_samples
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
        text = runtime.transcribe(chunk, language)
        latency = time.perf_counter() - chunk_start
        emit(
            {
                "type": "chunk",
                "time": chunk_time,
                "text": text,
                "silent": False,
                "latency": latency,
                "rtf": latency / chunking.chunk_seconds,
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
                debounce_seconds=chunking.debounce_seconds,
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
    language: str = DEFAULT_LANGUAGE,
    chunking: Chunking = DEFAULT_CHUNKING,
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

    chunk_samples = chunking.chunk_samples
    step_samples = chunking.step_samples
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
            text = runtime.transcribe(chunk, language)
            latency = time.perf_counter() - chunk_start
            rtf = latency / chunking.chunk_seconds
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
                    debounce_seconds=chunking.debounce_seconds,
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
    language: str = DEFAULT_LANGUAGE,
    chunking: Chunking = DEFAULT_CHUNKING,
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
        language=language,
        chunking=chunking,
    )


def main() -> None:
    # local, like the load_runtime import below: runtimes.py imports from this
    # module, so a top-level import would be circular
    from runtimes import RUNTIME_NAMES

    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=model_repos().keys(), required=False)
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
        choices=RUNTIME_NAMES,
        help="How to execute the model (default: pytorch). Run "
        "'uv run python runtimes.py' to see which are usable on this machine.",
    )
    parser.add_argument(
        "--language",
        default=DEFAULT_LANGUAGE,
        choices=LANGUAGES.keys(),
        help=f"Language to transcribe (default: {DEFAULT_LANGUAGE}). 'auto' lets the model "
        "detect it per chunk, which on 5-second chunks can misfire and make Whisper "
        "translate instead of transcribe.",
    )
    parser.add_argument(
        "--chunk",
        type=float,
        default=DEFAULT_CHUNKING.chunk_seconds,
        help="Seconds of audio per transcription (default: "
        f"{DEFAULT_CHUNKING.chunk_seconds:g}, maximum {MAX_CHUNK_SECONDS:g}). Whisper pads every "
        "chunk to 30s regardless, so a longer chunk spreads that fixed encoder cost over more "
        "audio and gives the model more context — at the price of waiting a whole chunk before "
        "any text appears.",
    )
    parser.add_argument(
        "--overlap",
        type=float,
        default=DEFAULT_CHUNKING.overlap_seconds,
        help="Seconds each chunk overlaps the previous one, so a word split across the boundary "
        f"is still heard whole (default: {DEFAULT_CHUNKING.overlap_seconds:g}). The cost is the "
        "duplicated words you sometimes see across lines.",
    )
    parser.add_argument(
        "--repetition-penalty",
        type=float,
        default=None,
        help="Penalty on tokens the model has already emitted; 1.0 turns it off "
        "(default: 1.3, from ADR 0004). It exists to stop Whisper looping one phrase on "
        "noise, but it also punishes the words Thai speech legitimately repeats, so turn "
        "it off if transcripts look worse than whisper.cpp's, which never gets it.",
    )
    parser.add_argument(
        "--no-repeat-ngram",
        type=int,
        default=None,
        help="Forbid repeating any n-gram of this size within a chunk; 0 turns it off "
        "(default: 3). Same trade as --repetition-penalty.",
    )
    parser.add_argument(
        "--plain-greedy",
        action="store_true",
        help="Shorthand for --repetition-penalty 1.0 --no-repeat-ngram 0, i.e. decode the "
        "way whisper.cpp does.",
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

    try:
        chunking = Chunking(args.chunk, args.overlap)
    except ValueError as exc:
        parser.error(str(exc))

    # imported here rather than at module scope: runtimes.py imports from this
    # module, so a top-level import would be circular
    from runtimes import DEFAULT_DECODING, Decoding, load_runtime

    if args.plain_greedy and (args.repetition_penalty is not None or args.no_repeat_ngram is not None):
        parser.error("--plain-greedy already sets both; drop the explicit values or drop it")
    if args.plain_greedy:
        decoding = Decoding.off()
    else:
        try:
            decoding = Decoding(
                no_repeat_ngram_size=(
                    DEFAULT_DECODING.no_repeat_ngram_size
                    if args.no_repeat_ngram is None
                    else args.no_repeat_ngram
                ),
                repetition_penalty=(
                    DEFAULT_DECODING.repetition_penalty
                    if args.repetition_penalty is None
                    else args.repetition_penalty
                ),
            )
        except ValueError as exc:
            parser.error(str(exc))

    print(f"[platform] {platform.system()} {platform.machine()}")
    print(f"[model] loading {resolve_repo(args.model)} via {args.runtime}...")
    runtime = load_runtime(args.runtime, args.model, decoding=decoding)
    print(f"[runtime] {runtime.description}")
    print(
        f"[decoding] {decoding.label()}"
        + ("" if runtime.honours_decoding else "  (ignored: this runtime exposes no knobs)")
    )
    print(f"[language] {LANGUAGES[args.language]}")
    print(f"[chunking] {chunking.label()}")

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
            args.language,
            chunking,
        )
    )

    if is_silent(audio, args.silence_threshold):
        print("[reference transcript] (silence, skipped)")
    else:
        start = time.perf_counter()
        text = runtime.transcribe(audio, args.language)
        elapsed = time.perf_counter() - start
        print(f"[reference transcript] {text}")
        print(f"[latency] {elapsed:.2f}s")


if __name__ == "__main__":
    main()
