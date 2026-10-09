"""Checked-in dashboard and schema remain aligned with the telemetry contract."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_keeps_individual_rtf_rows_and_operator_editing_enabled():
    dashboard = json.loads((ROOT / "deploy/observability/grafana/dashboards/speech-telemetry.json").read_text())
    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    query = panels["Individual inferred chunks"]["targets"][0]["rawSql"]
    for field in ("recorded_at", "feature_id", "operation", "pid", "source_id", "sequence",
                  "audio_seconds", "inference_seconds", "rtf", "status"):
        assert field in query
    event_query = panels["Recent service events"]["targets"][0]["rawSql"]
    for filter_name in ("service", "feature_id", "severity", "pid", "source_id"):
        assert f"IN (${{{filter_name}:sqlstring}})" in event_query
    provider = (ROOT / "deploy/observability/grafana/provisioning/dashboards/provider.yml").read_text()
    assert "allowUiUpdates: true" in provider
    assert (ROOT / "compose.observability.yml").exists()


def test_dashboard_multi_value_filters_use_grafana_sql_string_format():
    dashboard = json.loads((ROOT / "deploy/observability/grafana/dashboards/speech-telemetry.json").read_text())
    for variable in dashboard["templating"]["list"]:
        assert variable["multi"] and variable["includeAll"]
        assert variable["allValue"] == "__all", "All must stay independent of cached query options"
        assert variable["current"]["value"] == "$__all"
        assert "__no_matching_values__" in variable["query"]
        assert "WHERE NOT EXISTS" in variable["query"]

    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    queries = {
        "Individual inferred chunks": ("feature_id", "operation", "pid", "source_id", "status"),
        "Per-chunk real-time factor (target ≤ 1.0)": ("feature_id", "operation", "pid", "source_id"),
        "Recent service events": ("service", "feature_id", "severity", "pid", "source_id"),
        "Service errors per minute": ("service", "feature_id", "pid", "source_id"),
    }
    for title, variables in queries.items():
        sql = panels[title]["targets"][0]["rawSql"]
        for variable in variables:
            assert f"IN (${{{variable}:sqlstring}})" in sql
            assert f"('${{{variable}:raw}}' = '__all' OR " in sql


def test_schema_has_indexed_timeseries_and_retention_configuration():
    first = (ROOT / "speech_to_text/features/system_observability/migrations/001_initial.sql").read_text()
    second = (ROOT / "speech_to_text/features/system_observability/migrations/002_retention_updates.sql").read_text()
    for table in ("operation_measurements", "service_events", "host_samples", "process_samples", "writer_health"):
        assert f"create_hypertable('{table}'" in first
        assert table in first
    assert "remove_retention_policy" in second and "add_retention_policy" in second
    assert "retention_days" in second
