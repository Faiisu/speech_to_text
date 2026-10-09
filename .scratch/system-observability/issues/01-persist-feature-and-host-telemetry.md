# 01. Persist feature and host telemetry

Status: ready-for-human
Blocked by: none
Execution: implementation-complete; local-deploy-passed; linux-host-pending

## Scope

Implement the shared asynchronous telemetry writer, TimescaleDB schema and migrations, service-owned two-second host/process sampler, and Feature 01 RTF publishing described in the [System Observability spec](../spec.md). Keep inference independent of SQL and remove the in-memory-only observation API/page from the Control Center.

## Acceptance checklist

- [x] A feature contribution can publish timestamped operation measurements through the shared writer contract.
- [x] Services can persist structured timestamped lifecycle, queue, warning, and error events without storing audio, transcripts, credentials, or arbitrary payloads.
- [x] Feature 01 stores one record per inferred chunk for clip, live, shared-model, and per-input-model flows, with actual inference PID, source, sequence, UTC completion time, durations, and numeric RTF.
- [x] Host and explicitly owned process resource samples are stored every two seconds; unrelated OS processes are never enumerated.
- [x] Database writes are asynchronous, batched, bounded, retried, and drained on orderly shutdown without blocking real-time inference.
- [x] Database unavailability and queue overflow are observable; transcription continues if telemetry storage is unavailable.
- [x] Schema migrations create indexed time-series tables and retention policy; 30-day default retention is configurable.
- [x] Tests cover persistence contracts, both process topologies, retention, errors/retries, and no-SQL operation paths.
- [x] Existing Feature 01 and Control Center contract tests continue to pass.

## Comments

Keep TimescaleDB and Grafana deployment files in the repository root outside `legacies-poc/`. Never store database passwords in tracked files. Do not add historical observation routes or a new Control Center page.

## Verification

Verified on the local macOS development host with 93 tests passing and 2 opt-in tests skipped. The Compose smoke check verified inserts, read-only queries, retry/retention policy state, and Grafana data source queries. Physical Linux target-host behavior remains a deployment validation.

## Corrective plan from review findings 2 and 3

- Consolidate Feature 01 per-chunk measurement construction and sink delivery behind one feature-local helper used by finite clips, same-process microphone sessions, and both process topologies. Keep inference timing boundaries, inference PID, source ID, sequence, completion timestamp, status, and per-chunk RTF intact. Measurement failures remain isolated from transcription.
- Publish allowlisted service events for model load/close, microphone and process-group start/stop/completion, queue saturation, and inference/capture failures. Route worker events through the owning service writer; never send transcripts, audio, arbitrary exception text, or credentials to telemetry.
- Add regressions that exercise service-event writer serialization and event delivery from direct microphone and process-group paths, including terminal-event uniqueness and process attribution where available.
- Keep the existing insert-only writer contract and schema. Do not introduce migrations unless implementation proves the current contract cannot represent an event.

Corrective software implementation is complete. `.venv/bin/python -m pytest tests/feature_01 tests/control_center/test_api.py tests/system_observability -q` passes 100 tests with 2 opt-in hardware tests skipped. Coverage includes no-SSE event delivery, unique terminal events through explicit stop and shutdown, both process topologies, worker PID attribution, sanitized capture errors, and writer failure isolation for clip and microphone processing. Existing writer tests cover the insert serialization path. See [corrective database verification](#corrective-database-verification). Linux host validation remains pending.

## Corrective database verification

On the local development host, the parent agent verified inserts through the application writer and read-back through Grafana's read-only database role using injected runtime and capture boundaries. The run observed 15 lifecycle/service-event rows and 6 per-chunk RTF rows across a finite clip, one microphone, both process topologies, model/group shutdown, and repeated group stop without an SSE consumer. Persisted rows retained actual worker PIDs. This establishes the database insert/read path for those injected software flows; it does not establish physical microphone, OpenVINO/GPU, or Linux-host behavior.
