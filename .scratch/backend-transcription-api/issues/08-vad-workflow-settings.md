# Persist and propagate speech gate settings

Status: wontfix

Legacy outcome: Implemented normalized speech-gate settings through callable workflows, direct microphone/RTSP requests, clip multipart requests, additive profile storage, start-time snapshots, and both recovery modes. Detector-only skips retain timing without counting as replay failures; detector availability errors map through the workflow boundary.

## Scope

Implement the [speech gate API contract](../spec.md#speech-gate-configuration) through a dedicated feature-development sub-agent. Callable workflows, direct live requests, saved profiles, and clip multipart requests carry the same validated VAD mapping and adjustable RMS threshold. Preserve existing in-progress isolated-clip changes.

## Acceptance

- Invalid settings return 422; normalized settings survive profile migration, reopen, duplication, starts, immutable snapshots, and recovery.
- Clip/live and shared/isolated execution receive both settings.
- Detector-only skips retain timing/events and do not count as capacity failures.
- Focused transport, persistence, propagation, recovery, and aggregation checks pass.

## Comments

2026-10-22: Assigned to a `gpt-6-luna` feature-development sub-agent with high reasoning. Parent owns documentation and integration verification.


2026-10-22: Parent integration verification: `uv run --no-sync python -m pytest -q --import-mode=importlib --disable-warnings --tb=short` passed with 507 tests and 9 optional skips. Profile/recovery tests cover enabled VAD surviving later profile edits. The clip API test uses the real feature pipeline to verify skipped measurement status, reason, latest RTF, and aggregates. Hardware/MQTT optional checks were not enabled.
