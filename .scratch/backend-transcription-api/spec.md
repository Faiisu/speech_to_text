# Backend Transcription API v1

## Purpose

Expose the transcription workflows to a frontend through a versioned FastAPI API. Backend routes validate HTTP input and map workflow results to responses. Feature 01 owns audio capture/transcription; the workflow owns transcription, Thai keyword matching, and session event orchestration.

## Endpoints

All endpoints use the `/api/v1` prefix.

### `GET /profiles` and `POST /profiles`

Profiles are named, reusable microphone-workflow configurations stored in SQLite. The profile store is durable across backend restarts; run records and events remain in the existing process-local registry. Configure the SQLite file with `SPEECH_TO_TEXT_PROFILE_DB`; its default is `~/.local/share/speech_to_text/profiles.sqlite3`. The parent directory is created when needed.

`GET /profiles` returns `{ "profiles": [...] }`, ordered by `name` and then `profile_id`. `POST /profiles` accepts a complete profile definition and returns HTTP 201 with the created profile:

```json
{
  "name": "Studio Thai",
  "device": "Studio Mic",
  "execution_mode": "per_workflow_process",
  "model": "turbo",
  "runtime": "openvino-gpu",
  "keywords": ["สวัสดี"],
  "silence_threshold": 0.00
}
```

The response includes `profile_id` (a generated opaque ID), the definition fields, `created_at`, and `updated_at` (UTC ISO 8601 timestamps). `device` may be null to use the OS default microphone. `execution_mode` is `shared` or `per_workflow_process` and defaults to `shared`. `model` is a model catalog key; omitted values use the effective configured model (`SPEECH_TO_TEXT_MODEL`, or Feature 01's `turbo` default). `runtime` is a Feature 01 runtime; omitted values use the effective configured runtime (`SPEECH_TO_TEXT_RUNTIME`, or Feature 01's `openvino-gpu` default). The model/runtime pair must be catalog-compatible, but it may be saved when that runtime is currently unavailable. `/models` reports readiness separately. Unknown model keys, unsupported runtime names, and incompatible model/runtime pairs return HTTP 422. `keywords` follows the same non-empty, unique keyword validation as direct transcription requests. `silence_threshold` is the Feature 01 RMS silence threshold; it defaults to `0.00` for profiles and must be a finite number in `[0, 1)`. A value of `0.00` disables silence filtering because no non-negative RMS value falls below it. Direct microphone requests that omit this profile field continue to use Feature 01's flow default of `0.05`. Names must be non-empty after trimming; duplicate names are allowed because `profile_id` identifies a profile. Unknown fields are rejected. Invalid definitions return HTTP 422. SQLite initialization and I/O failures return HTTP 503.

### `GET /models`

Return `{ "default_model": "turbo", "default_runtime": "openvino-gpu", "models": [...] }` through the workflow service. Each catalog record exposes `key`, `display_name`, `installed`, aggregate `ready`, and `runtimes`. Each runtime entry exposes Feature 01 runtime `key`, model `compatible` status, local `ready` status, `precision_options`, and an optional `reason`. Compatibility is independent from readiness: incompatible selections are rejected as invalid configuration, while a compatible but unavailable runtime can be saved and fails at workflow startup with HTTP 503 if it remains unavailable. This endpoint only discovers local/catalog models; it does not download or install weights.

### `GET /profiles/{profile_id}`, `PUT /profiles/{profile_id}`, and `DELETE /profiles/{profile_id}`

`GET` returns the profile representation described above. `PUT` replaces the full definition using the same request body as `POST`, preserves `profile_id` and `created_at`, updates `updated_at`, and returns the updated profile. `DELETE` returns HTTP 204. Each operation returns HTTP 404 for an unknown profile ID. Profile changes do not change existing runs.

### `POST /profiles/{profile_id}/runs`

Start a microphone workflow using the selected profile. The route reads the profile once and copies its `device`, `execution_mode`, `keywords`, `silence_threshold`, `model`, and `runtime` into the new run before starting the workflow. Feature 01 receives the saved value as `flow_config.silence_threshold`; the workflow service loads or reuses the selected model/runtime configuration. Both `shared` and `per_workflow_process` modes receive the selected runtime. Runtime-only CTranslate2 selection uses `int8` when precision has not been explicitly configured; an explicitly configured incompatible precision remains a configuration error. It returns HTTP 202 with `{ "workflow_id": "<id>", "status": "recording" }` after microphone/model startup succeeds, matching `POST /transcriptions/microphones`. A startup or configuration failure uses the same HTTP errors as that endpoint. An unknown profile returns HTTP 404. The run keeps this start-time snapshot if the profile is later edited or deleted; runs are not linked back to or cascaded from profiles.

### `POST /transcriptions/clips`

Accept `multipart/form-data` with a `.wav` upload in `file` and one or more repeated `keywords` fields. Keywords must be unique and non-empty; matching uses the Thai configuration and literal substring rules in the [matching workflow contract](../transcript-matching-forwarding/spec.md#matching-contract). Feature 01 accepts PCM signed 16-bit mono or stereo WAV at 8–48 kHz.

The route validates the request, copies the upload to a temporary WAV file in 1 MiB chunks, and closes the upload before queueing the independent run. The worker passes the temporary path to `transcribe_clip_and_forward` and deletes it on completion or failure. The workflow uses `forwarder=None` and `language="th"`. The route returns HTTP 202 without waiting for transcription:

```json
{"workflow_id":"<id>","status":"queued"}
```

The worker records transcript, match results, completion, or workflow error events and exposes the final transcript and matches in status.

### `POST /transcriptions/microphones`

Accept JSON with required `keywords` and optional exact `device` name. Omitting `device` selects the OS default input. Device names must uniquely identify an input device; ambiguous names cannot be selected. Optional `execution_mode` selects how the workflow owns its model; it defaults to `shared`:

```json
{
  "keywords": ["สวัสดี"],
  "device": "Studio Mic",
  "execution_mode": "per_workflow_process"
}
```

`shared` reuses the backend process's lazy model handle, as before. `per_workflow_process` starts one spawned input process for this microphone workflow and loads a separate model inside that process. The process group is owned by the workflow and cleaned up after normal completion, stop, startup failure, or application shutdown. This mode waits for model and microphone startup before returning, so its initial response can take as long as model loading. Model load/startup failures and startup timeouts return HTTP 503; invalid execution modes return HTTP 422. The route returns HTTP 202 after the selected workflow has started:

```json
{"workflow_id":"<id>","status":"recording"}
```

### `GET /microphones`

Return usable host input devices as `{ "devices": [...] }` through the workflow's `list_available_microphones()` interface, which wraps Feature 01's public device-listing callable. Each record has a stable `name`, `selectable`, `max_input_channels`, `default_samplerate`, and `is_default`. Device indexes are never exposed. Duplicate input names are returned with `selectable: false`; the Feature 01 exact-name selector also rejects ambiguous names. Missing `sounddevice` or PortAudio returns HTTP 503.

### `GET /transcriptions/{workflow_id}`

Return run `workflow_id`, `kind`, `status`, `keywords`, `profile_id`, `profile_name`, `device`, `execution_mode`, `silence_threshold`, `model`, `runtime`, `transcript`, `matches`, `error`, and `created_at`. `profile_id` and `profile_name` are null for clip runs and direct microphone runs that did not start from a profile. `device` and `execution_mode` describe microphone runs and are null for clip runs. `silence_threshold` is the saved start-time value for profile runs and null for other runs. `model` is the effective model key for microphone runs and null for clip runs. Status progresses through `queued`/`running`/`completed`/`failed` for clips and `recording`/`stopping`/`stopped`/`completed`/`failed` for microphone sessions. Unknown IDs return HTTP 404.

### `GET /transcriptions`

Return `{ "transcriptions": [...] }` containing the same status representation for every run currently retained by the in-memory registry, ordered by `created_at` descending and then `workflow_id` descending. This is a live view of the current backend process; it does not include runs from other server workers or runs lost at restart. Registry eviction applies equally to this list and individual status/event lookups.

### `GET /transcriptions/{workflow_id}/events?after=<cursor>`

Return `{ "workflow_id": ..., "events": [...], "next_cursor": n }`. Events are buffered per workflow ID, receive monotonically increasing integer cursors starting at 1, and are not consumed by reads. Send `after=0` for the first page, then use `next_cursor` for the next request. Each run retains at most 1000 events. The registry retains at most 256 runs; when full, admitting a new run evicts the oldest terminal run. Active runs are never evicted. If all slots are active, creation returns HTTP 503. An evicted workflow ID returns HTTP 404. When `after` is older than the retained history, return HTTP 410 with the oldest available cursor; clients should resume from that cursor minus one and account for the missing gap. A cursor ahead of the latest event returns HTTP 422. Unknown workflow IDs return HTTP 404.

### `POST /transcriptions/{workflow_id}/stop`

Request asynchronous stop for a microphone workflow and return HTTP 202 with its current status. A clip workflow cannot be stopped and returns HTTP 409. Unknown workflow IDs return HTTP 404.

## Validation and errors

Invalid input, duplicate or empty keywords, unsupported workflow configuration, and unselectable microphone names return HTTP 422. Missing microphone dependencies, model runtime failures, a full run registry, or reaching the per-process limit of 16 active microphone workflows return HTTP 503. Clip audio decode errors occur in the queued worker and appear as a failed status and `workflow_error` event because the API has already returned 202. No outbound forwarding is configured by this API; workflows do not emit fake `forwarded` events when `forwarder=None`.

## Lifecycle and limits

The public transcription workflow service lazily owns one shared `ModelHandle` per effective model configuration for `shared` mode; profiles with distinct model keys can retain multiple handles in one backend process. In `per_workflow_process` mode, each microphone invocation owns a spawned process that loads and closes its selected model; the application process owns its process group and relays its events. Backend routes call only this workflow service; they do not call Feature 01 directly. A thread-safe in-process backend registry isolates each workflow's lifecycle and bounded event buffer. Terminal runs are retained until capacity pressure evicts them; active runs are retained. At most 16 microphone workflows may be active per backend process across both execution modes. Registry state is process-local and non-durable: restart loses runs and events, and multiple server worker processes have independent registries and model handles. Deployments requiring cross-process monitoring or durable history need shared state outside this feature's scope.

The SQLite profile store is separate from the in-memory run registry. Profiles survive restart and are local to the configured database file; each server process may open the same SQLite file. SQLite stores profile definitions only, not workflow status, transcripts, matches, or events. Starting from a profile copies its definition into the run, so the run remains independently monitorable through the existing status, events, and stop endpoints.

## Non-goals

- Persisting transcription runs, transcripts, matches, or event history.
- Synchronizing in-memory runs across backend worker processes or coordinating microphone/model limits across processes.
- Associating a run with a live profile after startup, or editing a running workflow by changing its profile.
- Adding clip uploads to profiles; profiles configure microphone workflows only.
- Adding authentication, profile ownership, or remote profile synchronization.

Run with the optional backend dependencies:

```bash
uv pip install --python .venv/bin/python -e '.[backend,microphone,openvino]'
uv run --no-sync uvicorn speech_to_text.backend.app:app --host 127.0.0.1 --port 8000
```

The command binds to localhost by default. Choose a different host explicitly when the API must accept remote connections.

## Acceptance criteria

- Clip upload returns a workflow ID immediately; asynchronous completion records transcript, matches, and terminal status.
- Microphone sessions and device discovery call public workflow/Feature 01 interfaces, with no transient device index in the API.
- Status and event polling are isolated per workflow, repeatable, and cursor-addressable; event storage is bounded with documented expiry behavior.
- Stop applies to microphone workflows and rejects clip workflows.
- Microphone requests can select `shared` or `per_workflow_process`; omitted mode preserves shared-model behavior.
- Process-isolated microphone workflows load one model in their child process and clean up the process group on completion, stop, startup failure, and application shutdown.
- Two concurrent `per_workflow_process` API requests run inference in distinct child processes and retain independent transcript/event streams.
- Shared model handles and child process groups close on application shutdown; the API documents its non-durable process-local registry.
- Profile CRUD uses SQLite and profile definitions survive backend restart when the configured database file is retained.
- Profile API responses expose stable IDs, names, device, execution mode, model, runtime, keywords, and timestamps; validation and unknown IDs have documented HTTP behavior.
- Profile API requests and responses include a compatible model/runtime pair; supported runtime choices and readiness are exposed per model.
- Profile API requests and responses include `silence_threshold`, default it to `0.00` when omitted, and reject non-finite values or values outside `[0, 1)`.
- Existing SQLite profile databases gain a `REAL NOT NULL DEFAULT 0.00` silence-threshold column, `TEXT NOT NULL DEFAULT 'turbo'` model column, and `TEXT NOT NULL DEFAULT 'openvino-gpu'` runtime column idempotently without losing saved profiles. On first model/runtime column migration, legacy rows are set to the effective configured value when configured, otherwise Feature 01's default; subsequent startup does not rewrite choices.
- Starting a run from a profile uses a single snapshot of its definition; editing or deleting the profile afterward does not change that run.
- Profile-started microphone workflows receive the saved silence threshold in Feature 01 flow configuration and the saved runtime in model configuration in either execution mode; run status exposes the model/runtime snapshot.
- `GET /transcriptions` lists only currently retained runs in newest-first order and reflects the existing process-local eviction behavior.
- Profile persistence does not make runs, transcripts, matches, or events durable.
- The backend can be launched by uvicorn using the documented optional dependencies.
