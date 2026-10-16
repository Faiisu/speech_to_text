# 04. Recover unexpectedly stopped microphone runs

Status: wontfix

Legacy outcome: Process-local microphone recovery is implemented and the software integration gates pass. Physical UBX-330M recovery remains unverified, and restoring runs after backend/container restart is outside this change.

## Scope

Implement the [microphone recovery contract](../spec.md#microphone-recovery). Keep an accepted run active across recoverable workflow endings, using its start-time configuration and destination sender. Explicit stop and shutdown cancel recovery.

## Acceptance checklist

- [x] Unexpected completion, failure, and missing completion trigger reopening with bounded, cancellable backoff.
- [x] Recovery retains the run ID, registry capacity, profile ownership, and immutable settings in both execution modes.
- [x] Recovery events retain diagnostics and replacement source IDs prevent MQTT event identity collisions.
- [x] Stop during waiting or opening and application shutdown prevent further recovery; stop failures report failed state and terminate the controller rather than waiting forever.
- [x] Backend regression tests and integration with Feature 01 drop mode pass.
- [x] Hardware and backend-restart limitations remain documented.

## Comments

2026-10-16: Implementation assigned to the dedicated backend-recovery sub-agent. MQTT retries remain bounded and exhausted deliveries may be dropped; HTTP streaming and durable delivery are outside this change.

Final parent verification: `MQTT_TEST_BROKER_URL=mqtt://127.0.0.1:18884 .venv/bin/python -m pytest -q --tb=short` — 254 passed, 2 hardware tests skipped. A separate temporary MQTT broker exercised real publish/subscribe; it was removed after verification. `npm run test:e2e -- e2e/profile_run_lifecycle.spec.ts` from `speech_to_text/frontend/` — 10 passed. `git diff --check` passed.

The browser assertion now targets the recognized-transcript panel explicitly because live run snapshots and the event timeline both show the transcript. Recovery regression coverage includes immutable MQTT sender reuse, distinct chunk IDs across restarts, retry delays, profile deduplication, explicit stop during opening/backoff, failed stop, and shutdown cancellation/failure.
