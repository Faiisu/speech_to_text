# IPCAM audio through RTSP

## Scope

Add live IP camera/NVR audio to the existing transcription, keyword matching, and forwarding workflow. Implement the backend first, then integrate source selection into the existing frontend Profile editor, list, dashboard, and run detail where input identity is shown. Capture audio only. Automatic camera discovery and video processing are outside this feature.

## Source and callable contract

- Source type is `microphone` (default, including old profiles) or `rtsp`.
- An RTSP source selects exactly one of `rtsp_url` or `rtsp_url_secret_ref`. Direct URL requests may supply credentials using the frontend credential contract below. Public/stored source URLs are normalized to remove userinfo. URLs must use `rtsp://` or `rtsps://`, include a valid host/port, and contain no fragments or control characters. Environment references remain available for complete secret URLs, including secret query tokens.
- Secret references must be valid environment variable names explicitly allowed by comma-separated `SPEECH_TO_TEXT_RTSP_SECRET_REFS`. Missing/disallowed references fail with a safe configuration error. Resolve the value once when starting a run; recovery uses the same snapshot.
- RTSP sources have no microphone `device`. Reject conflicting source fields rather than silently ignoring them.
- Add a callable RTSP capture adapter under the model-deployment feature and export its supported configuration/factory boundary. It uses the installed PyAV dependency, selects the first audio stream, decodes and resamples to 16 kHz mono float32 PCM, and feeds the existing audio-source interface. Video is not decoded.
- Use RTSP over TCP by default, bounded network open/read waits (5 seconds each by default, configurable through the callable source configuration), and deterministic stop/cleanup. Decoding failures, a stream without audio, and end-of-stream are safe capture errors. Do not expose decoder exception text or resolved URLs in application errors, events, representations, or API responses.
- Support both existing shared-model and per-workflow-process execution. Factories passed to spawned processes must be picklable.
- Reuse existing chunking, overload policy, transcription/matching/forwarding, and backend recovery. Explicit stop and shutdown cancel reconnection.

The model-deployment package exports `create_rtsp_audio_source_factory(url, *, open_timeout_seconds=5.0, read_timeout_seconds=5.0)` and `create_rtsp_secret_source_factory(secret_ref, *, open_timeout_seconds=5.0, read_timeout_seconds=5.0)`. Both return a picklable `RTSPAudioSourceFactory`, whose resolved URL is excluded from its representation. Invalid URLs, references, and non-finite/non-positive timeout values raise `ConfigurationError`; omitted ports use the protocol default. A factory accepts the existing `device`, `on_audio`, and `on_error` keyword arguments and returns a source with `sample_rate=16000`, `channels=1`, `start()`, and `stop()`.

`start()` waits up to the configured open timeout plus one second for the capture worker to open the stream and select its audio track. Initial connection/no-audio failures raise safe `AudioInputError` messages. Subsequent decode failures or end-of-stream call `on_error`; the existing session/backend lifecycle owns termination and recovery. The worker owns the container and closes it on exit. `stop()` signals cancellation and joins the worker within the configured open plus read timeout plus one second, raising `AudioInputError` if it cannot stop. The optional PyAV import check raises `RTSPCaptureUnavailableError`, a subtype of `AudioInputError`.

The workflow exports `prepare_rtsp_source(rtsp_url=None, rtsp_url_secret_ref=None, ...)`, returning the validated factory and translating source configuration errors to `WorkflowConfigurationError`. The frontend credential extension below owns its optional credential inputs and normalization boundary. Pass the factory through `TranscriptionService.start_microphone(..., audio_source_factory=factory)` or `start_microphone_and_forward(..., audio_source_factory=factory)`. The service checks PyAV support before model loading and maps RTSP capture startup failures to `WorkflowUnavailableError`. Backend routes use this workflow interface.

## Backend API and persistence

- Add source fields to saved profile definitions, SQLite persistence, and read responses, with additive migration defaults preserving existing microphone profiles and IDs.
- Extend the existing live transcription start boundary to accept RTSP sources and expose a direct `POST /api/v1/transcriptions/rtsp` endpoint using the existing monitoring and stop APIs. Keep current microphone API callers compatible.
- Run snapshots identify `source_type`, the non-secret source definition, and retain the existing `kind: microphone` lifecycle classification for compatibility. Never serialize resolved environment values.
- Validate source configuration before loading a model or creating a capture connection. Return HTTP 422 for invalid configuration and safe actionable errors for unavailable capture support or a failed RTSP connection.

For example, start a camera directly with `POST /api/v1/transcriptions/rtsp`:

```json
{
  "keywords": ["สวัสดี"],
  "rtsp_url": "rtsp://192.168.1.50:554/stream1",
  "execution_mode": "shared",
  "language": "th"
}
```

For an authenticated camera, supply the write-only credential fields described below, paste its credential-bearing URL, or replace `rtsp_url` with `rtsp_url_secret_ref` naming an approved environment variable. Optional `destination_id` selects an existing forwarding destination. A successful start returns HTTP 202 with the existing workflow start response. Poll `GET /api/v1/transcriptions/{workflow_id}` or its `/events` endpoint; stop with `POST /api/v1/transcriptions/{workflow_id}/stop`.

Saved profiles use the same two RTSP fields and `source_type: "rtsp"`, with `device: null`, alongside the existing profile definition. Microphone profiles use `source_type: "microphone"` and null RTSP fields. Both source modes retain the same profile model/runtime, language, decoding, and destination settings.

## Frontend

- Profile editor provides an accessible Input source selector with Microphone and IPCAM (RTSP).
- Microphone selection retains its existing behavior. RTSP selection offers a direct URL with optional Username/Password fields, or an environment secret reference, and clears incompatible fields in submitted definitions.
- Create, edit, duplicate, start, monitor, and stop work for either source. Source identity appears correctly on profile cards, dashboard, and run details without showing resolved secrets. Old profiles lacking source fields render as microphones.
- Reuse the current UI and styling. Show validation and connection failures through existing error states.

## Acceptance and validation

1. Old microphone profiles and direct requests continue to work.
2. RTSP profiles round-trip through SQLite, including upgrade of an existing profile database.
3. Valid RTSP requests select the adapter in both execution modes; runtime settings and destinations retain their behavior.
4. Deterministic adapter tests prove audio-only decode/resampling, safe connection/no-audio/decode errors, and stop cleanup. Validate a real PyAV resampler with locally generated audio where possible.
5. Invalid URLs/source combinations and missing/disallowed secret references fail before model loading. Resolved credentials are absent from responses, errors, events, and reprs.
6. Recovery retains RTSP source identity and its resolved start-time configuration; stop prevents reconnecting.
7. Browser coverage proves RTSP create/edit/duplicate/start/monitor/stop while existing microphone scenarios pass. Frontend production build passes.
8. Physical camera connectivity, actual camera codecs, and target hardware throughput require a supplied camera and remain explicitly unverified without one.

## Documentation ownership

This document owns the RTSP source contract. Existing Feature 01 and backend API specifications link to it; configuration/deployment instructions remain in their existing `docs/` owners. Update `docs/project-structure.md` for the new source module and test ownership.

## Frontend camera credentials

Authenticated cameras can be configured entirely through the frontend, without environment setup. Both Profile and Model tuning direct URL controls offer optional **Username** and masked **Password** fields. Users may also paste a complete RTSP URL containing encoded credentials; normalize it into a credential-free URL plus separate credentials before persistence. Explicit credential fields and embedded userinfo must not ambiguously override each other. Separate credential fields accept raw password characters (including `@`, `:`, `/`, `%`, and `#`); percent-encode credentials exactly once when constructing the private capture URL. Pasted encoded userinfo is decoded once before reconstruction. Invalid URLs and conflicting authentication modes return safe errors without echoing input credentials.

### HTTP and workflow contract

- Profile definitions, direct RTSP start requests, and recording start requests accept optional write-only `rtsp_username` and `rtsp_password` fields when a direct `rtsp_url` is selected. Reject these fields for microphone sources and non-empty credentials combined with an environment reference. Existing unauthenticated URL and approved environment-reference callers retain their behavior.
- Add a feature-owned normalization/configuration boundary for parsing, validation, safe source metadata, and private URL construction; expose supported operations through the feature package. Both workflows call this feature boundary and keep their own public error translation. Backend routes continue calling workflow interfaces.
- Responses, run events, recording manifests, and callable representations must never contain a plaintext password or credential-bearing source URL. A non-secret username and a boolean `rtsp_has_password` may be returned; password fields themselves are absent from responses. Input-validation failures must not echo embedded credentials.
- Direct transcription/recording credentials are held only for that operation and recovery where applicable. A finite tuning recording persists only sanitized source identity, never the password. Browser passwords remain in component memory only, never local/session storage or URL query parameters.

### Saved profiles

- Persist saved profile passwords encrypted with the maintained `cryptography` Fernet implementation, using an additive private SQLite column. Do not build custom encryption. Include the dependency in the backend/test extras and update the lockfile.
- Generate the encryption key automatically beside the configured profile database, with owner-only access (0600), atomic creation, and reuse across backend restarts. Keep the key in the existing persistent profile directory/container volume; no additional environment variable is required. In-memory profile stores may keep an ephemeral key. Missing/corrupt keys for existing encrypted passwords fail safely rather than resetting credentials or starting a different audio source.
- Public profile serialization returns the sanitized URL, username, and password-presence flag only. A narrowly scoped private store interface supplies credentials for workflow startup. A run retains its resolved start-time source factory through recovery.
- Updating a profile without a password preserves its existing password only when its direct URL and username remain the same. An explicit empty password clears it. Switching to microphone/environment-reference sources clears saved direct credentials. Changing connection identity must not silently send an old password to a new address.
- Editing shows a blank masked password input with clear saved-password guidance. Duplicating a profile requiring authentication asks the user to re-enter the password; the read API cannot expose it to populate a copy. Explain this behavior in the duplicate editor.
- Back up the encryption key with the profile database. Document persistence ownership and key-loss behavior in the existing configuration/deployment docs.

### Acceptance

1. A raw password containing reserved URL characters and a pasted credential URL both connect through the existing source factory with correct escaping.
2. Authenticated Profile create/edit/restart/start/recovery and Model tuning record/stop/replay work without environment configuration. Browser coverage includes credential entry in both pages.
3. SQLite, WAL data, manifests, API responses/errors, and representations contain no plaintext password or embedded-credential URL. The saved profile decrypts after reopening with the same key, and a missing/corrupt key fails safely. Key permissions are owner-only.
4. Editing without replacing a saved password retains it under the identity rules; explicit clearing/source changes and duplication behave as documented.
5. Old microphone, unauthenticated RTSP, environment references, and tuning/upload behavior retain their contract. Run focused checks, the full Python suite, frontend build, and browser E2E.
