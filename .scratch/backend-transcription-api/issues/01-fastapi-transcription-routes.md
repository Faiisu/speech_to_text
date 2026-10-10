# 01. Implement FastAPI transcription routes

Status: ready-for-agent

## Scope

Implement the API and lifecycle described in [Backend Transcription API v1](../spec.md), including clip and microphone workflows, device listing, status, buffered event polling, microphone stop, and the frontend process profiles slice.

## Acceptance checklist

- [x] Add a versioned FastAPI app with clip, microphone, device, status, event, and stop endpoints.
- [x] Route transcription through the workflow interface and support no-forwarder behavior.
- [x] Add public Feature 01 device listing, wrapped by the workflow, with stable names and ambiguity metadata.
- [x] Stream uploads to temporary WAV paths and remove them after asynchronous completion.
- [x] Maintain isolated process-local run state, bounded run retention, bounded event buffers, and capped microphone concurrency.
- [x] Load and close one shared model per app lifecycle; document process-local limits.
- [x] Allow microphone requests to choose shared or per-workflow model processes and clean up child process groups through the workflow lifecycle.
- [x] Verify two simultaneous process-isolated microphone API requests infer in distinct child processes with independent results.
- [x] Add only backend runtime dependencies and a concise uvicorn command.
- [x] Update authoritative API, architecture, and getting-started docs.

### Process profiles slice

- [x] Store profile definitions in SQLite with a configurable database path and documented default; preserve profiles across backend restart.
- [x] Add `GET`/`POST /profiles` and `GET`/`PUT`/`DELETE /profiles/{profile_id}` with the profile fields, validation, status codes, and response shapes in the API spec.
- [x] Add `POST /profiles/{profile_id}/runs` and start a microphone workflow from one immutable snapshot of the profile's device, execution mode, and keywords.
- [x] Keep profile CRUD independent from existing run state: editing or deleting a profile does not alter or delete an active or retained run.
- [x] Add `GET /transcriptions` to list the current process's retained runs newest first, using the existing status representation and retention limits.
- [x] Keep runs, transcripts, matches, and events in memory; profile persistence does not make run history durable.
- [x] Verify profile persistence across app restart and verify the profile-start snapshot remains in effect after profile edit or deletion.

## Comments

Implementation landed in `backend/profile_store.py` and the backend API routes. `PYTHONPATH=. .venv/bin/pytest tests/backend -q` passes 27 tests, including profile persistence and run snapshot coverage. The run reports a timeout warning from the existing two-process microphone cleanup case; it does not fail the suite. `git diff --check` passes.
