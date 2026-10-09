"""Provision Grafana folder editing and prove database/Grafana connectivity."""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

import psycopg
from psycopg.rows import dict_row

from speech_to_text.features.system_observability import (
    TelemetryWriter, WriterConfig, make_event, make_measurement,
)


def request(url: str, *, username: str, password: str, method="GET", body=None):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = response.read()
            return json.loads(payload) if payload else {}
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Grafana request failed ({type(exc).__name__})") from None


def main():
    host = os.getenv("GRAFANA_HOST", "http://127.0.0.1:3000").rstrip("/")
    username = os.environ["GRAFANA_ADMIN_USER"]
    password = os.environ["GRAFANA_ADMIN_PASSWORD"]
    dashboard = request(f"{host}/api/dashboards/uid/speech-telemetry-overview", username=username, password=password)
    assert dashboard.get("dashboard", {}).get("title") == "Speech-to-Text Operations", "dashboard provisioning missing"
    datasource = request(f"{host}/api/datasources/uid/speech-telemetry", username=username, password=password)
    assert datasource.get("type") == "grafana-postgresql-datasource", "TimescaleDB data source missing"
    folders = request(f"{host}/api/folders", username=username, password=password)
    folder = next((item for item in folders if item.get("title") == "Speech Telemetry"), None)
    assert folder, "Speech Telemetry folder missing"
    request(f"{host}/api/folders/{folder['uid']}/permissions", username=username, password=password,
        method="POST", body={"items": [{"role": "Editor", "permission": 2}]})
    permissions = request(f"{host}/api/folders/{folder['uid']}/permissions", username=username, password=password)
    roles = {item.get("role"): item.get("permission") for item in permissions if item.get("role")}
    assert roles.get("Editor") == 2, "folder editor permissions were not applied"

    database_url = os.environ["SPEECH_TO_TEXT_TELEMETRY_DATABASE_URL"]
    writer = TelemetryWriter(WriterConfig(database_url=database_url, flush_interval_seconds=.1))
    smoke_id = f"observability-smoke-{uuid.uuid4().hex}"
    writer.start()
    measurement = make_measurement(feature_id="feature-01-model-deployment", operation="observability_smoke_check",
        pid=os.getpid(), source_id=smoke_id, sequence=0, audio_seconds=30.0, inference_seconds=24.0)
    writer.publish_measurement(measurement)
    writer.publish_event(make_event(event_name="observability_smoke_check", source_id=smoke_id,
        attributes={"code": "smoke"}))
    try:
        reader_dsn = (f"host=127.0.0.1 port={os.getenv('TELEMETRY_DATABASE_PORT', '5433')} "
            f"dbname={os.getenv('TELEMETRY_DATABASE', 'speech_telemetry')} user=telemetry_grafana "
            f"password={os.environ['TELEMETRY_GRAFANA_PASSWORD']}")
        deadline = time.monotonic() + 15
        with psycopg.connect(reader_dsn, row_factory=dict_row) as connection:
            privileges = connection.execute("SELECT has_table_privilege(current_user,'service_events','SELECT') AS can_read, "
                "has_table_privilege(current_user,'service_events','INSERT') AS can_write").fetchone()
            assert privileges["can_read"] and not privileges["can_write"], "Grafana database role is not read-only"
            while time.monotonic() < deadline:
                row = connection.execute("SELECT event_name FROM service_events WHERE source_id=%s", (smoke_id,)).fetchone()
                measured = connection.execute("SELECT rtf,pid,sequence FROM operation_measurements "
                    "WHERE source_id=%s AND operation='observability_smoke_check'", (smoke_id,)).fetchone()
                if row and measured:
                    break
                time.sleep(.25)
            else:
                raise RuntimeError("read-only Grafana role could not find inserted event and RTF measurement")
            assert row["event_name"] == "observability_smoke_check"
            assert measured["rtf"] == .8 and measured["pid"] > 0 and measured["sequence"] == 0
        grafana_query = request(f"{host}/api/ds/query", username=username, password=password,
            method="POST", body={"from":"now-5m", "to":"now", "queries":[{
                "refId":"A", "datasource":{"type":"postgres", "uid":"speech-telemetry"},
                "rawSql":f"SELECT rtf FROM operation_measurements WHERE source_id='{smoke_id}' AND sequence=0",
                "format":"table"}]})
        result = grafana_query.get("results", {}).get("A", {})
        assert not result.get("error") and "0.8" in json.dumps(result), \
            "Grafana could not query the inserted per-operation RTF through its provisioned data source"
    finally:
        writer.close(timeout=3)
        with psycopg.connect(os.environ["SPEECH_TO_TEXT_TELEMETRY_MIGRATION_DATABASE_URL"]) as connection:
            connection.execute("DELETE FROM operation_measurements WHERE source_id=%s", (smoke_id,))
            connection.execute("DELETE FROM service_events WHERE source_id=%s", (smoke_id,))
    with psycopg.connect(os.environ["SPEECH_TO_TEXT_TELEMETRY_MIGRATION_DATABASE_URL"]) as connection:
        retention = connection.execute("SELECT config->>'drop_after' FROM timescaledb_information.jobs "
            "WHERE proc_name='policy_retention'").fetchall()
        assert len(retention) == 5, "retention policy missing from one or more telemetry tables"
        expected = f"{os.getenv('SPEECH_TO_TEXT_TELEMETRY_RETENTION_DAYS', '30')} days"
        assert all(row[0] == expected for row in retention), "retention policy does not match configured days"
    print("PASS: DB event/RTF insert, Grafana read-only query, retention policies, datasource/dashboard, and Editor permission")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL: observability smoke check ({type(exc).__name__})", file=sys.stderr)
        sys.exit(1)
