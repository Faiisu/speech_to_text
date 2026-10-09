"""Run a real model through the production replay path or live station service.

Examples:
  uv run python tests/proof/real_pipeline.py --source replay --model turbo --runtime ctranslate2 --clip audio/test_clip.wav --keywords 'สวัสดี'
  uv run python tests/proof/real_pipeline.py --source mic --model turbo --runtime ctranslate2 --device 'MacBook Air Microphone' --duration 30 --keywords 'สวัสดี'

The local HTTP collector records reports without touching the configured database.
"""

import argparse
import hashlib
import json
import platform
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


class EventCollector(BaseHTTPRequestHandler):
    events = []

    def do_POST(self):
        if self.path != "/events":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        EventCollector.events.append(json.loads(self.rfile.read(length)))
        self.send_response(201)
        self.end_headers()
        self.wfile.write(b'{"status":"stored"}')

    def log_message(self, *_):
        pass


def _result(outcome: str, **evidence) -> int:
    print(json.dumps({"outcome": outcome, **evidence}, ensure_ascii=False, indent=2))
    return 0 if outcome == "PASS" else 1 if outcome == "FAIL" else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=("replay", "mic"), required=True)
    parser.add_argument("--model", default="turbo")
    parser.add_argument("--runtime", default="ctranslate2")
    parser.add_argument("--clip", default="audio/test_clip.wav")
    parser.add_argument("--device", help="Exact input device name for --source mic")
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--keywords", default="สวัสดี", help="Comma-separated known target keywords")
    parser.add_argument("--language", default="th")
    parser.add_argument("--chunk-seconds", type=float, default=5.0)
    parser.add_argument("--overlap-seconds", type=float, default=1.0)
    parser.add_argument("--silence-threshold", type=float, default=0.02)
    args = parser.parse_args()
    keywords = [word.strip() for word in args.keywords.split(",") if word.strip()]
    if not keywords:
        parser.error("at least one --keywords target is required")
    if args.source == "mic" and not args.device:
        parser.error("--device is required for --source mic")

    from model_catalog import discover
    from runtimes import load_runtime, probe
    from stations.config import Settings, Station
    from stations.engine import Engine
    from stations.replay import ReplayCapture, load_media
    from stations.capture import StationCapture
    from transcribe import Chunking

    catalog = discover()
    if args.model not in catalog:
        return _result("SKIP", reason="model key is not installed", model=args.model,
                       available_models=list(catalog))
    runtime_info = next((entry for entry in probe(args.model) if entry["name"] == args.runtime), None)
    if runtime_info is None or not runtime_info["available"]:
        return _result("SKIP", reason="runtime is unavailable on this machine",
                       model=args.model, runtime=args.runtime,
                       runtime_reason=runtime_info["reason"] if runtime_info else "unknown runtime")

    try:
        chunking = Chunking(args.chunk_seconds, args.overlap_seconds)
    except ValueError as exc:
        parser.error(str(exc))
    if args.source == "replay" and not Path(args.clip).is_file():
        return _result("SKIP", reason="fixed clip is missing", clip=args.clip)
    if args.source == "mic":
        from stations.capture import check_device
        try:
            check_device(args.device)
        except Exception as exc:
            return _result("SKIP", reason=f"input device is unavailable: {exc}", device=args.device)

    try:
        runtime = load_runtime(args.runtime, args.model)
    except Exception as exc:
        return _result("FAIL", reason="runtime was reported available but failed to load",
                       model=args.model, runtime=args.runtime, error=str(exc))

    EventCollector.events = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), EventCollector)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    backend_url = f"http://127.0.0.1:{server.server_port}"
    events = []
    session_id = f"proof-{int(time.time())}"
    input_identity = args.clip if args.source == "replay" else args.device
    clip_identity = None
    replay_audio = None
    if args.source == "replay":
        clip_bytes = Path(args.clip).read_bytes()
        replay_audio = load_media(args.clip)
        clip_identity = {
            "path": str(Path(args.clip).resolve()),
            "sha256": hashlib.sha256(clip_bytes).hexdigest(),
            "duration_seconds": len(replay_audio) / 16_000,
        }
    station = Station(
        id="proof-station", label="Real pipeline proof", device=input_identity,
        keywords=keywords, language=args.language,
        silence_threshold=args.silence_threshold,
        chunk_seconds=chunking.chunk_seconds, overlap_seconds=chunking.overlap_seconds,
    )
    settings = Settings(model=args.model, runtime=args.runtime, backend_url=backend_url,
                        workers=1, stations=[station])
    engine = Engine(settings, on_event=events.append)
    engine._runtime = runtime
    station_health = engine.register(station)
    capture = None
    workers = []
    input_samples = 0
    submitted = []
    drain_timeout = False
    try:
        engine.start()
        if args.source == "replay":
            capture = ReplayCapture(
                station, replay_audio,
                lambda st, chunk, at: (submitted.append(1), engine.submit(st, chunk, at)),
                realtime=False,
            )
            capture.start()
            capture.join(timeout=max(30.0, len(replay_audio) / 16_000 * 15))
            input_samples = len(submitted) * chunking.chunk_samples
            if capture.running:
                raise TimeoutError("fixed clip replay did not finish before the proof timeout")
        else:
            capture = StationCapture(station,
                                     lambda st, chunk, at: (submitted.append(1),
                                                            engine.submit(st, chunk, at)),
                                     on_error=lambda st, exc: events.append(
                                         {"type": "capture_error", "station_id": st.id,
                                          "message": str(exc)}))
            capture.start()
            time.sleep(args.duration)
            capture.stop()
            input_samples = len(submitted) * chunking.chunk_samples
        expected_work = len(submitted)
        deadline = time.monotonic() + max(120.0, expected_work * 30.0)
        finished = 0
        while time.monotonic() < deadline:
            finished = (station_health.chunks_done + station_health.chunks_silent
                        + sum(event.get("type") in ("error", "drop") for event in events))
            if finished >= expected_work and engine.queue.depth == 0:
                break
            time.sleep(0.05)
        drain_timeout = engine.queue.depth != 0 or finished < expected_work
    except Exception as exc:
        return _result("FAIL", reason="real pipeline raised an exception", source=args.source,
                       model=args.model, runtime=args.runtime, error=str(exc))
    finally:
        if capture:
            capture.stop()
        workers = list(engine._workers)
        engine.stop()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    chunks = [event for event in events if event.get("type") == "chunk"]
    transcripts = [event["text"] for event in chunks if event.get("text")]
    combined = " ".join(transcripts)
    detections = [event for event in events if event.get("type") == "keyword"]
    matched = [keyword for keyword in keywords if keyword.lower() in combined.lower()]
    outcomes = {
        "nonempty_transcript": bool(transcripts),
        "targets_present_in_transcript": matched == keywords,
        "target_detections_emitted": {word: any(e.get("keyword") == word for e in detections)
                                       for word in keywords},
        "backend_reports_accepted_by_local_collector": len(EventCollector.events),
    }
    runtime_errors = [event for event in events if event.get("type") in ("error", "capture_error")]
    workers_exited = bool(workers) and all(not worker.is_alive() for worker in workers)
    capture_exited = capture is not None and not capture.running
    checks = {
        "runtime_loaded_and_inferred": "PASS" if transcripts else "FAIL",
        "transcript_nonempty": "PASS" if transcripts else "FAIL",
        "declared_targets_in_transcript": "PASS" if matched == keywords else "FAIL",
        "keyword_detection_events": "PASS" if all(outcomes["target_detections_emitted"].values()) else "FAIL",
        "backend_reporting": ("PASS" if len(EventCollector.events) == len(detections) and detections
                              else "SKIP" if not detections else "FAIL"),
        "station_identity_on_chunks": ("PASS" if chunks and all(
            event.get("station_id") == station.id and event.get("label") == station.label
            for event in chunks) else "FAIL"),
        "capture_stopped": "PASS" if capture_exited else "FAIL",
        "queue_drained": "PASS" if not drain_timeout else "FAIL",
        "worker_threads_stopped": "PASS" if workers_exited else "FAIL",
        "runtime_errors": "FAIL" if runtime_errors else "PASS",
    }
    passed = not drain_timeout and not runtime_errors and workers_exited and capture_exited \
        and outcomes["nonempty_transcript"] and outcomes["targets_present_in_transcript"] and all(
        outcomes["target_detections_emitted"].values()
    ) and len(EventCollector.events) == len(detections)
    health = station_health.as_dict()
    return _result(
        "PASS" if passed else "FAIL", source=args.source, input=input_identity,
        sample_rate=16_000, channels=1, samples_delivered=input_samples,
        model=args.model, runtime=runtime.name,
        runtime_description=runtime.description, machine=platform.platform(), language=args.language,
        clip_identity=clip_identity,
        chunk_seconds=chunking.chunk_seconds, overlap_seconds=chunking.overlap_seconds,
        keywords=keywords, transcript="\n".join(transcripts), detection_timeline=detections,
        local_collector_reports=EventCollector.events, health=health,
        capture_exited=capture_exited, worker_threads_exited=workers_exited,
        queue_drained=not drain_timeout, errors=runtime_errors,
        counts={"processed": sum(not event.get("silent", False) for event in chunks),
                "silent": sum(event.get("silent", False) for event in chunks),
                "dropped": sum(event.get("type") == "drop" for event in events),
                "submitted": expected_work},
        checks=checks,
    )


if __name__ == "__main__":
    sys.exit(main())
