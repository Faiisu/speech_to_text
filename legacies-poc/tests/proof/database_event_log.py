"""Opt-in event API proof using a dedicated TimescaleDB database.

Run from the legacy PoC root with an explicit database named `*_test`:
  uv run --group dev --with 'psycopg[binary]' python tests/proof/database_event_log.py \
    --database-url postgresql://postgres:postgres@localhost:5433/sttdemo_test

The runner refuses all other database names, checks the production schema and
migration, inserts uniquely tagged events through the HTTP API, and deletes only
its own session rows on exit.
"""

import argparse
import json
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))


def result(outcome, **details):
    print(json.dumps({"outcome": outcome, **details}, ensure_ascii=False, indent=2, default=str))
    return 0 if outcome == "PASS" else 1 if outcome == "FAIL" else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    database_name = urlparse(args.database_url).path.lstrip("/")
    if not database_name.endswith("_test"):
        return result("SKIP", reason="refusing database name without _test suffix",
                      database=database_name)

    try:
        import psycopg
    except ImportError:
        return result("SKIP", reason="backend dependency psycopg is not installed")

    root = ROOT
    run_id = f"proof-{uuid.uuid4()}"
    session_id, spot_session = f"{run_id}-seed", f"{run_id}-spot"
    word_a, word_b, word_c = (f"proof-{key}-{uuid.uuid4().hex[:8]}" for key in ("a", "b", "c"))
    station_a, station_b, station_c = (f"Proof Line {key}-{uuid.uuid4().hex[:6]}"
                                       for key in ("A", "B", "C"))
    start = datetime.now(timezone.utc).replace(microsecond=0)
    times = [start, start + timedelta(seconds=10), start + timedelta(seconds=20)]
    try:
        with psycopg.connect(args.database_url) as conn:
            actual_db = conn.execute("SELECT current_database()").fetchone()[0]
            if not actual_db.endswith("_test"):
                return result("SKIP", reason="connected database name fails isolated-db guard",
                              database=actual_db)
    except Exception as exc:
        return result("SKIP", reason="isolated TimescaleDB is unavailable",
                      database=database_name, error=str(exc))
    try:
        with psycopg.connect(args.database_url) as conn:
            conn.execute((root / "backend/db/init.sql").read_text(), prepare=False)
            conn.execute((root / "backend/db/migrations/001_add_station.sql").read_text(), prepare=False)
    except Exception as exc:
        return result("FAIL", reason="production schema or migration setup failed",
                      database=database_name, error=str(exc))

    inserted = []
    server = None
    bridge_thread = None
    evidence = {"outcome": "FAIL", "database": database_name, "session_id": run_id}
    try:
        from fastapi.testclient import TestClient
        from app import db
        from app.main import app
        import transcribe

        db.DATABASE_URL = args.database_url
        client = TestClient(app)
        class IngestBridge(BaseHTTPRequestHandler):
            api_client = client
            requests = []

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
                type(self).requests.append(payload)
                response = type(self).api_client.post(self.path, json=payload)
                self.send_response(response.status_code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(response.content)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), IngestBridge)
        bridge_thread = threading.Thread(target=server.serve_forever, daemon=True)
        bridge_thread.start()
        bridge_url = f"http://127.0.0.1:{server.server_port}"
        payloads = [
            {"word": word_a, "detected_at": times[0].isoformat(), "model": "turbo",
             "session_id": session_id, "station": station_a},
            {"word": word_a, "detected_at": times[1].isoformat(), "model": "turbo",
             "session_id": session_id, "station": station_a},
            # Decoys make each filter independently necessary: same word on a
            # second station, same station/word outside either time boundary,
            # and another word inside the target range.
            {"word": word_a, "detected_at": (times[0] - timedelta(seconds=10)).isoformat(),
             "model": "turbo", "session_id": f"{session_id}-range", "station": station_a},
            {"word": word_a, "detected_at": (times[0] + timedelta(seconds=5)).isoformat(),
             "model": "turbo", "session_id": f"{session_id}-station", "station": station_b},
            {"word": word_a, "detected_at": times[2].isoformat(), "model": "turbo",
             "session_id": f"{session_id}-range", "station": station_a},
            {"word": word_b, "detected_at": (times[0] + timedelta(seconds=5)).isoformat(),
             "model": "turbo", "session_id": f"{session_id}-word", "station": station_a},
            {"word": word_b, "detected_at": times[2].isoformat(), "model": "turbo",
             "session_id": f"{session_id}-other", "station": station_b},
        ]
        baseline = client.get("/counts", params={"word": word_a}).json()["count"]
        inserted = [payload["session_id"] for payload in payloads]
        post_results = [client.post("/events", json=payload) for payload in payloads]
        all_ok = all(response.status_code == 201 for response in post_results)
        if not all_ok:
            raise AssertionError(f"event ingest statuses: {[r.status_code for r in post_results]}")

        # A controlled repeated transcript sequence passes through the
        # production spotter and report_event HTTP client into the real API.
        inserted.append(spot_session)
        local_detections = []
        last_alerted = {}
        before_spot = client.get("/counts", params={"word": word_c}).json()["count"]
        for offset in (0.0, 4.0, 4.5, 10.0):
            transcribe.spot_keywords(
                word_c, [word_c], last_alerted, offset,
                model_key="turbo", session_id=spot_session, backend_url=bridge_url,
                on_event=local_detections.append, debounce_seconds=4.5, station=station_c,
            )
        spot_events = client.get("/events", params={"session_id": spot_session}).json()
        spot_delta = client.get("/counts", params={"word": word_c}).json()["count"] - before_spot

        # Decoys test each filter independently. The central word_a rows sit
        # exactly at both ends of [start, start+10s].
        counts = {
            "empty_unique_word": client.get("/counts", params={"word": f"absent-{uuid.uuid4()}"}).json()["count"],
            "delta_word": client.get("/counts", params={"word": word_a}).json()["count"] - baseline,
            "station_and_word": client.get("/counts", params={"station": station_a, "word": word_a}).json()["count"],
            "from_inclusive": client.get("/counts", params={"word": word_a, "from": times[0].isoformat(), "to": times[1].isoformat()}).json()["count"],
            "to_inclusive": client.get("/counts", params={"word": word_a, "from": times[1].isoformat(), "to": times[2].isoformat()}).json()["count"],
            "combined": client.get("/counts", params={"word": word_a, "station": station_a,
                                                        "from": times[0].isoformat(), "to": times[1].isoformat()}).json()["count"],
        }
        events = client.get("/events", params={"session_id": session_id}).json()
        other_events = client.get("/events", params={"session_id": f"{session_id}-other"}).json()
        counts.update({"spotting_count_delta": spot_delta, "spotting_local_events": len(local_detections),
                       "spotting_http_reports": len(IngestBridge.requests),
                       "session_isolation_events": len(events),
                       "other_session_events": len(other_events)})
        checks = {
            "empty_word_count": counts["empty_unique_word"] == 0,
            "word_filter": counts["delta_word"] == 5,
            "station_filter": counts["station_and_word"] == 4,
            "inclusive_from_and_to": counts["from_inclusive"] == 3 and counts["to_inclusive"] == 2,
            "combined_filters": counts["combined"] == 2,
            "spotting_count_delta": counts["spotting_count_delta"] == 3,
            "debounce_suppresses_repeat": counts["spotting_count_delta"] == 3
                and len(local_detections) == 3 and len(IngestBridge.requests) == 3,
            "session_filter": counts["session_isolation_events"] == 2
                and counts["other_session_events"] == 1,
            "seeded_metadata": all(event["model"] == "turbo" and event["station"] == station_a
                                    for event in events),
            "spotting_metadata": all(event["model"] == "turbo" and event["station"] == station_c
                                      and event["session_id"] == spot_session for event in spot_events),
            "ingest_http_status": all(response.status_code == 201 for response in post_results)
                and all(request["station"] == station_c for request in IngestBridge.requests),
        }
        good = all(checks.values())
        evidence.update({"outcome": "PASS" if good else "FAIL", "input_sequence": payloads,
                         "controlled_transcript_sequence": [
                             {"transcript": word_c, "at": t, "expected": expected}
                             for t, expected in ((0.0, "emit"), (4.0, "debounce"),
                                                 (4.5, "emit-boundary"), (10.0, "emit-after-window"))],
                         "ingest_status": [r.status_code for r in post_results],
                         "count_evidence": counts, "events": events,
                         "spotting_events": spot_events,
                         "spotting_local_events": local_detections,
                         "spotting_http_reports": IngestBridge.requests,
                         "checks": {key: "PASS" if value else "FAIL" for key, value in checks.items()}})
    except Exception as exc:
        evidence.update(outcome="FAIL", reason="event API proof failed", error=str(exc))
    finally:
        if server:
            server.shutdown()
            server.server_close()
        if bridge_thread:
            bridge_thread.join(timeout=2)
        cleanup_error = None
        try:
            with psycopg.connect(args.database_url) as conn:
                conn.execute("DELETE FROM detection_events WHERE session_id = ANY(%s)",
                             (inserted,))
        except Exception as exc:
            cleanup_error = str(exc)
        evidence["cleanup"] = "PASS" if cleanup_error is None else "FAIL"
        evidence.setdefault("checks", {})["isolated_cleanup"] = evidence["cleanup"]
        if cleanup_error:
            evidence["cleanup_error"] = cleanup_error
            evidence["outcome"] = "FAIL"
    return result(evidence.pop("outcome"), **evidence)


if __name__ == "__main__":
    sys.exit(main())
