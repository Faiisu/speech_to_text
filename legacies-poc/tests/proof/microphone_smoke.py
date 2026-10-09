"""Opt-in hardware proof for a named microphone station.

Run: uv run python tests/proof/microphone_smoke.py --device "MacBook Air Microphone"
Add --manual-reconnect to pause for a human unplug/replug while the watchdog runs.
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from stations.config import Settings, Station
from stations.supervisor import Supervisor


class CaptureOnlyRuntime:
    def transcribe(self, audio, language):
        return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", required=True, help="Exact PortAudio input device name")
    parser.add_argument("--label", default="Hardware proof")
    parser.add_argument("--duration", type=float, default=3.0)
    parser.add_argument("--manual-reconnect", action="store_true")
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error("--duration must be positive")

    from stations import capture
    from stations import supervisor as supervisor_module

    try:
        selected = [d for d in capture.input_devices() if d["name"] == args.device]
        if len(selected) != 1:
            return _result("SKIP", reason=f"exact input device unavailable or ambiguous: {args.device!r}")
        station = Station(id="hardware-proof", label=args.label, device=args.device,
                          keywords=[], language="th", silence_threshold=0.0,
                          chunk_seconds=0.25, overlap_seconds=0.0)
        events = []
        opened_captures = []
        original_open_capture = supervisor_module.open_capture

        class CountingCapture(capture.StationCapture):
            samples_received = 0

            def _callback(self, indata, frame_count, time_info, status):
                self.samples_received += indata.size
                super()._callback(indata, frame_count, time_info, status)

        def open_capture(st, sink, on_error=None):
            current = (CountingCapture(st, sink, on_error) if st.id == station.id
                       else original_open_capture(st, sink, on_error))
            opened_captures.append(current)
            return current

        supervisor_module.open_capture = open_capture
        supervisor_module.WATCH_INTERVAL_SECONDS = 1.0
        supervisor_module.RETRY_BACKOFF_SECONDS = 1.0
        service = Supervisor(Settings(runtime="ctranslate2", stations=[station]), events.append)
        service.engine._runtime = CaptureOnlyRuntime()
        service.engine.load_model = lambda: service.engine._runtime
        service.start()
        try:
            time.sleep(args.duration)
            initial = service.health()["stations"][0]
            if not initial["running"]:
                return _result("SKIP", reason="input did not remain open; check microphone permission",
                               device=args.device, station=station.id, health=initial, events=events)
            if initial["chunks_done"] == 0:
                return _result("FAIL", reason="capture opened but no samples reached station chunking",
                               device=args.device, station=station.id, health=initial, events=events)

            first_capture = opened_captures[-1]
            first_run_samples = first_capture.samples_received
            first_workers = list(service.engine._workers)
            service.stop()
            stopped_health = service.health()["stations"][0]
            stopped_capture = not first_capture.running
            stopped_workers = bool(first_workers) and all(not worker.is_alive() for worker in first_workers)
            service.start()
            restart_deadline = time.monotonic() + 5
            restarted = service.health()["stations"][0]
            while restarted["chunks_done"] == 0 and time.monotonic() < restart_deadline:
                time.sleep(0.1)
                restarted = service.health()["stations"][0]
            second_capture = opened_captures[-1]
            restart = {
                "stopped_cleanly": not stopped_health["running"] and stopped_capture and stopped_workers,
                "stopped_health": stopped_health,
                "capture_thread_exited": stopped_capture,
                "worker_threads_exited": stopped_workers,
                "reopened_same_device": restarted["device"] == args.device and restarted["running"],
                "received_chunks_after_restart": restarted["chunks_done"],
                "samples_after_restart": second_capture.samples_received,
                "outcome": "PASS" if not stopped_health["running"] and stopped_capture
                           and stopped_workers and restarted["running"]
                           and restarted["chunks_done"] > 0 else "FAIL",
            }

            reconnect = None
            if args.manual_reconnect:
                reconnect_event_index = len(events)
                pre_disconnect_session_id = restarted["session_id"]
                pre_disconnect_capture_count = len(opened_captures)
                print("Unplug the selected microphone now, then press Enter.", flush=True)
                input()
                unplug_deadline = time.monotonic() + 20
                while time.monotonic() < unplug_deadline:
                    if any(e.get("state") == "error" and e.get("station_id") == station.id
                           for e in events[reconnect_event_index:]):
                        break
                    time.sleep(0.2)
                saw_error = any(e.get("state") == "error" and e.get("station_id") == station.id
                                for e in events[reconnect_event_index:])
                print("Reconnect the same microphone, then press Enter.", flush=True)
                input()
                reconnect_deadline = time.monotonic() + 30
                recovered_capture = None
                while time.monotonic() < reconnect_deadline:
                    current = service.health()["stations"][0]
                    if len(opened_captures) > pre_disconnect_capture_count:
                        recovered_capture = opened_captures[-1]
                    restarting_seen = any(e.get("state") == "restarting"
                                           and e.get("station_id") == station.id
                                           for e in events[reconnect_event_index:])
                    if (restarting_seen and recovered_capture and current["running"]
                            and current["session_id"] != pre_disconnect_session_id
                            and recovered_capture.samples_received > 0):
                        break
                    time.sleep(0.25)
                current = service.health()["stations"][0]
                restarting_seen = any(e.get("state") == "restarting"
                                       and e.get("station_id") == station.id
                                       for e in events[reconnect_event_index:])
                reconnect = {
                    "saw_capture_error": saw_error,
                    "watchdog_restarting_event": restarting_seen,
                    "recovered_running": current["running"],
                    "same_station_id": current["id"] == station.id,
                    "new_session_after_reconnect": current["session_id"] != pre_disconnect_session_id,
                    "samples_in_recovered_capture": recovered_capture.samples_received if recovered_capture else 0,
                }
                reconnect["outcome"] = "PASS" if all((
                    reconnect["saw_capture_error"], reconnect["watchdog_restarting_event"],
                    reconnect["recovered_running"], reconnect["same_station_id"],
                    reconnect["new_session_after_reconnect"],
                    reconnect["samples_in_recovered_capture"] > 0,
                )) else "FAIL"
            else:
                reconnect = {"outcome": "SKIP", "reason": "manual unplug/replug was not requested"}

            overall = "PASS" if restart["outcome"] == "PASS" and reconnect["outcome"] in ("PASS", "SKIP") else "FAIL"
            return _result(overall,
                           device=args.device, device_index=selected[0]["index"],
                           station=station.id, duration=args.duration, health=initial,
                           samples_delivered=first_run_samples, restart=restart,
                           restart_samples=second_capture.samples_received,
                           reconnect=reconnect, events=events)
        finally:
            service.stop()
            supervisor_module.open_capture = original_open_capture
    except (OSError, RuntimeError) as exc:
        return _result("SKIP", reason=f"microphone could not be opened: {exc}", device=args.device)


def _result(outcome: str, **evidence) -> int:
    print(json.dumps({"outcome": outcome, **evidence}, ensure_ascii=False, indent=2))
    return 0 if outcome in ("PASS", "SKIP") else 1


if __name__ == "__main__":
    sys.exit(main())
