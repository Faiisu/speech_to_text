# 05. System observation sampler and API

Status: wontfix
Blocked by: none
Execution: superseded-by-system-observability

## Decision

This proposed in-memory sampler and historical observation API were superseded by the requirement to persist service, process, host, and per-chunk performance telemetry in a database and view it in Grafana. Do not extend or deploy the in-memory observation API as the operator experience.

The authoritative replacement is [System Observability](../../system-observability/spec.md), implemented by its own tickets.
