# 02. Grafana dashboards and observability Compose stack

Status: wontfix
Blocked by: 01
Execution: implementation-complete; local-and-linux-deploy-passed; hardware-capacity-pending

Legacy outcome: the TimescaleDB/Grafana stack was implemented and deployed for the former Control Center, then retired. It is not part of the current application.

## Scope

Provide local/host deployment for TimescaleDB and Grafana with a provisioned read-only data source and dashboards described in the [System Observability spec](../spec.md).

## Acceptance checklist

- [x] Compose starts TimescaleDB and Grafana with persistent volumes and loopback-only host ports by default.
- [x] Grafana's PostgreSQL data source is provisioned from environment variables and uses a read-only database role.
- [x] Provisioned dashboards can be edited and saved in Grafana UI, and the Grafana volume preserves those edits across container replacement.
- [x] Dashboards show per-chunk RTF rows, RTF over time and target 1.0, host CPU/memory/swap, owned process CPU/RSS, and telemetry writer health.
- [x] A recent service-event/error panel filters structured records by service, feature, severity, PID, and source.
- [x] Dashboard filters identify feature, operation, PID, source, and status without averaging away individual chunk records.
- [x] A documented example environment file has placeholders only; real credentials are not committed.
- [x] A Compose smoke check verifies database readiness, telemetry insert/query, and Grafana dashboard provisioning.

## Comments

Use version-controlled Grafana provisioning. The model-serving application remains on the host; only observability infrastructure runs in Compose.

## Verification

The stack is running on the local development host at loopback ports 5433 and 3000. The smoke check queried an inserted event through Grafana's provisioned data source and verified Editor folder access. Linux target-host validation remains pending.

## Linux deployment verification: 2026-10-09

The deployment smoke check passed on the actual UBX-330M: database event/RTF insertion, Grafana read-only query, retention policies, datasource/dashboard provisioning, and Editor folder permissions. Real OpenVINO GPU measurements and microphone lifecycle events were read through the Grafana database role; all nine provisioned panel queries succeeded with All filters. Host/process/writer samples were present. See [target deployment proof](../../new-speech-to-text/issues/05-target-hardware-acceptance.md#linux-deployment-proof-2026-10-09) for the authoritative machine setup, measured performance, evidence artifact, and remaining hardware limits.
