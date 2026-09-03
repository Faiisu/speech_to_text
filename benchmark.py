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

from runtimes import load_runtime
from transcribe import (
    CHUNK_SECONDS,
    MODEL_REPOS,
    OVERLAP_SECONDS,
    SAMPLE_RATE,
    is_silent,
    load_audio,
)


def chunk_offsets(total_samples: int) -> list[int]:
    chunk_samples = CHUNK_SECONDS * SAMPLE_RATE
    step_samples = chunk_samples - OVERLAP_SECONDS * SAMPLE_RATE
    offsets = []
    start = 0
    while start + chunk_samples <= total_samples:
        offsets.append(start)
        start += step_samples
    return offsets


def run_pass(runtime, audio: np.ndarray, silence_threshold: float, verbose: bool) -> dict:
    offsets = chunk_offsets(audio.size)
    chunk_samples = CHUNK_SECONDS * SAMPLE_RATE

    latencies: list[float] = []
    skipped = 0

    for offset in offsets:
        chunk = audio[offset : offset + chunk_samples]
        if is_silent(chunk, silence_threshold):
            skipped += 1
            if verbose:
                print(f"  [{offset / SAMPLE_RATE:6.1f}s] (silence, skipped)")
            continue

        started = time.perf_counter()
        text = runtime.transcribe(chunk)
        latency = time.perf_counter() - started
        latencies.append(latency)
        if verbose:
            print(f"  [{offset / SAMPLE_RATE:6.1f}s] {latency:5.2f}s  RTF {latency / CHUNK_SECONDS:4.2f}  {text[:60]}")

    if not latencies:
        return {"chunks": 0, "skipped": skipped}

    rtfs = [latency / CHUNK_SECONDS for latency in latencies]
    return {
        "chunks": len(latencies),
        "skipped": skipped,
        "mean_rtf": statistics.mean(rtfs),
        "median_rtf": statistics.median(rtfs),
        "worst_rtf": max(rtfs),
        "mean_latency": statistics.mean(latencies),
        "total": sum(latencies),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=MODEL_REPOS.keys(), required=True)
    parser.add_argument("--file", required=True, help="16kHz mono WAV to replay")
    parser.add_argument(
        "--threads",
        help="Comma-separated torch thread counts to compare, e.g. 4,8,14. "
        "Defaults to whatever torch picks on its own.",
    )
    parser.add_argument("--silence-threshold", type=float, default=0.0,
                        help="Set above 0 to skip quiet chunks the way a live session does "
                             "(default 0 = transcribe every chunk, for comparable timings)")
    parser.add_argument(
        "--runtime",
        default="pytorch",
        choices=["pytorch", "openvino-gpu", "openvino-cpu", "ctranslate2", "whispercpp"],
        help="How to execute the model (default: pytorch)",
    )
    parser.add_argument("--verbose", action="store_true", help="Print every chunk")
    args = parser.parse_args()

    audio = load_audio(args.file)
    duration = audio.size / SAMPLE_RATE
    offsets = chunk_offsets(audio.size)

    print(f"machine  : {platform.system()} {platform.machine()}")
    print(f"torch    : {torch.__version__}")
    print(f"model    : {MODEL_REPOS[args.model]}")
    print(f"runtime  : {args.runtime}")
    print(f"clip     : {args.file}  ({duration:.1f}s audio, {len(offsets)} chunks of {CHUNK_SECONDS}s)")

    if not offsets:
        raise SystemExit(f"Clip is shorter than one {CHUNK_SECONDS}s chunk — use a longer recording.")

    print("\nloading model...")
    runtime = load_runtime(args.runtime, args.model)
    print(f"  {runtime.description}")

    # First inference pays for lazy init and cache warm-up; exclude it so the
    # reported numbers reflect steady-state throughput.
    print("warming up...")
    runtime.transcribe(audio[: CHUNK_SECONDS * SAMPLE_RATE])

    thread_counts = (
        [int(t.strip()) for t in args.threads.split(",") if t.strip()]
        if args.threads
        else [torch.get_num_threads()]
    )

    print(f"\n{'threads':>8}  {'chunks':>6}  {'mean RTF':>9}  {'median':>7}  {'worst':>7}  {'mean lat':>9}  keeps up?")
    print("-" * 72)

    results = []
    for threads in thread_counts:
        torch.set_num_threads(threads)
        stats = run_pass(runtime, audio, args.silence_threshold, args.verbose)
        if not stats["chunks"]:
            print(f"{threads:>8}  every chunk was skipped as silence")
            continue
        keeps_up = "yes" if stats["mean_rtf"] < 1 else "NO"
        print(
            f"{threads:>8}  {stats['chunks']:>6}  {stats['mean_rtf']:>9.2f}  "
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
        print("Set it for a run with:  OMP_NUM_THREADS=<n> uv run python ...")


if __name__ == "__main__":
    main()
