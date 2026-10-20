# Model Tuning

## Purpose

Provide a browser workflow for capturing short host microphone or IP camera audio examples and comparing transcription settings against saved audio. Live audio is captured by the backend host, not by the browser.

## Recording contract

Feature 01 exposes `start_audio_recording(device=None, output_path=..., *, max_seconds=120, audio_source_factory=None)`. It returns a session with `snapshot()`, idempotent `stop()`, and `wait()` for terminal completion. The workflow supplies the generated storage path; the optional audio-source factory selects the capture boundary, including production RTSP sources and deterministic test sources. Invalid capture configuration raises `ValueError`; unavailable capture raises `AudioInputError` or a storage error. The workflow translates availability failures into its public `RecordingUnavailableError` and conflicting operations into `RecordingConflictError` for API callers.

The backend records at most one capture at a time, for at most 120 seconds. Capture retains silence and stores a mono PCM16 WAV, with stereo input downmixed to mono. A capture can be stopped early. Input overflow is reported as a warning; fatal capture or storage errors fail the recording while preserving any audio already written as a valid WAV when possible. Recording does not load a model.

Browser users can also import an audio file from their computer. `POST /recordings/upload` accepts one multipart `file` up to 20 MiB. WAV input must be complete PCM16 mono or stereo at 8–48 kHz and at most 120 seconds; accepted WAV bytes are retained unchanged. Audio-only MP3 and M4A (AAC or ALAC) files are decoded into mono PCM16 WAV at 16 kHz, with a maximum decoded duration of 120 seconds. The original display filename is sanitized and retained as metadata; storage names always use server-generated IDs. Imported recordings do not reserve or change the active microphone capture. Invalid/unsupported audio returns 422, an oversized upload returns 413, and unavailable storage or compressed-audio decoding returns 503.

Feature 01 exposes `validate_wav(path, *, max_seconds=120) -> float` and `convert_audio_to_wav(input_path, output_path, *, max_seconds=120) -> float`; each returns the audio duration in seconds. Validation reads frames in bounded blocks, and conversion streams decoded frames to WAV while enforcing the duration limit. Invalid audio raises `AudioInputError`, missing PyAV raises `AudioCodecUnavailableError`, and storage failures propagate as `OSError` for the workflow to translate. Compressed input uses a fixed local-file demuxer and cannot select playlists or remote media. The backend extra includes PyAV, so OpenVINO deployments support imports without a CTranslate2 runtime or a separate FFmpeg executable.

The API generates opaque recording IDs and derives all audio and metadata paths from those IDs. It never accepts filesystem paths; imported files are written under server-generated paths. Recordings and metadata survive backend restart in the configured storage directory. Default storage is `data/tuning-recordings`; `SPEECH_TO_TEXT_RECORDINGS_DIR` overrides it. Only completed and failed recordings are restored at startup. A finalized recording can be deleted by ID.

`RecordingService.copy_audio_snapshot(recording_id, destination_path)` is the workflow's public boundary for consumers that need an independent finite-audio copy. It returns `(recording_metadata, duration_seconds)` for a finalized, non-empty usable WAV, or `None` when the ID or audio file is unavailable. It raises `RecordingConflictError` while capture is active, `RecordingInvalidAudioError` for unusable or over-duration WAV audio, and `RecordingUnavailableError` for storage failures. The copy is made under the recording-library lock so concurrent deletion cannot interrupt it; the caller owns and removes the destination file.

Routes are mounted under `/api/v1`:

| Method and route | Behavior |
| --- | --- |
| `POST /recordings` | Accept a live source definition as described below; start bounded capture and return its snapshot. |
| `POST /recordings/upload` | Accept multipart field `file`; validate/import WAV, MP3, or M4A and return its snapshot. |
| `GET /recordings` | Return `{ "recordings": [...] }`, newest first. |
| `GET /recordings/{id}` | Return the recording snapshot. |
| `POST /recordings/{id}/stop` | Stop active capture and return its latest snapshot. |
| `GET /recordings/{id}/audio` | Download finalized WAV; return 409 while capture is active. |
| `DELETE /recordings/{id}` | Delete a finalized WAV and its metadata; return 409 while active. |

Snapshots contain `recording_id`, `status` (`recording`, `completed`, or `failed`), `duration_seconds`, `device`, `error`, `warnings`, `source` (`microphone`, `rtsp`, or `upload`), nullable display-only `filename`, and UTC `created_at`. RTSP recordings also retain their non-secret source fields as described below. Older saved manifests without `source` or `filename` restore as microphone recordings with no filename. Unknown IDs return 404. A second active capture returns 409. Invalid input returns 422; oversized uploads return 413; unavailable capture, storage, or compressed-audio decoding returns 503. Audio downloads are WAV: uploaded WAV bytes remain unchanged, while MP3/M4A downloads contain the normalized WAV.

Finalized recording snapshots also include `sample_rate` and `channels` when their WAV header is readable. These values are read from the saved WAV, including for older manifests, so callers can validate frequency bounds against the actual audio. They are omitted for active captures or unreadable headers.

### IPCAM recording

`RecordingService.start(device=None, *, source_type="microphone", rtsp_url=None, rtsp_url_secret_ref=None, rtsp_username=None, rtsp_password=None)` selects the live capture source. `POST /recordings` accepts these same fields. The [frontend credential contract](../ipcam-rtsp/spec.md#frontend-camera-credentials) owns direct username/password and pasted URL normalization; passwords remain transient during recording. Existing `{ "device": string|null }` requests retain microphone capture. `source_type` accepts `microphone` or `rtsp`; reject unknown fields, invalid types, microphone definitions containing RTSP fields, RTSP definitions with a non-null microphone device, and RTSP definitions without exactly one URL/reference.

For RTSP, reuse Feature 01's exported source factories. URL validation, safe errors, network timeouts, normalized PCM, and deployment-managed secret references remain owned by the [RTSP source contract](../ipcam-rtsp/spec.md). The audio-recording workflow calls the feature interface directly and translates invalid source configuration to a public `RecordingConfigurationError` (HTTP 422), capture/storage failures to `RecordingUnavailableError` (HTTP 503), and active capture conflicts to `RecordingConflictError` (HTTP 409). Resolve secrets once at capture start. Never serialize credential-bearing URLs, passwords, or source factories into recording metadata, responses, or error messages. A non-secret username and password-presence flag may identify authenticated samples. Initial startup failure cleans up newly allocated recording files and leaves no active capture or library entry.

For example:

```json
{"source_type":"rtsp","device":null,"rtsp_url":"rtsp://192.168.1.50:554/stream1"}
```

For an authenticated camera, enter Username/Password with the direct URL, paste a complete credential URL, or use `rtsp_url_secret_ref` containing an approved environment variable name. Recordings use `source: "rtsp"`, `device: null`, and a sanitized source definition. Existing microphone/upload manifests and responses remain compatible, and RTSP recordings retain their source identity after backend restart. RTSP capture uses the same global one-recording limit, silence retention, maximum 120 seconds, manual stop, playback/download/delete, and trial operations as microphone recordings. Recording itself never loads a model or starts a transcription workflow. A disconnect fails the finite recording while retaining any valid partial WAV; long-running transcription run reconnection does not apply to finite tuning samples.

The Model tuning capture panel provides an accessible Input source selector with Microphone and IPCAM (RTSP), plus direct URL/environment reference controls matching the Profile editor's terminology. Submit only the selected source's fields. Microphone discovery failure must not block RTSP capture or uploads. Controls cannot change the active capture's configuration. Saved recording choices, details, and trial labels identify IPCAM samples without exposing secrets. A saved camera recording can be replayed through the existing tuning trials repeatedly, without reconnecting to the camera.

## Tuning workflow

The microphone capture panel also exposes the existing bounded no-model [microphone level diagnostic](../live-audio/spec.md). It uses the selected host device (or system default), polls transient RMS/peak and capture details, supports stop/retry, and closes an active test when leaving Model tuning. Diagnostic capture and recording cannot run at the same time; device discovery can be refreshed, and ambiguous devices remain visible but unavailable. The diagnostic does not create a saved recording or a transcription trial.

Language is selected per trial and retained with the trial's immutable configuration snapshot. The [backend language configuration contract](../backend-transcription-api/spec.md) owns supported choices, defaults, and the meaning of automatic language detection; this page does not identify detected languages or promise code-switching accuracy.

The user can select a discovered host input device or configure an IPCAM RTSP source and record, or import a WAV, MP3, or M4A file; they can listen to or download the saved WAV and rerun it through the existing clip transcription operation with selected model/runtime/decoding options and keywords. Each trial shows transcript, keyword matches, latest chunk RTF, cumulative minimum/average/maximum RTF from backend run status, and an ordered RTF measurement row for every chunk that reached inference, including actual duration for a shorter final chunk. Measurement rows show queue wait, inference time, status, and any error. Failed and null RTF values remain visible without being replaced by zero; when no chunk reached inference, the trial says so. The page keeps its comparison history for the current browser page session; transcription status and event history follow the existing backend run registry's retention rules. The saved recording remains unchanged across reruns. Model loading remains owned by the existing transcription workflow.

## Boundaries

The [clip API contract](../backend-transcription-api/spec.md#post-transcriptionsclips) owns per-run settings validation. Trial cards retain their recording identity and configuration snapshot while the page remains open. Trials run one at a time from the page and do not select an output destination. Production microphone workflows are not stopped by recording or tuning controls.

- `features/model_deployment/recording.py` owns bounded host capture and WAV encoding.
- `workflows/audio_recording/` owns generated IDs, persistent metadata and file lifecycle.
- Backend routes own HTTP validation and translate service outcomes.
- The model-tuning frontend owns recording, microphone diagnostic, and rerun interactions.
- Recordings are local runtime data, not model artifacts or packaged audio fixtures.

## Acceptance

- Capture streams to disk with bounded memory, retains silence, produces readable mono PCM16 WAV, and auto-stops at the configured maximum.
- Stop, recoverable overflow, fatal input error, writer failure, and backend shutdown have explicit status and preserve written audio where possible.
- Capture can be tested with an injected source and does not load a model. WAV/MP3/M4A import validates and stores audio without opening a microphone or loading a model.
- Generated IDs cannot be used to access arbitrary filesystem paths.
- Finalized recordings and metadata are discoverable after restart; active recordings are not restored as active.
- One recording at a time is allowed; active WAV download and deletion return conflict.
- Browser users can diagnose microphone signal from the capture panel, stop on navigation, and cannot overlap a diagnostic with a recording. They can listen to/download a saved recording, rerun it with configurable transcription options, and compare transcript, keyword matches, latest RTF, and every inferred chunk's timing, status, and RTF; all-silence trials show that no chunk reached inference.
- Browser users can import WAV, MP3, and M4A files up to 20 MiB; malformed, unsupported, empty, truncated, or over-duration audio is rejected without leaving files or metadata behind.
- RTSP capture preserves its source metadata across restart, uses the production capture seam, and supports safe startup failure cleanup and partial audio preservation after disconnect. Invalid/mixed source fields and unavailable/disallowed secret references are rejected before opening capture; resolved credentials are absent from responses, errors, and persisted manifests.
- Browser coverage records/stops an IPCAM clip with unavailable microphone discovery, verifies a downloadable WAV, replays the saved clip in a tuning trial, and covers secret-reference selection. Existing microphone and upload scenarios continue to pass. Actual camera codec/authentication and target hardware performance remain separate proofs without supplied camera access.
