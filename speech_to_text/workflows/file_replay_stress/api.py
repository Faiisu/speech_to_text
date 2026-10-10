"""Cold-start file-replay stress matrix across model process topologies."""

from __future__ import annotations

import json
import platform
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path
from queue import Empty

from speech_to_text.features.model_deployment import (
    PacedWavSource,
    start_multiprocess_microphone_flows,
)
from speech_to_text.features.model_deployment.audio import read_clip
from speech_to_text.features.model_deployment.config import (
    flow_config as validate_flow_config,
    model_config as validate_model_config,
)

WORKFLOW_COUNTS = (1, 2, 4)
REPLAY_LOOPS = 2
REALTIME_TOLERANCE = 0.05
PHYSICAL_MEMORY_RESERVE = 0.25


def _percentile(values, fraction):
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _summary(values):
    return {
        "count": len(values),
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "maximum": max(values) if values else None,
    }


def _memory_snapshot(process_group):
    try:
        import psutil
    except ImportError:
        return None, None, None, "install the benchmark extra to collect process memory"
    process = psutil.Process()
    processes = [process]
    child_processes = []
    process_handles = (
        [session.process for session in process_group.sessions]
        if hasattr(process_group, "sessions")
        else process_group
    )
    for process_handle in process_handles:
        try:
            child = psutil.Process(process_handle.pid)
            descendants = [child, *child.children(recursive=True)]
            processes.extend(descendants)
            child_processes.extend(descendants)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    rss = 0
    child_rss = 0
    for item in processes:
        try:
            measured = item.memory_info().rss
            rss += measured
            if item in child_processes:
                child_rss += measured
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return rss, child_rss, psutil.virtual_memory(), "available"


def _hardware_identity():
    result = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
    }
    try:
        import psutil

        memory = psutil.virtual_memory()
        result.update(
            {
                "physical_memory_bytes": memory.total,
                "available_memory_bytes_at_start": memory.available,
            }
        )
    except ImportError:
        result["memory_measurement"] = "unavailable: install the benchmark extra"
    return result


def _emit(progress_callback, update):
    if progress_callback is not None:
        progress_callback(dict(update))


def _unavailable_trial(topology, workflow_count, reason, config):
    return {
        "status": "unavailable",
        "topology": topology,
        "workflow_count": workflow_count,
        "model": config["model"],
        "runtime": config["runtime"],
        "precision": config["precision"],
        "hardware": _hardware_identity(),
        "unavailable_reason": str(reason),
        "chunks": [],
        "capacity_verdict": "unavailable",
    }


def _run_trial(
    *,
    topology,
    workflow_count,
    clip_path,
    duration_seconds,
    model_config,
    flow_settings,
    runtime_factory,
    progress_callback,
):
    source_ids = [f"file-replay-{uuid.uuid4().hex[:12]}" for _ in range(workflow_count)]
    flows = [{**flow_settings, "source_id": source_id} for source_id in source_ids]
    from functools import partial

    audio_source_factory = partial(
        PacedWavSource, path=str(clip_path), loops=REPLAY_LOOPS
    )
    peak_rss = 0
    peak_child_rss = 0
    memory_status = "available"

    def observe_startup(processes):
        nonlocal peak_rss, peak_child_rss, memory_status
        current_rss, current_child_rss, _, memory_status = _memory_snapshot(processes)
        if current_rss is not None:
            peak_rss = max(peak_rss, current_rss)
            peak_child_rss = max(peak_child_rss, current_child_rss or 0)

    ready_started = time.perf_counter()
    group = start_multiprocess_microphone_flows(
        [None] * workflow_count,
        model_config,
        flow_settings,
        topology=topology,
        runtime_factory=runtime_factory,
        audio_source_factory=audio_source_factory,
        flow_configs=flows,
        startup_monitor_callback=observe_startup,
    )
    ready_seconds = time.perf_counter() - ready_started
    capture_window_seconds = duration_seconds * REPLAY_LOOPS
    peak_queue = 0 if group._input_queue is not None else None
    chunks = []
    source_events = []
    terminal_ids = set()
    replay_started = time.perf_counter()
    last_progress = replay_started
    terminal_wall_seconds = None
    queue_depth_at_input_drain = None
    try:
        while len(terminal_ids) < workflow_count:
            current_rss, current_child_rss, _, memory_status = _memory_snapshot(group)
            if current_rss is not None:
                peak_rss = max(peak_rss, current_rss)
                peak_child_rss = max(peak_child_rss, current_child_rss or 0)
            if group._input_queue is not None:
                try:
                    peak_queue = max(peak_queue, group._input_queue.qsize())
                except (NotImplementedError, OSError, ValueError):
                    pass
            for session in group.sessions:
                while True:
                    try:
                        event = session.result_queue.get_nowait()
                    except Empty:
                        break
                    if event.get("type") == "measurement":
                        chunks.append(event)
                    else:
                        source_events.append(event)
                    if event.get("type") == "completed":
                        terminal_ids.add(event.get("source_id"))
            now = time.perf_counter()
            if now - last_progress >= 1:
                _emit(
                    progress_callback,
                    {
                        "event": "trial_progress",
                        "topology": topology,
                        "workflow_count": workflow_count,
                        "completed_workflows": len(terminal_ids),
                        "elapsed_seconds": now - replay_started,
                    },
                )
                last_progress = now
            if now - replay_started > capture_window_seconds + 600:
                raise TimeoutError("File replay did not reach terminal completion")
            time.sleep(0.05)
        terminal_wall_seconds = max(time.perf_counter() - replay_started, 1e-9)
        if group._input_queue is not None:
            try:
                queue_depth_at_input_drain = group._input_queue.qsize()
                peak_queue = max(peak_queue, queue_depth_at_input_drain)
            except (NotImplementedError, OSError, ValueError):
                queue_depth_at_input_drain = None
        drain_started = time.perf_counter()
        group.stop(timeout=60)
        drain_seconds = time.perf_counter() - drain_started
    except BaseException:
        group.abort(timeout=5)
        raise

    telemetry = []
    while True:
        try:
            telemetry.append(group.telemetry_queue.get_nowait())
        except Empty:
            break
    # Telemetry carries the same queue and timing fields on both topologies.
    chunks_by_key = {
        (item.get("source_id"), item.get("sequence")): item for item in telemetry
    }
    measured_chunks = []
    for event in chunks:
        key = (event.get("source_id"), event.get("sequence"))
        item = dict(event)
        item.update(chunks_by_key.get(key, {}))
        item.setdefault(
            "elapsed_seconds",
            item.get("queue_wait_seconds", 0) + item.get("inference_seconds", 0),
        )
        item.setdefault(
            "audio_seconds",
            item.get("audio_seconds", item.get("chunk_duration_seconds")),
        )
        item.setdefault("status", "completed")
        measured_chunks.append(item)
    errors = [event for event in source_events if event.get("type") == "error"]
    terminals = [event for event in source_events if event.get("type") == "completed"]
    failed_chunks = [
        item for item in measured_chunks if item.get("status") != "completed"
    ]
    dropped = sum(
        int(event.get("rejected_chunks", 0))
        + int(event.get("discarded_queued_chunks", 0))
        for event in errors
    ) + sum(bool(item.get("discarded_inflight")) for item in telemetry)
    inferred_seconds = defaultdict(float)
    for item in measured_chunks:
        inferred_seconds[item.get("source_id")] += float(
            item.get("inference_seconds", 0)
        )
    utilization = (
        {"shared-model": sum(inferred_seconds.values()) / capture_window_seconds}
        if topology == "shared-model"
        else {
            source_id: inferred_seconds[source_id] / capture_window_seconds
            for source_id in source_ids
        }
    )
    clean_terminals = len(terminals) == workflow_count and all(
        event.get("status") == "stopped" for event in terminals
    )
    drained = queue_depth_at_input_drain in (None, 0)
    utilization_passes = all(
        value <= 1 + REALTIME_TOLERANCE for value in utilization.values()
    )
    if (
        errors
        or failed_chunks
        or dropped
        or not clean_terminals
        or not drained
        or not utilization_passes
    ):
        verdict = "fail"
    else:
        verdict = "pass"
    elapsed_values = [float(item.get("elapsed_seconds", 0)) for item in measured_chunks]
    rtf_values = [
        float(item["rtf"]) for item in measured_chunks if item.get("rtf") is not None
    ]
    result = {
        "status": "completed",
        "topology": topology,
        "workflow_count": workflow_count,
        "model": model_config["model"],
        "runtime": model_config["runtime"],
        "precision": model_config["precision"],
        "language": flow_settings["language"],
        "clip_path": str(clip_path),
        "loops": REPLAY_LOOPS,
        "clip_duration_seconds": duration_seconds,
        "hardware": _hardware_identity(),
        "startup_seconds_to_ready": ready_seconds,
        "model_startup_seconds": group.model_load_seconds
        if group.model_load_seconds is not None
        else sum(group.per_source_model_load_seconds.values()),
        "per_source_model_startup_seconds": group.per_source_model_load_seconds,
        "per_source_startup_seconds": group.source_load_seconds,
        "capture_window_seconds": capture_window_seconds,
        "terminal_wall_seconds": terminal_wall_seconds,
        "stop_drain_seconds": drain_seconds,
        "queue_capacity_chunks": model_config["queue_capacity"],
        "max_observed_queue_depth": peak_queue,
        "queue_depth_after_input_drain": queue_depth_at_input_drain,
        "queue_depth_measurement": "shared-model-input-queue"
        if group._input_queue is not None
        else "unavailable-per-input-process",
        "source_statuses": [
            {
                "source_id": source_id,
                "status": next(
                    (
                        event.get("status")
                        for event in terminals
                        if event.get("source_id") == source_id
                    ),
                    "missing-terminal-event",
                ),
            }
            for source_id in source_ids
        ],
        "chunk_elapsed_seconds": _summary(elapsed_values),
        "chunk_rtf": _summary(rtf_values),
        "inference_utilization_by_model": utilization,
        "dropped_or_failed_chunks": dropped + len(failed_chunks),
        "failed_inference_chunks": len(failed_chunks),
        "errors": errors,
        "peak_total_process_rss_bytes": peak_rss or None,
        "peak_child_process_rss_bytes": peak_child_rss or None,
        "memory_measurement": memory_status,
        "clean_terminal_completion": clean_terminals,
        "queue_empty_after_input_drain": drained,
        "capacity_verdict": verdict,
        "chunks": measured_chunks,
    }
    return result


def run_file_replay_stress(
    *,
    clip_path="audio/test-audio.wav",
    model_config=None,
    flow_config=None,
    output_directory=None,
    progress_callback=None,
    runtime_factory=None,
):
    """Run independent 1/2/4-input cold starts in both model topologies.

    ``progress_callback`` receives dictionaries with ``trial_started``,
    ``trial_progress``, ``trial_completed``, ``trial_unavailable``, and
    ``matrix_completed`` event names. The returned mapping contains separate
    topology reports, each with its own hardware identity and trial list.
    """
    resolved_model = validate_model_config(model_config)
    resolved_flow = validate_flow_config(flow_config)
    clip_path = Path(clip_path).expanduser()
    if not clip_path.is_absolute() and clip_path == Path("audio/test-audio.wav"):
        repository_root = Path(__file__).resolve().parents[3]
        clip_path = repository_root / clip_path
    clip_path = clip_path.resolve()
    audio = read_clip(clip_path)
    duration_seconds = audio.size / 16000
    if not duration_seconds:
        raise ValueError("Replay WAV must contain audio frames")
    del audio
    reports = {}
    one_workflow_peak_rss = None
    for topology in ("shared-model", "per-input-model"):
        trials = []
        topology_unavailable = None
        for workflow_count in WORKFLOW_COUNTS:
            if topology == "per-input-model" and workflow_count > 1:
                if one_workflow_peak_rss is None:
                    reason = "one-workflow process-memory footprint is unavailable"
                    trials.append(
                        _unavailable_trial(
                            topology, workflow_count, reason, resolved_model
                        )
                    )
                    _emit(
                        progress_callback,
                        {
                            "event": "trial_unavailable",
                            "topology": topology,
                            "workflow_count": workflow_count,
                            "reason": reason,
                        },
                    )
                    continue
                try:
                    import psutil

                    memory = psutil.virtual_memory()
                    projected = one_workflow_peak_rss * workflow_count
                    reserve = int(memory.total * PHYSICAL_MEMORY_RESERVE)
                    if (
                        projected > memory.available
                        or memory.total - projected < reserve
                    ):
                        reason = (
                            "projected model processes exceed available memory or "
                            "leave less than 25% of physical memory free"
                        )
                        trials.append(
                            _unavailable_trial(
                                topology, workflow_count, reason, resolved_model
                            )
                        )
                        _emit(
                            progress_callback,
                            {
                                "event": "trial_unavailable",
                                "topology": topology,
                                "workflow_count": workflow_count,
                                "reason": reason,
                            },
                        )
                        continue
                except ImportError:
                    reason = "memory preflight unavailable: install the benchmark extra"
                    trials.append(
                        _unavailable_trial(
                            topology, workflow_count, reason, resolved_model
                        )
                    )
                    _emit(
                        progress_callback,
                        {
                            "event": "trial_unavailable",
                            "topology": topology,
                            "workflow_count": workflow_count,
                            "reason": reason,
                        },
                    )
                    continue
            _emit(
                progress_callback,
                {
                    "event": "trial_started",
                    "topology": topology,
                    "workflow_count": workflow_count,
                },
            )
            try:
                trial = _run_trial(
                    topology=topology,
                    workflow_count=workflow_count,
                    clip_path=clip_path,
                    duration_seconds=duration_seconds,
                    model_config=resolved_model,
                    flow_settings=resolved_flow,
                    runtime_factory=runtime_factory,
                    progress_callback=progress_callback,
                )
            except Exception as exc:
                trial = _unavailable_trial(
                    topology, workflow_count, exc, resolved_model
                )
                if topology == "per-input-model" and workflow_count == 1:
                    topology_unavailable = str(exc)
            trials.append(trial)
            if topology == "per-input-model" and workflow_count == 1:
                one_workflow_peak_rss = trial.get("peak_child_process_rss_bytes")
            _emit(
                progress_callback,
                {
                    "event": "trial_completed"
                    if trial["status"] == "completed"
                    else "trial_unavailable",
                    "topology": topology,
                    "workflow_count": workflow_count,
                    "capacity_verdict": trial.get("capacity_verdict"),
                    "reason": topology_unavailable,
                },
            )
        reports[topology] = {
            "topology": topology,
            "model": resolved_model["model"],
            "runtime": resolved_model["runtime"],
            "precision": resolved_model["precision"],
            "language": resolved_flow["language"],
            "hardware": _hardware_identity(),
            "trials": trials,
        }
    if output_directory is not None:
        output_directory = Path(output_directory).expanduser()
        output_directory.mkdir(parents=True, exist_ok=True)
        for topology, report in reports.items():
            (output_directory / f"{topology}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    _emit(progress_callback, {"event": "matrix_completed", "topologies": list(reports)})
    return reports
