# 03. Manage output destinations and connect workflow delivery

Status: wontfix

Legacy outcome: Backend destination management and clip/shared/process-isolated microphone integration are implemented. The backend/workflow gate passed before the parent ran scripts/run_macos.sh; the full Python gate at that point was 192 passed and two opt-in hardware skips. Saved definitions, migration, selection snapshots, and separate delivery outcomes were verified.

## Scope

Implement the [backend destination selection contract](../spec.md#output-destination-selection). A local operator manages saved destinations and performs synthetic delivery tests; API callers select saved IDs. Persist definitions and profile selection in the existing SQLite store. Credentials remain deployment-managed secret references.

## Acceptance checklist

- [x] Destination create, read, update, delete, and synthetic test endpoints validate operator access and configuration.
- [x] Profile, direct microphone, and clip requests select an optional saved destination.
- [x] Shared and process-isolated microphone sessions and clips forward terminal JSON results.
- [x] Start-time settings are stable under edits; delivery failure preserves transcription results and resource cleanup.
- [x] Profile migration preserves existing data; omitted destination keeps existing behavior.
- [x] Status and events expose delivery independently and safely.
- [x] Backend and workflow tests pass before `scripts/run_macos.sh` is run.

## Comments

2026-10-14: implementation assigned to a dedicated feature-development sub-agent. Frontend configuration work follows the backend gate and script startup, as requested by the user.
