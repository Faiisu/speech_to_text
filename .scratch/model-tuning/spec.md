# Model Tuning

## Purpose

Provide a browser workflow for capturing short host microphone examples and comparing transcription settings against saved audio. Audio is captured by the backend host (the UBX-330M microphone in deployment), not by the browser.

## Recording contract

Feature 01 exposes `start_audio_recording(device=None, output_path=..., *, max_seconds=120, audio_source_factory=None)`. It returns a session with `snapshot()`, idempotent `stop()`, and `wait()` for terminal completion. The workflow supplies the generated storage path; the optional audio-source factory replaces only the host hardware boundary in tests. Invalid capture configuration raises `ValueError`; unavailable capture raises `AudioInputError` or a storage error. The workflow translates availability failures into its public `RecordingUnavailableError` and conflicting operations into `RecordingConflictError` for API callers.

The backend records at most one capture at a time, for at most 120 seconds. Capture retains silence and stores a mono PCM16 WAV, with stereo input downmixed to mono. A capture can be stopped early. Input overflow is reported as a warning; fatal capture or storage errors fail the recording while preserving any audio already written as a valid WAV when possible. Recording does not load a model.

The API generates opaque recording IDs and derives all audio and metadata paths from those IDs. It does not accept filesystem paths or uploads. Recordings and metadata survive backend restart in the configured storage directory. Default storage is `data/tuning-recordings`; `SPEECH_TO_TEXT_RECORDINGS_DIR` overrides it. Only completed and failed recordings are restored at startup. A finalized recording can be deleted by ID.

Routes are mounted under `/api/v1`:

| Method and route | Behavior |
| --- | --- |
| `POST /recordings` | Accept `{ "device": string|null }`; start host capture and return its snapshot. |
| `GET /recordings` | Return `{ "recordings": [...] }`, newest first. |
| `GET /recordings/{id}` | Return the recording snapshot. |
| `POST /recordings/{id}/stop` | Stop active capture and return its latest snapshot. |
| `GET /recordings/{id}/audio` | Download finalized WAV; return 409 while capture is active. |
| `DELETE /recordings/{id}` | Delete a finalized WAV and its metadata; return 409 while active. |

Snapshots contain `recording_id`, `status` (`recording`, `completed`, or `failed`), `duration_seconds`, `device`, `error`, `warnings`, and UTC `created_at`. Unknown IDs return 404. A second active capture returns 409. Invalid input returns 422; unavailable capture or storage returns 503.

## Tuning workflow

Language is selected per trial and retained with the trial's immutable configuration snapshot. The [backend language configuration contract](../backend-transcription-api/spec.md) owns supported choices, defaults, and the meaning of automatic language detection; this page does not identify detected languages or promise code-switching accuracy.

The user can select a discovered host input device, record, listen to or download the saved WAV, then rerun that WAV through the existing clip transcription operation with selected model/runtime/decoding options and keywords. Each run shows transcript, keyword matches, and real-time factor (RTF), including the last reported chunk RTF for a completed run. The page keeps its comparison history for the current browser page session; transcription status and event history follow the existing backend run registry's retention rules. The original recording remains unchanged across reruns. Model loading remains owned by the existing transcription workflow.

## Boundaries

The [clip API contract](../backend-transcription-api/spec.md#post-transcriptionsclips) owns per-run settings validation. Trial cards retain their recording identity and configuration snapshot while the page remains open. Trials run one at a time from the page and do not select an output destination. Production microphone workflows are not stopped by recording or tuning controls.

- `features/model_deployment/recording.py` owns bounded host capture and WAV encoding.
- `workflows/audio_recording/` owns generated IDs, persistent metadata and file lifecycle.
- Backend routes own HTTP validation and translate service outcomes.
- The model-tuning frontend owns recording and rerun interactions.
- Recordings are local runtime data, not model artifacts or packaged audio fixtures.

## Acceptance

- Capture streams to disk with bounded memory, retains silence, produces readable mono PCM16 WAV, and auto-stops at the configured maximum.
- Stop, recoverable overflow, fatal input error, writer failure, and backend shutdown have explicit status and preserve written audio where possible.
- Capture can be tested with an injected source and does not load a model.
- Generated IDs cannot be used to access arbitrary filesystem paths.
- Finalized recordings and metadata are discoverable after restart; active recordings are not restored as active.
- One recording at a time is allowed; active WAV download and deletion return conflict.
- Browser users can listen to/download a saved recording, rerun it with configurable transcription options, and compare transcript, keyword matches, and RTF.
