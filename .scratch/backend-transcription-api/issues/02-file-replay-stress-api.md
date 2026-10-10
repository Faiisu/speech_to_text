# 02. File-replay stress-test API

Status: ready-for-agent
Execution: completed
Blocked by: Feature 01 file-replay stress workflow

## Scope

Expose the file-replay stress workflow through asynchronous backend endpoints, so callers can submit a configured matrix, poll lifecycle state, consume progress events, and retrieve its per-topology report.

The authoritative request, response, conflict, validation, lifecycle, and event contract is in the [backend transcription API specification](../spec.md#post-stress-tests).

## Acceptance checklist

- [x] Add `POST /api/v1/stress-tests`, `GET /api/v1/stress-tests/{stress_test_id}`, and cursor-addressable `GET /api/v1/stress-tests/{stress_test_id}/events`.
- [x] Accept strict model/runtime/precision overrides and pin input path to the repository's fixed WAV asset.
- [x] Run the workflow asynchronously and return a queued identifier without loading models in the request handler.
- [x] Retain at most 256 stress runs and 1000 events per run in a process-local bounded registry; evict the oldest terminal run when full, return 410 with `oldest_cursor` for expired history, and return 503 if no terminal run can be evicted.
- [x] Serialize stress/microphone starts: reject concurrent stress matrices, stress starts during an active microphone workflow, and microphone starts during a queued or running stress matrix with HTTP 409.
- [x] Map unavailable trials to successful matrix completion with `unavailable` trial statuses; reserve API `failed` for orchestration failures.
- [x] Add backend API E2E coverage for request validation, asynchronous status/events, final reports, and conflict behavior without loading real models or requiring hardware.
- [x] Preserve existing transcription and profile API behavior.

Verification: `uv run --no-sync pytest tests/backend/test_stress_api.py` (4 passed); `git diff --check` passes.
