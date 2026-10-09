"""Measure shared-model and per-input microphone process topologies.

Run with ``python -m speech_to_text.features.model_deployment.capacity`` after
installing the ``microphone`` and ``benchmark`` extras and the selected model
runtime. This tool captures real microphone input at audio pace.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import sys
import time
from collections import defaultdict
from queue import Empty

from .errors import AudioInputError, ModelLoadError
from .process_topology import start_multiprocess_microphone_flows

REALTIME_TOLERANCE = 0.05


def _validate_args(args):
    for name in ("duration_seconds", "stop_timeout", "enqueue_timeout"):
        value = getattr(args, name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{name.replace('_', ' ')} must be finite and positive")
    if not isinstance(args.device, (list, tuple)) or not args.device:
        raise ValueError(
            "device must be a non-empty list or tuple of stable microphone names"
        )


def run_benchmark(
    args, *, runtime_factory=None, audio_source_factory=None, flow_configs=None
):
    _validate_args(args)
    config = {
        "model": args.model,
        "runtime": args.runtime,
        "precision": args.precision,
        "queue_capacity": args.queue_capacity,
        "enqueue_timeout_seconds": args.enqueue_timeout,
    }
    flow = {
        "language": args.language,
        "chunk_seconds": args.chunk_seconds,
        "silence_threshold": args.silence_threshold,
    }
    started = time.perf_counter()
    try:
        group = start_multiprocess_microphone_flows(
            args.device,
            config,
            flow,
            topology=args.topology,
            runtime_factory=runtime_factory,
            audio_source_factory=audio_source_factory,
            flow_configs=flow_configs,
        )
    except (AudioInputError, ModelLoadError) as exc:
        return {
            "status": "unavailable",
            "topology": args.topology,
            "model": args.model,
            "runtime": args.runtime,
            "precision": args.precision,
            "hardware": {
                "platform": platform.platform(),
                "machine": platform.machine(),
                "python": sys.version.split()[0],
            },
            "prerequisite_error": str(exc),
            "measurements": None,
        }
    elapsed_to_ready = time.perf_counter() - started
    peak_rss = 0
    peak_queue = None if group._input_queue is None else 0
    queue_depth_at_capture_stop = None
    memory_status = "available"
    try:
        import psutil

        process = psutil.Process()
    except ImportError:
        process = None
        memory_status = "install the benchmark extra to collect process memory"
    run_started = time.perf_counter()
    try:
        while time.perf_counter() - run_started < args.duration_seconds:
            if group._input_queue is not None:
                try:
                    peak_queue = max(peak_queue or 0, group._input_queue.qsize())
                except (NotImplementedError, OSError, ValueError):
                    pass
            if process is not None:
                processes = [process, *process.children(recursive=True)]
                current_rss = 0
                for child in processes:
                    try:
                        current_rss += child.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                peak_rss = max(peak_rss, current_rss)
            time.sleep(
                min(
                    0.25,
                    max(0, args.duration_seconds - (time.perf_counter() - run_started)),
                )
            )
        capture_stopped_at = time.perf_counter()
        if group._input_queue is not None:
            try:
                queue_depth_at_capture_stop = group._input_queue.qsize()
                peak_queue = max(peak_queue or 0, queue_depth_at_capture_stop)
            except (NotImplementedError, OSError, ValueError):
                pass
        drain_started = time.perf_counter()
        group.stop(timeout=args.stop_timeout)
        drain_seconds = time.perf_counter() - drain_started
    except BaseException:
        try:
            group.abort(timeout=min(args.stop_timeout, 5))
        except Exception as cleanup_error:
            raise RuntimeError(
                f"Capacity run failed and process cleanup failed: {cleanup_error}"
            ) from cleanup_error
        raise
    events = []
    for session in group.sessions:
        while True:
            try:
                events.append(session.result_queue.get_nowait())
            except Empty:
                break
    telemetry = []
    while True:
        try:
            telemetry.append(group.telemetry_queue.get_nowait())
        except Empty:
            break
    errors = [event for event in events if event["type"] == "error"]
    queue_discarded = sum(event.get("discarded_queued_chunks", 0) for event in errors)
    rejected_chunks = sum(event.get("rejected_chunks", 0) for event in errors)
    inference_failures = sum(
        event.get("code") == "CHUNK_INFERENCE_FAILED" for event in errors
    )
    inflight_discarded = sum(
        item.get("discarded_inflight", False) for item in telemetry
    )
    per_source_audio = defaultdict(float)
    for item in telemetry:
        per_source_audio[item["source_id"]] += item["chunk_duration_seconds"]
    capture_seconds = max(capture_stopped_at - run_started, 1e-9)
    per_source_inference = defaultdict(float)
    for item in telemetry:
        per_source_inference[item["source_id"]] += item["inference_seconds"]
    per_source_audio_throughput = {
        session.source_id: per_source_audio[session.source_id] / capture_seconds
        for session in group.sessions
    }
    if args.topology == "shared-model":
        utilization = {
            "shared-model": sum(per_source_inference.values()) / capture_seconds
        }
    else:
        utilization = {
            session.source_id: per_source_inference[session.source_id] / capture_seconds
            for session in group.sessions
        }
    minimum_audio_seconds = max(5.0, args.duration_seconds * (1 - REALTIME_TOLERANCE))
    sufficient_workload = bool(group.sessions) and all(
        per_source_audio[session.source_id] >= minimum_audio_seconds
        for session in group.sessions
    )
    failed_sources = [
        item
        for item in group.sessions
        if next(
            (
                event.get("status")
                for event in events
                if event.get("source_id") == item.source_id
                and event.get("type") == "completed"
            ),
            None,
        )
        != "stopped"
    ]
    failed_run = bool(errors or failed_sources)
    dropped_or_failed = (
        queue_discarded + rejected_chunks + inflight_discarded + inference_failures
    )
    if failed_run or dropped_or_failed:
        realtime_verdict = "fail"
    elif not sufficient_workload:
        realtime_verdict = "inconclusive-insufficient-workload"
    elif any(value > 1 + REALTIME_TOLERANCE for value in utilization.values()):
        realtime_verdict = "fail"
    else:
        realtime_verdict = "pass"
    result = {
        "status": "completed",
        "topology": args.topology,
        "model": args.model,
        "runtime": args.runtime,
        "precision": args.precision,
        "language": args.language,
        "hardware": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
        },
        "sources": [
            {
                "source_id": session.source_id,
                "status": next(
                    (
                        event["status"]
                        for event in events
                        if event["source_id"] == session.source_id
                        and event["type"] == "completed"
                    ),
                    "missing-terminal-event",
                ),
            }
            for session in group.sessions
        ],
        "startup_seconds_to_ready": elapsed_to_ready,
        "model_startup_seconds": group.model_load_seconds,
        "per_source_startup_seconds": group.source_load_seconds,
        "capture_window_seconds": capture_seconds,
        "stop_drain_seconds": drain_seconds,
        "minimum_audio_seconds_per_source_for_verdict": minimum_audio_seconds,
        "queue_capacity_chunks": args.queue_capacity,
        "max_observed_queue_depth": peak_queue,
        "queue_depth_at_capture_stop": queue_depth_at_capture_stop,
        "queue_depth_measurement": "shared-model-input-queue"
        if group._input_queue is not None
        else "unavailable-per-input-process",
        "eligible_audio_chunk_count": len(telemetry),
        "eligible_audio_seconds_by_source": dict(per_source_audio),
        "per_source_audio_seconds_per_capture_second": per_source_audio_throughput,
        "inference_utilization_by_model": utilization,
        "realtime_tolerance_fraction": REALTIME_TOLERANCE,
        "realtime_verdict": realtime_verdict,
        "discarded_queued_chunks": queue_discarded,
        "rejected_chunks": rejected_chunks,
        "discarded_inflight_chunks": inflight_discarded,
        "failed_inference_chunks": inference_failures,
        "dropped_or_failed_chunks": dropped_or_failed,
        "errors": errors,
        "peak_total_process_rss_bytes": peak_rss if process is not None else None,
        "memory_measurement": memory_status,
        "chunks": telemetry,
        "source_identity_by_chunk": [
            {"source_id": item["source_id"], "sequence": item["sequence"]}
            for item in telemetry
        ],
    }
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Compare real microphone capacity across process topologies"
    )
    parser.add_argument(
        "--topology", required=True, choices=("shared-model", "per-input-model")
    )
    parser.add_argument(
        "--device",
        required=True,
        nargs="+",
        help="Exact stable input device names, one per microphone",
    )
    parser.add_argument("--duration-seconds", type=float, default=60)
    parser.add_argument("--stop-timeout", type=float, default=60)
    parser.add_argument("--model", default="turbo")
    parser.add_argument("--runtime", default="openvino-gpu")
    parser.add_argument("--precision", default="source")
    parser.add_argument("--language", default="th")
    parser.add_argument("--chunk-seconds", type=float, default=5)
    parser.add_argument("--silence-threshold", type=float, default=0.05)
    parser.add_argument("--queue-capacity", type=int, default=6)
    parser.add_argument("--enqueue-timeout", type=float, default=1)
    parser.add_argument(
        "--output", help="Write JSON evidence to this path instead of stdout"
    )
    args = parser.parse_args()
    try:
        _validate_args(args)
    except ValueError as exc:
        parser.error(str(exc))
    result = run_benchmark(args)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    if result.get("status") == "unavailable":
        return 2
    verdict = result.get("realtime_verdict")
    if verdict == "pass":
        return 0
    if verdict and verdict.startswith("inconclusive"):
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
