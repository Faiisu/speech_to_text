# Carry OpenVINO speech filter settings through workflows

Status: wontfix

Legacy outcome: Implemented normalized nested settings through profiles, clip multipart, direct microphone/RTSP, callable workflows, start/status responses, and recovery snapshots. Existing decoding-options JSON storage normalizes old profiles without a schema migration.

## Scope

Implement the [OpenVINO settings API contract](../spec.md#openvino-speech-filter-settings) through a dedicated feature-development sub-agent. Reuse existing decoder JSON persistence and runtime flow configuration; normalize legacy rows without a schema change.

## Acceptance

- Profile, multipart clip, direct microphone/RTSP, and callable workflows accept the nested configuration.
- Invalid input returns HTTP 422 before starting work.
- Complete settings survive reopen, duplication, run snapshots, and recovery after profile edits.
- Both shared and isolated workflows preserve the snapshot and measurements.

## Comments

2026-10-22: Assigned to a `gpt-6-luna` feature-development sub-agent with high reasoning. Parent owns specification updates and integrated verification.


2026-10-22 parent integration verification: `uv run --no-sync python -m pytest -q --import-mode=importlib --disable-warnings --tb=short` passed 554 tests with 9 optional skips. Frontend production build passed. `PYTHON="uv run --no-sync python" npm run test:e2e -- e2e/model_tuning.spec.ts e2e/profile_run_lifecycle.spec.ts e2e/live_audio.spec.ts` passed all 29 browser checks. Final `git diff --check` passed. Real OpenVINO CPU evidence and the observed default-threshold limitation are recorded in [Feature 01 issue 15](../../new-speech-to-text/issues/15-openvino-speech-filter.md). OpenVINO GPU/UBX and optional hardware/MQTT checks were not exercised.
