# 06. System observation page

Status: wontfix
Blocked by: none
Execution: superseded-by-system-observability

## Decision

The Control Center must not provide a system-observation page. Operators will inspect persisted host, process, service-event, and performance telemetry in Grafana.

The authoritative replacement is [System Observability](../../system-observability/spec.md), including the database schema, writer, Grafana dashboards, and deployment instructions.
