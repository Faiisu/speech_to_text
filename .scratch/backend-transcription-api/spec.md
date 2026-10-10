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

The response includes `profile_id` (a generated opaque ID), the definition fields, `created_at`, and `updated_at` (UTC ISO 8601 timestamps). `device` may be null to use the OS default microphone. `execution_mode` is `shared` or `per_workflow_process` and defaults to `shared`. `model` is a model catalog key; omitted values use the effective configured model (`SPEECH_TO_TEXT_MODEL`, or Feature 01's `turbo` default). `runtime` is a Feature 01 runtime; omitted values use the effective configured runtime (`SPEECH_TO_TEXT_RUNTIME`, or Feature 01's `openvino-gpu` default). The model/runtime pair must be catalog-compatible, but it may be saved when that runtime is currently unavailable. `/models` reports readiness separately. Unknown model keys, unsupported runtime names, and incompatible model/runtime pairs return HTTP 422. `keywords` follows the same non-empty, unique keyword validation as direct transcription requests. `silence_threshold` is the Feature 01 RMS silence threshold; it defaults to `0.00` for profiles and must be a finite number in `[0, 1)`. A value of `0.00` disables silence filtering because no non-negative RMS value falls below it. Direct microphone requests that omit this profile field continue to use Feature 01's flow default of `0.05`. Names must be non-empty after trimming and unique after trimming, Unicode NFC normalization, and Unicode case folding. A create or update that matches another profile returns HTTP 409; updating a profile to its existing name is allowed. This check is serialized with the SQLite write transaction, so concurrent requests cannot create normalized-name collisions. Existing duplicate rows are retained, but a new or updated profile cannot take a name matching any of them. Unknown fields are rejected. Invalid definitions return HTTP 422. SQLite initialization and I/O failures return HTTP 503.

In the frontend profile list, Duplicate opens the create form with the source profile's device, execution mode, model, runtime, keywords, and silence threshold copied into the form. The new name starts blank. Opening the form never creates a profile or changes the source; saving creates a separate profile with a new ID. The source is loaded by ID from the URL, so refresh preserves the draft source and a missing source prevents saving with an actionable error.

The profile list refreshes retained workflow statuses every five seconds while mounted. It derives each profile's active/inactive state from its associated microphone workflow (`queued`, `running`, `recording`, or `stopping`) by `profile_id`, independent of model/runtime selection. A profile has at most one active workflow in a backend runtime. An active profile offers Stop; after it becomes terminal, it offers Play again. The overview profile card shows the same state and can stop its active workflow. A failed status refresh preserves the last known state and disables starting until status can be loaded again.

### `GET /models`

Return `{ "default_model": "turbo", "default_runtime": "openvino-gpu", "models": [...] }` through the workflow service. Each catalog record exposes `key`, `display_name`, `installed`, aggregate `ready`, and `runtimes`. Each runtime entry exposes Feature 01 runtime `key`, model `compatible` status, local `ready` status, `precision_options`, and an optional `reason`. Compatibility is independent from readiness: incompatible selections are rejected as invalid configuration, while a compatible but unavailable runtime can be saved and fails at workflow startup with HTTP 503 if it remains unavailable. This endpoint only discovers local/catalog models; it does not download or install weights.

### `GET /profiles/{profile_id}`, `PUT /profiles/{profile_id}`, and `DELETE /profiles/{profile_id}`

`GET` returns the profile representation described above. `PUT` replaces the full definition using the same request body as `POST`, preserves `profile_id` and `created_at`, updates `updated_at`, and returns the updated profile. A name matching another profile under the normalized comparison above returns HTTP 409; the profile being updated is excluded from the comparison. `DELETE` returns HTTP 204. Each operation returns HTTP 404 for an unknown profile ID. Profile changes do not change existing runs.

### `POST /profiles/{profile_id}/runs`

Start a microphone workflow using the selected profile. The route reads the profile once and copies its `device`, `execution_mode`, `keywords`, `silence_threshold`, `model`, and `runtime` into the run before starting the workflow. If that profile already has an active microphone workflow (`starting`, `queued`, `running`, `recording`, or `stopping`), return HTTP 202 with that existing workflow's `{ "workflow_id": "<id>", "status": "<current status>" }` and do not start another process. Concurrent start requests in the same backend runtime are serialized so they cannot create multiple active workflows for the profile. After the current workflow becomes terminal, a later request starts a new workflow. Feature 01 receives the saved value as `flow_config.silence_threshold`; the workflow service loads or reuses the selected model/runtime configuration. Both `shared` and `per_workflow_process` modes receive the selected runtime. Runtime-only CTranslate2 selection uses `int8` when precision has not been explicitly configured; an explicitly configured incompatible precision remains a configuration error. A startup or configuration failure uses the same HTTP errors as `POST /transcriptions/microphones`. An unknown profile returns HTTP 404. The run keeps this start-time snapshot if the profile is later edited or deleted; run snapshots are not updated by later profile edits.

### `POST /transcriptions/clips`

Accept `multipart/form-data` with a `.wav` upload in `file` and one or more repeated `keywords` fields. Keywords must be unique and non-empty; matching uses the Thai configuration and literal substring rules in the [matching workflow contract](../transcript-matching-forwarding/spec.md#matching-contract). Feature 01 accepts PCM signed 16-bit mono or stereo WAV at 8–48 kHz.

The route validates the request, copies the upload to a temporary WAV file in 1 MiB chunks, and closes the upload before queueing the independent run. The worker passes the temporary path to `transcribe_clip_and_forward` and deletes it on completion or failure. The workflow uses `forwarder=None` and `language="th"`. The route returns HTTP 202 without waiting for transcription:

```json
{"workflow_id":"<id>","status":"queued","first_queued_at":"<UTC timestamp>","last_response_at":null}
```

`first_queued_at` is set to the successful executor submission time. `last_response_at` is null until the worker has a transcription result or failure outcome. The worker records transcript, match results, completion, or workflow error events and exposes the final transcript, matches, and updated timestamps in status.

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
{"workflow_id":"<id>","status":"recording","first_queued_at":null,"last_response_at":null}
```

Both timestamp fields are included in direct and profile-start responses. They may be null when the response is accepted; later status JSON fills them as Feature 01 accepts the first non-silent audio chunk and returns inference responses. A profile-start request that reuses an existing active run returns that run's current timestamp values.

### `GET /microphones`

Return usable host input devices as `{ "devices": [...] }` through the workflow's `list_available_microphones()` interface, which wraps Feature 01's public device-listing callable. Each record has a stable `name`, `selectable`, `max_input_channels`, `default_samplerate`, and `is_default`. Device indexes are never exposed. Duplicate input names are returned with `selectable: false`; the Feature 01 exact-name selector also rejects ambiguous names. Missing `sounddevice` or PortAudio returns HTTP 503.

### `GET /transcriptions/{workflow_id}`

Return run `workflow_id`, `kind`, `status`, `keywords`, `profile_id`, `profile_name`, `device`, `execution_mode`, `silence_threshold`, `model`, `runtime`, `transcript`, `matches`, `latest_rtf`, `first_queued_at`, `last_response_at`, `error`, and `created_at`. `latest_rtf` is the numeric RTF from the most recently completed inference measurement, or null until the first inferred chunk; it updates as clip or microphone measurements arrive. Silent chunks skipped before inference do not update it. `first_queued_at` and `last_response_at` are nullable UTC ISO 8601 timestamps formatted with a `Z` suffix. For clip runs, `first_queued_at` is set when the worker is successfully submitted to the executor. For microphone runs, Feature 01 sets it when the first accepted non-silent chunk reaches its inference work queue; runs with no accepted audio keep it null. `last_response_at` is set when the latest inference response is available for microphone runs and when the transcription operation returns for clip runs. For a clip operation that fails before returning a transcript, it is set when the failure outcome becomes available. A microphone workflow failure before any inference response leaves it null. `profile_id` and `profile_name` are null for clip runs and direct microphone runs that did not start from a profile. `device` and `execution_mode` describe microphone runs and are null for clip runs. `silence_threshold` is the saved start-time value for profile runs and null for other runs. `model` is the effective model key for microphone runs and null for clip runs. Status progresses through `queued`/`running`/`completed`/`failed` for clips and `recording`/`stopping`/`stopped`/`completed`/`failed` for microphone sessions. Unknown IDs return HTTP 404.

### `GET /transcriptions`

Return `{ "transcriptions": [...] }` containing the same status representation, including `latest_rtf`, `first_queued_at`, and `last_response_at`, for every run currently retained by the in-memory registry, ordered by `created_at` descending and then `workflow_id` descending. This is a live view of the current backend process; it does not include runs from other server workers or runs lost at restart. Registry eviction applies equally to this list and individual status/event lookups.

### `GET /transcriptions/{workflow_id}/events?after=<cursor>`

Return `{ "workflow_id": ..., "events": [...], "next_cursor": n }`. Events are buffered per workflow ID, receive monotonically increasing integer cursors starting at 1, and are not consumed by reads. When an event is appended, the backend adds a `timestamp` in UTC ISO 8601 format with a `Z` suffix unless the event already has an explicit `timestamp`. Send `after=0` for the first page, then use `next_cursor` for the next request. Each run retains at most 1000 events. The registry retains at most 256 runs; when full, admitting a new run evicts the oldest terminal run. Active runs are never evicted. If all slots are active, creation returns HTTP 503. An evicted workflow ID returns HTTP 404. When `after` is older than the retained history, return HTTP 410 with the oldest available cursor; clients should resume from that cursor minus one and account for the missing gap. A cursor ahead of the latest event returns HTTP 422. Unknown workflow IDs return HTTP 404.

The Run Detail Events timeline displays the appended event timestamp, including for `started` and `completed` lifecycle events. For `audio_queued` and `measurement` events, it displays `first_queued_at` and `completed_at`, respectively, when available.

### `POST /transcriptions/{workflow_id}/stop`

Request asynchronous stop for a microphone workflow and return HTTP 202 with its current status. A clip workflow cannot be stopped and returns HTTP 409. Unknown workflow IDs return HTTP 404.

### `POST /stress-tests`

Start the fixed file-replay stress matrix through the public `run_file_replay_stress()` workflow and return HTTP 202 with `{ "stress_test_id": "<id>", "status": "queued" }`. Accept an optional strict JSON body containing `model`, `runtime`, and `precision`; omitted values use the workflow service's effective model configuration, which follows Feature 01 defaults when no overrides are configured. The server always selects its packaged `audio/test-audio.wav` input and does not accept a path or upload from the caller. Unknown fields, invalid model/runtime/precision combinations, or invalid values return HTTP 422. A stress matrix is resource intensive: only one may be queued or running per backend process. Starting another returns HTTP 409. Starting while a microphone workflow is active also returns HTTP 409 to avoid mixing microphone load into capacity evidence. Starting a microphone workflow while a stress matrix is queued or running returns HTTP 409. Stress and microphone starts are serialized by the process-local registry so concurrent start requests cannot both pass the conflict check. A compatible runtime that is unavailable on the host is accepted by request validation; the asynchronous result records affected trials as unavailable.

### `GET /stress-tests/{stress_test_id}`

Return `stress_test_id`, `status` (`queued`, `running`, `completed`, or `failed`), `model`, `runtime`, `precision`, nullable `report`, nullable `error`, and `created_at`. The completed `report` contains the two topology reports and their 1/2/4-workflow trial outcomes from Feature 01. A failed API/workflow execution has status `failed` and an error message. Unknown IDs return HTTP 404.

### `GET /stress-tests/{stress_test_id}/events?after=<cursor>`

Return `{ "stress_test_id": ..., "events": [...], "next_cursor": n }` using the same repeatable, bounded, cursor-addressable event behavior as transcription event streams. Forward workflow progress events (`trial_started`, `trial_progress`, `trial_completed`, `trial_unavailable`, and `matrix_completed`) with their topology and workflow count where applicable. Append terminal `completed` or `workflow_error` lifecycle events. Each stress run retains at most 1000 events, and the process-local registry retains at most 256 stress runs; when full, admitting a new run evicts the oldest terminal stress run. Active runs are never evicted; if all slots are active, creation returns HTTP 503. An `after` cursor older than retained history returns HTTP 410 with `oldest_cursor`; a cursor ahead of the latest event returns HTTP 422. Unknown or evicted IDs return HTTP 404.

## Validation and errors

Invalid input, duplicate or empty keywords, unsupported workflow configuration, and unselectable microphone names return HTTP 422. Missing microphone dependencies, model runtime failures, a full run registry, or reaching the per-process limit of 16 active microphone workflows return HTTP 503. Clip audio decode errors occur in the queued worker and appear as a failed status and `workflow_error` event because the API has already returned 202. No outbound forwarding is configured by this API; workflows do not emit fake `forwarded` events when `forwarder=None`.

## Lifecycle and limits

In `shared` mode, the workflow service caches a `ModelHandle` per effective model configuration while at least one active clip operation or microphone workflow uses it. Concurrent clips and microphone workflows using the same configuration share that handle. Each operation or workflow holds a thread-safe lease from before model use until the clip returns or the microphone workflow reaches its terminal completion, including inference already in flight. When the last lease is released, the service removes the handle from the cache and closes it; a later operation loads a fresh handle. Distinct effective configurations have independent handles and leases. In `per_workflow_process` mode, each microphone workflow owns a spawned process that loads and closes its selected model in the capture process cleanup path on normal completion, stop, or failure; the application process owns each process group and relays its events. A profile cannot own more than one active workflow within one backend runtime. Backend routes call only this workflow service; they do not call Feature 01 directly. A thread-safe in-process backend registry isolates each workflow's lifecycle and bounded event buffer, and serializes profile starts. Terminal runs are retained until capacity pressure evicts them; active runs are retained. At most 16 microphone workflows may be active per backend process across both execution modes. Registry state is process-local and non-durable: restart loses runs and events, and multiple server worker processes have independent registries and model handles. Deployments requiring cross-process monitoring or durable history need shared state outside this feature's scope.

The SQLite profile store is separate from the in-memory run registry. Profiles survive restart and are local to the configured database file; each server process may open the same SQLite file. SQLite stores profile definitions only, not workflow status, transcripts, matches, or events. Starting from a profile copies its definition into the run, so the run remains independently monitorable through the existing status, events, and stop endpoints.

## Non-goals

- Persisting transcription runs, transcripts, matches, or event history.
- Synchronizing in-memory runs across backend worker processes or coordinating microphone/model limits across processes.
- Associating a run with a live profile after startup, or editing a running workflow by changing its profile.
- Adding clip uploads to profiles; profiles configure microphone workflows only.
- Adding authentication, profile ownership, or remote profile synchronization.

Run with the optional backend dependencies:

```bash
uv sync --locked --extra backend --extra microphone --extra openvino --extra ctranslate2
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
- Individual and list status responses expose nullable `latest_rtf`, updated as per-chunk measurements arrive for clips and microphone workflows.
- Individual and list status responses expose nullable UTC `first_queued_at` and `last_response_at` values for clip and microphone runs, including profile-started runs in both execution modes.
- The stress-test API starts the fixed file-replay matrix asynchronously, exposes progress and completed topology reports, validates optional model overrides, and rejects concurrent stress matrices or active microphone sessions.
- Profile persistence does not make runs, transcripts, matches, or events durable.
- The backend can be launched by uvicorn using the documented optional dependencies.
