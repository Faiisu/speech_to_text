"""Replay an audio file through the live chunking pipeline and measure it.

The live path can't be benchmarked with a microphone: every run says something
different, so two runs are never comparable. This feeds a fixed file through
exactly the same 5s/1s-overlap chunking the live session uses, so numbers can
be compared across models, machines, and optimisation attempts.

    uv run python benchmark.py --model turbo --file clip.wav
    uv run python benchmark.py --model turbo --file clip.wav --threads 4,8,14
    uv run python benchmark.py --model turbo,turbo-int8 --file clip.wav --runtime openvino-gpu

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
from runtimes import DEFAULT_DECODING, RUNTIME_NAMES, Decoding, load_runtime
from transcribe import (
    CHUNK_SECONDS,
    DEFAULT_CHUNKING,
    MAX_CHUNK_SECONDS,
    Chunking,
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


def chunk_offsets(total_samples: int, chunking: Chunking = DEFAULT_CHUNKING) -> list[int]:
    offsets = []
    start = 0
    while start + chunking.chunk_samples <= total_samples:
        offsets.append(start)
        start += chunking.step_samples
    return offsets


def run_pass(
    runtime,
    audio: np.ndarray,
    silence_threshold: float,
    verbose: bool = False,
    on_chunk: callable = None,
    language: str = DEFAULT_LANGUAGE,
    chunking: Chunking = DEFAULT_CHUNKING,
) -> dict:
    offsets = chunk_offsets(audio.size, chunking)
    chunk_samples = chunking.chunk_samples

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
        rtf = latency / chunking.chunk_seconds
        if verbose:
            print(f"  [{offset / SAMPLE_RATE:6.1f}s] {latency:5.2f}s  RTF {rtf:4.2f}  {text[:60]}")
        if on_chunk:
            on_chunk(i + 1, len(offsets), offset / SAMPLE_RATE, latency, rtf, text, False)

    if not latencies:
        return {"chunks": 0, "skipped": skipped, "sample_text": ""}

    rtfs = [latency / chunking.chunk_seconds for latency in latencies]
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
    #
    # Comma-separated rather than choices=, so several models can be compared
    # on one runtime — the question "is int8 worth it here" is about models,
    # not runtimes, and it needs the same fixed clip to mean anything.
    parser.add_argument(
        "--model",
        required=True,
        help="Model key, or several to compare: turbo,turbo-int8. "
        f"Available here: {', '.join(discover())}",
    )
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
    parser.add_argument(
        "--chunk",
        default=str(int(DEFAULT_CHUNKING.chunk_seconds)),
        help="Seconds per chunk, or several to compare: 5,10,30. Whisper pads every chunk to "
        f"30s regardless (the maximum here is {MAX_CHUNK_SECONDS:g}), so a longer chunk spreads "
        "one fixed encoder cost over more audio — the sweep says how much of that is real on "
        "this machine, and the 'you wait' column says what it costs the person speaking.",
    )
    parser.add_argument(
        "--overlap",
        type=float,
        default=DEFAULT_CHUNKING.overlap_seconds,
        help=f"Seconds of overlap between chunks (default: {DEFAULT_CHUNKING.overlap_seconds:g}).",
    )
    parser.add_argument(
        "--repetition-penalty",
        default=str(DEFAULT_DECODING.repetition_penalty),
        help="Penalty on already-emitted tokens, or several to compare: 1.0,1.3. "
        f"1.0 is off and is the default (currently {DEFAULT_DECODING.repetition_penalty:g}). "
        "whispercpp never receives it, so the default is also the only setting on which it "
        "can be compared with the other runtimes.",
    )
    parser.add_argument(
        "--no-repeat-ngram",
        type=int,
        default=DEFAULT_DECODING.no_repeat_ngram_size,
        help=f"Forbid repeating an n-gram of this size; 0 is off (default: "
        f"{DEFAULT_DECODING.no_repeat_ngram_size}).",
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

    known = discover()
    models = [m.strip() for m in args.model.split(",") if m.strip()]
    unknown = [m for m in models if m not in known]
    if unknown:
        parser.error(
            f"Unknown model(s) {', '.join(unknown)}. Available here: {', '.join(known)}"
        )

    try:
        chunkings = [
            Chunking(float(c.strip()), args.overlap)
            for c in args.chunk.split(",")
            if c.strip()
        ]
    except ValueError as exc:
        parser.error(str(exc))
    if not chunkings:
        parser.error("--chunk needs at least one value")

    try:
        decodings = [
            Decoding(args.no_repeat_ngram, float(v.strip()))
            for v in args.repetition_penalty.split(",")
            if v.strip()
        ]
    except ValueError as exc:
        parser.error(str(exc))
    if not decodings:
        parser.error("--repetition-penalty needs at least one value")

    audio = load_audio(args.file)
    duration = audio.size / SAMPLE_RATE
    offsets = chunk_offsets(audio.size, chunkings[0])

    print(f"machine  : {platform.system()} {platform.machine()}")
    print(f"torch    : {torch.__version__}")
    for model_key in models:
        try:
            print(f"model    : {resolve_repo(model_key)}")
        except KeyError:
            print(f"model    : {model_key}  (converted weights only, no source repo)")
    print(f"runtime  : {args.runtime}")
    print(f"clip     : {args.file}  ({duration:.1f}s audio)")
    for chunking in chunkings:
        count = len(chunk_offsets(audio.size, chunking))
        print(f"chunking : {chunking.label()} — {count} chunk{'' if count == 1 else 's'}")

    too_short = [c for c in chunkings if not chunk_offsets(audio.size, c)]
    if too_short:
        raise SystemExit(
            f"Clip is {duration:.1f}s — shorter than one "
            f"{max(c.chunk_seconds for c in too_short):g}s chunk. Use a longer recording, or a "
            "smaller --chunk."
        )

    thread_counts = (
        [int(t.strip()) for t in args.threads.split(",") if t.strip()]
        if args.threads
        else [None]
    )

    model_column = max(len(m) for m in models) if len(models) > 1 else 0
    chunk_column = 7 if len(chunkings) > 1 else 0
    penalty_column = 8 if len(decodings) > 1 else 0
    header = f"{'model':>{model_column}}  " if model_column else ""
    header += f"{'rep pen':>{penalty_column}}  " if penalty_column else ""
    header += f"{'chunk':>{chunk_column}}  " if chunk_column else ""
    # "you wait" is the number the person speaking experiences: they finish a
    # chunk's worth of audio, then wait for it to be transcribed. RTF alone
    # hides this — it improves as chunks grow, while the wait gets worse.
    print(f"\n{header}{'threads':>8}  {'chunks':>6}  {'mean RTF':>9}  {'median':>7}  "
          f"{'worst':>7}  {'mean lat':>9}  {'you wait':>9}  keeps up?")
    print("-" * (83 + model_column + penalty_column + chunk_column
                 + 2 * bool(model_column) + 2 * bool(penalty_column) + 2 * bool(chunk_column)))

    results = []
    for model_key in models:
      for decoding in decodings:
        for chunking in chunkings:
            for threads in thread_counts:
                # Reloaded per count: CTranslate2, whisper.cpp and OpenVINO all
                # fix their thread pool when the model is built, so setting it
                # afterwards (which is all this used to do, via
                # torch.set_num_threads) changed nothing at all for three of
                # the five runtimes.
                try:
                    runtime = load_runtime(args.runtime, model_key, threads, decoding)
                except (FileNotFoundError, RuntimeError, ValueError, KeyError) as exc:
                    # One unconvertible or undeployable combination must not
                    # take the whole sweep down with it — say which one and
                    # carry on, the way the panel greys out a runtime.
                    reason = str(exc).splitlines()[0]
                    if len(models) * len(decodings) * len(chunkings) * len(thread_counts) == 1:
                        raise SystemExit(f"{model_key} on {args.runtime}: {reason}") from None
                    print(f"{model_key:>{model_column}}  skipped — {reason}")
                    break

                first = (
                    model_key == models[0]
                    and decoding is decodings[0]
                    and chunking is chunkings[0]
                    and threads == thread_counts[0]
                )
                if first:
                    print(f"  {runtime.description}")
                    print(f"  language: {LANGUAGES[args.language]}")
                    if not runtime.honours_decoding and (len(decodings) > 1 or decoding.active):
                        print(
                            "  note: this runtime exposes no generation knobs, so "
                            "--repetition-penalty / --no-repeat-ngram do nothing here"
                        )
                    elif len(decodings) == 1:
                        print(f"  decoding: {decoding.label()}")

                # First inference pays for lazy init and cache warm-up; exclude
                # it so the reported numbers reflect steady-state throughput.
                # Warm up at the chunk length actually about to be measured:
                # OpenVINO recompiles for a new input shape, and paying that
                # inside the first timed chunk would be charged to the chunk
                # size rather than to the switch.
                runtime.transcribe(audio[: chunking.chunk_samples], args.language)
                stats = run_pass(
                    runtime,
                    audio,
                    args.silence_threshold,
                    args.verbose,
                    language=args.language,
                    chunking=chunking,
                )

                prefix = f"{model_key:>{model_column}}  " if model_column else ""
                if penalty_column:
                    prefix += f"{decoding.repetition_penalty:>{penalty_column}g}  "
                if chunk_column:
                    prefix += f"{chunking.chunk_seconds:>{chunk_column}g}  "
                label = "default" if threads is None else str(threads)
                if not stats["chunks"]:
                    print(f"{prefix}{label:>8}  every chunk was skipped as silence")
                    continue
                keeps_up = "yes" if stats["mean_rtf"] < 1 else "NO"
                # What the speaker waits for: the chunk has to be spoken before
                # it can be transcribed, so both terms count.
                wait = chunking.chunk_seconds + stats["mean_latency"]
                print(
                    f"{prefix}{label:>8}  {stats['chunks']:>6}  {stats['mean_rtf']:>9.2f}  "
                    f"{stats['median_rtf']:>7.2f}  {stats['worst_rtf']:>7.2f}  "
                    f"{stats['mean_latency']:>8.2f}s  {wait:>8.2f}s  {keeps_up}"
                )
                results.append((model_key, decoding, chunking, threads, stats))

    if len(results) > 1:
        best = min(results, key=lambda r: r[4]["mean_rtf"])
        worst = max(results, key=lambda r: r[4]["mean_rtf"])
        gain = (worst[4]["mean_rtf"] / best[4]["mean_rtf"] - 1) * 100

        def describe(entry) -> str:
            model_key, decoding, chunking, threads, _ = entry
            parts = []
            if len(models) > 1:
                parts.append(model_key)
            if len(decodings) > 1:
                parts.append(f"penalty {decoding.repetition_penalty:g}")
            if len(chunkings) > 1:
                parts.append(f"{chunking.chunk_seconds:g}s chunks")
            parts.append("default threads" if threads is None else f"{threads} threads")
            return " at ".join([parts[0], ", ".join(parts[1:])]) if len(parts) > 1 else parts[0]

        print(
            f"\nbest: {describe(best)} (mean RTF {best[4]['mean_rtf']:.2f}) — "
            f"{gain:.0f}% faster than {describe(worst)}"
        )
        if len(chunkings) > 1:
            print(
                "Longer chunks lower RTF by spreading one fixed encoder pass over more audio. "
                "Read 'you wait' before spending that: it is what the speaker sits through."
            )

    if len(models) > 1 or len(decodings) > 1:
        # Speed is only half of these comparisons: a model or a decoding
        # setting that is faster and mangles the words is not a win. Print
        # what each one actually said, on the same audio, for eyeballing.
        print("\nwhat each setting heard:")
        width = max(model_column, 12)
        for model_key, decoding, chunking, threads, stats in results:
            if chunking is not chunkings[0] or threads != thread_counts[0]:
                continue
            if not stats.get("sample_text"):
                continue
            tag = model_key if len(models) > 1 else ""
            if len(decodings) > 1:
                tag = f"{tag} pen {decoding.repetition_penalty:g}".strip()
            print(f"  {tag:>{width}}  {stats['sample_text']}")


if __name__ == "__main__":
    main()
