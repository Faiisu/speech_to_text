# 07. Consolidate chunk measurement publishing

Status: ready-for-agent
Execution: software-implementation-complete; local-writer-insert-check-passed
Blocked by: none

## Scope

Consolidate Feature 01 per-chunk measurement construction and publication across finite clips, same-process microphone sessions, and both process deployment topologies. Preserve timing and attribution while keeping telemetry sinks asynchronous and isolated from inference.

## Acceptance checklist

- [x] One feature-local helper owns per-chunk measurement creation and best-effort sink publication for every inference topology.
- [x] Every inferred chunk retains actual inference PID, source ID, sequence, UTC completion timestamp, audio duration, inference duration, status, and numeric RTF.
- [x] Silent chunks skipped before inference produce no measurement.
- [x] Sink failures do not change transcript, session completion, or process-group behavior.
- [x] Regression tests cover writer serialization and telemetry delivery in both process topologies where feasible.
- [x] Terminal completion events are persisted exactly once per source.

## Comments

The existing [System Observability ticket](../../system-observability/issues/01-persist-feature-and-host-telemetry.md) owns the corrective lifecycle and structured event persistence plan. Continue using its insert-only writer and allowlisted event attributes; do not persist transcripts, audio, credentials, or free-form exception messages.

## Verification

Verified with `.venv/bin/python -m pytest tests/feature_01 tests/control_center/test_api.py tests/system_observability -q` (100 passed, 2 opt-in hardware skips). New Control Center regressions confirm lifecycle and terminal events reach the writer contract without SSE reads, process-group measurement/event PIDs come from the owning worker in both topologies, repeated group stop and shutdown do not duplicate terminal lifecycle events, sanitized capture errors are persisted, and failing telemetry does not prevent clip or microphone processing. Existing writer tests cover safe event attributes and event/measurement SQL serialization. The injected local database insert/read evidence is recorded in [System Observability ticket 01](../../system-observability/issues/01-persist-feature-and-host-telemetry.md#corrective-database-verification). This does not prove physical hardware or Linux-host behavior.
