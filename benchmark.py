"""Replay an audio file through the live chunking pipeline and measure it.

The live path can't be benchmarked with a microphone: every run says something
different, so two runs are never comparable. This feeds a fixed file through
exactly the same 5s/1s-overlap chunking the live session uses, so numbers can
be compared across models, machines, and optimisation attempts.

    uv run python benchmark.py --model turbo --file clip.wav
    uv run python benchmark.py --model turbo --file clip.wav --threads 4,8,14

Real-time factor is the headline: below 1.0 the model transcribes a chunk
faster than the chunk's audio takes to speak, so it can keep up with a live
microphone. At or above 1.0 it falls progressively behind.
"""

import argparse
import platform
import statistics
import time

import numpy as np
import torch

from model_catalog import discover, resolve_repo
from runtimes import RUNTIME_NAMES, load_runtime
from transcribe import (
    CHUNK_SECONDS,
    DEFAULT_LANGUAGE,
    DEFAULT_SILENCE_RMS,
    LANGUAGES,
    OVERLAP_SECONDS,
    SAMPLE_RATE,
    is_silent,
    load_audio,
)


from pathlib import Path

AUDIO_DIR = Path(__file__).parent / "audio"


def record_audio_clip(
    output_path: Path | str,
    duration: float | None = None,
    device: int | str | None = None,
) -> Path:
    """Record 16kHz mono audio from a microphone and save to WAV."""
    import sounddevice as sd
    import soundfile as sf

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    frames: list[np.ndarray] = []

    def callback(indata, frame_count, time_info, status):
        frames.append(indata.copy())

    dev_index = None
    if device is not None:
        try:
            dev_index = int(device)
        except ValueError:
            for idx, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0 and str(device).lower() in d["name"].lower():
                    dev_index = idx
                    break

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        device=dev_index,
        callback=callback,
    )

    with stream:
        if duration is not None and duration > 0:
            print(f"Recording to {target.name} for {duration:.1f}s (speak now)...")
            time.sleep(duration)
        else:
            print(f"Recording to {target.name} (speak now). Press Enter to stop...")
            input()

    if not frames:
        raise RuntimeError("No audio captured.")

    audio = np.concatenate(frames, axis=0).flatten()
    sf.write(str(target), audio, SAMPLE_RATE)
    print(f"Saved {target} ({len(audio) / SAMPLE_RATE:.1f}s, 16kHz mono)")
    return target


def chunk_offsets(total_samples: int) -> list[int]:
    chunk_samples = CHUNK_SECONDS * SAMPLE_RATE
    step_samples = chunk_samples - OVERLAP_SECONDS * SAMPLE_RATE
    offsets = []
    start = 0
    while start + chunk_samples <= total_samples:
        offsets.append(start)
        start += step_samples
    return offsets


def run_pass(
    runtime,
    audio: np.ndarray,
    silence_threshold: float,
    verbose: bool = False,
    on_chunk: callable = None,
    language: str = DEFAULT_LANGUAGE,
) -> dict:
    offsets = chunk_offsets(audio.size)
    chunk_samples = CHUNK_SECONDS * SAMPLE_RATE

    latencies: list[float] = []
    texts: list[str] = []
    skipped = 0

    for i, offset in enumerate(offsets):
        chunk = audio[offset : offset + chunk_samples]
        if is_silent(chunk, silence_threshold):
            skipped += 1
            if verbose:
                print(f"  [{offset / SAMPLE_RATE:6.1f}s] (silence, skipped)")
            if on_chunk:
                on_chunk(i + 1, len(offsets), offset / SAMPLE_RATE, 0.0, 0.0, "(silence, skipped)", True)
            continue

        started = time.perf_counter()
        text = runtime.transcribe(chunk, language)
        latency = time.perf_counter() - started
        latencies.append(latency)
        texts.append(text)
        rtf = latency / CHUNK_SECONDS
        if verbose:
            print(f"  [{offset / SAMPLE_RATE:6.1f}s] {latency:5.2f}s  RTF {rtf:4.2f}  {text[:60]}")
        if on_chunk:
            on_chunk(i + 1, len(offsets), offset / SAMPLE_RATE, latency, rtf, text, False)

    if not latencies:
        return {"chunks": 0, "skipped": skipped, "sample_text": ""}

    rtfs = [latency / CHUNK_SECONDS for latency in latencies]
    return {
        "chunks": len(latencies),
        "skipped": skipped,
        "mean_rtf": statistics.mean(rtfs),
        "median_rtf": statistics.median(rtfs),
        "worst_rtf": max(rtfs),
        "mean_latency": statistics.mean(latencies),
        "total": sum(latencies),
        "sample_text": " ".join(t for t in texts if t).strip(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    # discover() rather than model_repos(): a model that exists only as
    # converted weights (an int8 OpenVINO build, say) has no repo and would
    # otherwise be unbenchmarkable, which is exactly backwards.
    parser.add_argument("--model", choices=discover().keys(), required=True)
    parser.add_argument("--file", help="16kHz mono WAV to replay (or provide --record)")
    parser.add_argument(
        "--record",
        nargs="?",
        const="benchmark_clip.wav",
        help="Record a new benchmark clip from microphone before running (default: benchmark_clip.wav)",
    )
    parser.add_argument("--duration", type=float, default=None, help="Recording duration in seconds (stops on Enter if omitted)")
    parser.add_argument("--device", help="Microphone device index or name")
    parser.add_argument(
        "--threads",
        help="Comma-separated thread counts to compare, e.g. 4,8,14. Applies to "
        "whichever runtime is selected — each gets its own library's knob — and "
        "reloads the model per count, since none of them can be retuned in place. "
        "Defaults to each library's own choice.",
    )
    parser.add_argument("--silence-threshold", type=float, default=DEFAULT_SILENCE_RMS,
                        help=f"Set above 0 to skip quiet chunks the way a live session does "
                             f"(default: {DEFAULT_SILENCE_RMS})")
    parser.add_argument(
        "--runtime",
        default="pytorch",
        choices=RUNTIME_NAMES,
        help="How to execute the model (default: pytorch)",
    )
    parser.add_argument(
        "--language",
        default=DEFAULT_LANGUAGE,
        choices=LANGUAGES.keys(),
        help=f"Language to transcribe (default: {DEFAULT_LANGUAGE}). Keep it identical "
        "across runs being compared — 'auto' adds a detection pass and can pick "
        "differently per chunk, which makes timings incomparable.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print every chunk")
    args = parser.parse_args()

    if args.record:
        filename = args.record if args.record.endswith(".wav") else f"{args.record}.wav"
        target_path = AUDIO_DIR / filename
        record_audio_clip(target_path, duration=args.duration, device=args.device)
        if not args.file:
            args.file = str(target_path)

    if not args.file:
        parser.error("Either --file or --record must be specified.")

    audio = load_audio(args.file)
    duration = audio.size / SAMPLE_RATE
    offsets = chunk_offsets(audio.size)

    print(f"machine  : {platform.system()} {platform.machine()}")
    print(f"torch    : {torch.__version__}")
    try:
        print(f"model    : {resolve_repo(args.model)}")
    except KeyError:
        print(f"model    : {args.model}  (converted weights only, no source repo)")
    print(f"runtime  : {args.runtime}")
    print(f"clip     : {args.file}  ({duration:.1f}s audio, {len(offsets)} chunks of {CHUNK_SECONDS}s)")

    if not offsets:
        raise SystemExit(f"Clip is shorter than one {CHUNK_SECONDS}s chunk — use a longer recording.")

    thread_counts = (
        [int(t.strip()) for t in args.threads.split(",") if t.strip()]
        if args.threads
        else [None]
    )

    print(f"\n{'threads':>8}  {'chunks':>6}  {'mean RTF':>9}  {'median':>7}  {'worst':>7}  {'mean lat':>9}  keeps up?")
    print("-" * 72)

    results = []
    for threads in thread_counts:
        # Reloaded per count: CTranslate2, whisper.cpp and OpenVINO all fix
        # their thread pool when the model is built, so setting it afterwards
        # (which is all this used to do, via torch.set_num_threads) changed
        # nothing at all for three of the five runtimes.
        runtime = load_runtime(args.runtime, args.model, threads)
        if threads == thread_counts[0]:
            print(f"  {runtime.description}")
            print(f"  language: {LANGUAGES[args.language]}")
        # First inference pays for lazy init and cache warm-up; exclude it so
        # the reported numbers reflect steady-state throughput.
        runtime.transcribe(audio[: CHUNK_SECONDS * SAMPLE_RATE], args.language)
        stats = run_pass(
            runtime, audio, args.silence_threshold, args.verbose, language=args.language
        )
        label = "default" if threads is None else str(threads)
        if not stats["chunks"]:
            print(f"{label:>8}  every chunk was skipped as silence")
            continue
        keeps_up = "yes" if stats["mean_rtf"] < 1 else "NO"
        print(
            f"{label:>8}  {stats['chunks']:>6}  {stats['mean_rtf']:>9.2f}  "
            f"{stats['median_rtf']:>7.2f}  {stats['worst_rtf']:>7.2f}  "
            f"{stats['mean_latency']:>8.2f}s  {keeps_up}"
        )
        results.append((threads, stats))

    if len(results) > 1:
        best = min(results, key=lambda r: r[1]["mean_rtf"])
        worst = max(results, key=lambda r: r[1]["mean_rtf"])
        gain = (worst[1]["mean_rtf"] / best[1]["mean_rtf"] - 1) * 100
        print(
            f"\nbest: {best[0]} threads (mean RTF {best[1]['mean_rtf']:.2f}) — "
            f"{gain:.0f}% faster than {worst[0]} threads"
        )


if __name__ == "__main__":
    main()
