"""How many stations can one machine actually sustain?

benchmark.py answers "which runtime is fastest on one clip". This answers the
question the product turns on: with N microphones feeding one shared model,
does every stream keep up, and where does it stop?

It runs the real Engine, the real bounded queue, and the real backpressure
policy. A harness that transcribed N clips in N threads would measure raw
throughput and miss the thing that actually degrades — queueing.

    uv run python benchmark_parallel.py --clips audio/ --sweep 1,2,3,4
    uv run python benchmark_parallel.py --clips a.mp4 b.mp4 c.mp4 --streams 3
"""

from __future__ import annotations

import argparse
import statistics
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from stations.config import Settings, Station
from stations.engine import Engine
from stations.replay import MediaError, ReplayCapture, load_media

MEDIA_SUFFIXES = {
    ".wav", ".flac", ".mp3", ".ogg", ".m4a", ".aac",
    ".mp4", ".mkv", ".mov", ".avi", ".webm",
}


def collect_clips(paths: list[str]) -> list[Path]:
    """Accept files, directories, or a mix. Directories are not recursed."""
    clips: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            clips.extend(sorted(p for p in path.iterdir() if p.suffix.lower() in MEDIA_SUFFIXES))
        elif path.exists():
            clips.append(path)
        else:
            raise SystemExit(f"No such clip: {path}")
    if not clips:
        raise SystemExit("No audio or video files found in the given paths")
    return clips


@dataclass
class Options:
    """Everything a measurement needs, independent of where it was asked for.

    The CLI fills this from argparse and the web UI from a request body, so
    both drive exactly the same measurement rather than two that drift.
    """

    duration: float = 60.0
    model: str = "turbo"
    runtime: str = "openvino-gpu"
    language: str = "th"
    chunk_seconds: float = 5.0
    overlap_seconds: float = 1.0
    silence_threshold: float = 0.02
    queue_size: int = 6
    workers: int = 1
    realtime: bool = True


class Run:
    """One measured run at a fixed number of concurrent streams."""

    def __init__(self, streams: int, args: Options, clips: list, on_event=None) -> None:
        self.streams = streams
        self.args = args
        self.clips = clips
        self._on_event = on_event
        self.latencies: dict[str, list[float]] = {}
        self.rtfs: dict[str, list[float]] = {}
        self.silent = 0
        self.depths: list[int] = []
        self._lock = threading.Lock()

    def on_event(self, event: dict) -> None:
        if event.get("type") != "chunk":
            return
        with self._lock:
            if event["silent"]:
                self.silent += 1
                return
            self.latencies.setdefault(event["label"], []).append(event["latency"])
            self.rtfs.setdefault(event["label"], []).append(event["rtf"])

    def execute(self) -> dict:
        self._emit({"type": "run_start", "streams": self.streams})
        stations = []
        for n in range(self.streams):
            clip = self.clips[n % len(self.clips)]
            stations.append(
                Station(
                    id=f"s{n + 1}",
                    label=f"stream {n + 1} ({clip.name[:22]})",
                    device="replay",
                    keywords=[],  # no detections: this measures throughput, not the DB
                    language=self.args.language,
                    chunk_seconds=self.args.chunk_seconds,
                    overlap_seconds=self.args.overlap_seconds,
                    silence_threshold=self.args.silence_threshold,
                )
            )

        settings = Settings(
            model=self.args.model,
            runtime=self.args.runtime,
            queue_size=self.args.queue_size,
            workers=self.args.workers,
            stations=stations,
        )
        engine = Engine(settings, on_event=self.on_event)
        self._emit({"type": "loading", "streams": self.streams,
                    "model": self.args.model, "runtime": self.args.runtime})
        engine.load()
        engine.start()
        for station in stations:
            engine.register(station)

        audio = {clip: load_media(clip) for clip in {self.clips[n % len(self.clips)] for n in range(self.streams)}}
        captures = [
            ReplayCapture(
                station,
                audio[self.clips[n % len(self.clips)]],
                engine.submit,
                realtime=self.args.realtime,
                loop=True,
            )
            for n, station in enumerate(stations)
        ]

        started = time.perf_counter()
        sampling = threading.Event()

        def sample() -> None:
            last_emit = 0.0
            while not sampling.wait(0.25):
                self.depths.append(engine.queue.depth)
                now = time.perf_counter()
                # Once a second is enough for a progress bar and keeps the SSE
                # feed from competing with the inference it is measuring.
                if self._on_event and now - last_emit >= 1.0:
                    last_emit = now
                    with self._lock:
                        rtfs = [x for v in self.rtfs.values() for x in v]
                        done = len(rtfs) + self.silent
                    self._on_event({
                        "type": "progress",
                        "streams": self.streams,
                        "elapsed": round(now - started, 1),
                        "duration": self.args.duration,
                        "processed": done,
                        "dropped": sum(engine.queue.dropped.values()),
                        "queue_depth": engine.queue.depth,
                        "mean_rtf": round(statistics.mean(rtfs), 2) if rtfs else None,
                    })

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()

        for capture in captures:
            capture.start()
        time.sleep(self.args.duration)
        for capture in captures:
            capture.stop()
        # Let the queue drain briefly so a chunk in flight isn't counted as
        # lost, but don't wait it out — a backlog at this point is the finding.
        deadline = time.perf_counter() + self.args.chunk_seconds * 2
        while engine.queue.depth and time.perf_counter() < deadline:
            time.sleep(0.2)
        elapsed = time.perf_counter() - started
        sampling.set()
        engine.stop()

        submitted = sum(c.chunks_emitted for c in captures)
        dropped = sum(engine.queue.dropped.values())
        processed = sum(len(v) for v in self.latencies.values()) + self.silent
        all_latencies = [x for v in self.latencies.values() for x in v]
        all_rtfs = [x for v in self.rtfs.values() for x in v]

        result = {
            "streams": self.streams,
            "elapsed": elapsed,
            "submitted": submitted,
            "processed": processed,
            "dropped": dropped,
            "backlog": engine.queue.depth,
            "silent": self.silent,
            "mean_rtf": statistics.mean(all_rtfs) if all_rtfs else None,
            "p95_rtf": (statistics.quantiles(all_rtfs, n=20)[18] if len(all_rtfs) > 20
                        else (max(all_rtfs) if all_rtfs else None)),
            "mean_latency": statistics.mean(all_latencies) if all_latencies else None,
            "max_depth": max(self.depths) if self.depths else 0,
            "per_stream": {k: statistics.mean(v) for k, v in self.rtfs.items()},
            # Keeping up means every chunk that a microphone produced actually
            # got transcribed. Mean RTF below 1 is not sufficient on its own:
            # a run can average under 1 and still have dropped audio in bursts.
            "sustained": dropped == 0 and engine.queue.depth == 0,
        }
        self._emit({"type": "run_done", "result": result})
        return result

    def _emit(self, event: dict) -> None:
        if self._on_event:
            self._on_event(event)


def summarise(results: list[dict], chunk_seconds: float, runtime: str) -> dict:
    """The finding, as a value — the CLI prints it, the web UI renders it."""
    sustained = [r["streams"] for r in results if r["sustained"]]
    failed = [r["streams"] for r in results if not r["sustained"]]
    best = max(sustained) if sustained else 0
    if best:
        text = (f"Sustained {best} concurrent station{'s' if best > 1 else ''} on {runtime} "
                f"with {chunk_seconds:g}s chunks.")
        if failed:
            text += (f" Fell behind at {min(failed)} — raising the chunk length spreads one "
                     "fixed encoder pass over more audio and is the first thing to try.")
    else:
        text = ("No stream count kept up. Try a longer chunk, a lighter model, "
                "or a faster runtime.")
    return {"max_sustained": best, "first_failure": min(failed) if failed else None, "text": text}


def report(result: dict, chunk_seconds: float) -> None:
    n = result["streams"]
    print(f"\n  {n} stream{'s' if n > 1 else ''}")
    print(f"    submitted {result['submitted']}  processed {result['processed']}  "
          f"dropped {result['dropped']}  backlog {result['backlog']}  silent {result['silent']}")
    if result["mean_rtf"] is not None:
        print(f"    RTF per chunk   mean {result['mean_rtf']:.2f}   p95 {result['p95_rtf']:.2f}   "
              f"latency {result['mean_latency']:.2f}s for a {chunk_seconds:g}s chunk")
    print(f"    queue depth     max {result['max_depth']}")
    for label, rtf in sorted(result["per_stream"].items()):
        print(f"      {label:34} {rtf:.2f}")
    verdict = "\033[32mSUSTAINED\033[0m" if result["sustained"] else "\033[31mFELL BEHIND\033[0m"
    print(f"    verdict: {verdict}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure how many concurrent stations one machine sustains.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--clips", nargs="+", required=True,
                        help="Audio/video files, or directories of them. Reused round-robin "
                             "if there are fewer clips than streams.")
    parser.add_argument("--streams", type=int, help="Run one measurement at this many streams")
    parser.add_argument("--sweep", help="Comma-separated stream counts, e.g. 1,2,3,4")
    parser.add_argument("--duration", type=float, default=60.0, help="Seconds to run each measurement")
    parser.add_argument("--model", default="turbo")
    parser.add_argument("--runtime", default="openvino-gpu")
    parser.add_argument("--language", default="th")
    parser.add_argument("--chunk-seconds", type=float, default=5.0)
    parser.add_argument("--overlap-seconds", type=float, default=1.0)
    parser.add_argument("--silence-threshold", type=float, default=0.02)
    parser.add_argument("--queue-size", type=int, default=6)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--max-throughput", dest="realtime", action="store_false",
                        help="Feed as fast as the model accepts instead of at microphone "
                             "pace. Measures raw throughput; 'keeping up' stops meaning anything.")
    parser.set_defaults(realtime=True)
    args = parser.parse_args()

    if not args.streams and not args.sweep:
        parser.error("give --streams N or --sweep 1,2,3")
    counts = ([int(x) for x in args.sweep.split(",")] if args.sweep else [args.streams])

    try:
        clips = collect_clips(args.clips)
    except MediaError as exc:
        raise SystemExit(str(exc)) from None

    print(f"model {args.model} on {args.runtime}   "
          f"{args.chunk_seconds:g}s chunks / {args.overlap_seconds:g}s overlap   "
          f"{args.workers} worker(s), queue {args.queue_size}")
    print(f"clips: {', '.join(c.name for c in clips)}")
    print(f"pacing: {'real time (as microphones)' if args.realtime else 'maximum throughput'}, "
          f"{args.duration:g}s per measurement")

    options = Options(
        duration=args.duration,
        model=args.model,
        runtime=args.runtime,
        language=args.language,
        chunk_seconds=args.chunk_seconds,
        overlap_seconds=args.overlap_seconds,
        silence_threshold=args.silence_threshold,
        queue_size=args.queue_size,
        workers=args.workers,
        realtime=args.realtime,
    )

    results = []
    for count in counts:
        results.append(Run(count, options, clips).execute())
        report(results[-1], args.chunk_seconds)

    verdict = summarise(results, args.chunk_seconds, args.runtime)
    sustained = [r["streams"] for r in results if r["sustained"]]
    print("\n" + "─" * 62)
    if sustained:
        best = max(sustained)
        print(f"  Sustained {best} concurrent station{'s' if best > 1 else ''} "
              f"on {args.runtime} with {args.chunk_seconds:g}s chunks.")
        failed = [r["streams"] for r in results if not r["sustained"]]
        if failed:
            print(f"  Fell behind at {min(failed)}. Raising --chunk-seconds spreads one fixed "
                  "encoder pass over more audio and is the first thing to try.")
    else:
        print("  No stream count kept up. Try a larger --chunk-seconds, a lighter model, "
              "or a faster runtime.")
    print("─" * 62 + "\n")


if __name__ == "__main__":
    main()
