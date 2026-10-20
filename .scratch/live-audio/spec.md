# Microphone level diagnostic

## Purpose

Let an operator verify that a host microphone input device is connected and receiving sound from the Model tuning page. This is a diagnostic capture only: do not load a model, transcribe, match keywords, forward data, or save audio.

## Scope

- Discover host microphone input devices through the existing device list. Allow the OS default input or a specific selectable device. Explain duplicate/ambiguous names as unavailable.
- Start a bounded input-test session from Model tuning. The backend opens the selected microphone and reports transient signal measurements: device, sample rate, channel count, captured frame count, current RMS level, and peak level. Show a responsive live level meter and elapsed time beside microphone selection.
- Stop the session from the page. The backend also ends a session automatically after a bounded duration and closes the device. Only one diagnostic session may be active per backend process.
- Retain at most the latest diagnostic status in process memory. Do not persist audio, measurements, or credentials. Do not share the device with the transcription workflow implicitly.
- Poll while the page is open, stop polling after completion/failure, and make API/start/device errors actionable.
- Responsive layout and accessible labels/status announcements.

The old `/live-audio` URL redirects to `/model-tuning`; there is no separate microphone-test navigation item or frontend page.

## Out of scope

RTSP sources, profile editing, model loading, transcription, keyword matching, forwarding, audio recording/playback, and historical diagnostics. Live transcription remains in the existing profile and run workflow.

## Ownership

The backend diagnostic use case lives in `speech_to_text/workflows/audio_input_test/`; the microphone adapter remains owned by Feature 01's model-deployment feature. Routes live in `backend/api/routes/microphones.py`. The diagnostic UI belongs to the microphone capture section in `speech_to_text/frontend/src/features/model-tuning/`; shared navigation and the legacy URL redirect live in `src/shared/`.

The workflow defaults to a 30 second maximum duration and keeps one current session record. RMS and peak are recomputed from each incoming callback block while only aggregate counters/current levels and lifecycle metadata remain in memory. A new session replaces the prior terminal record. Backend shutdown stops and closes any active source. The source factory is exposed by Feature 01's callable interface without constructing or loading a model. HTTP request, response, status, and error mappings are specified by the [backend API contract](../backend-transcription-api/spec.md#microphone-input-test-endpoints).

## Acceptance

1. Model tuning lists selectable host microphone inputs and the OS default, then starts and stops a diagnostic capture without invoking a model. Ambiguous devices remain visible as unavailable, and device discovery can be retried.
2. While running, signal measurements update and show when no audio frames arrive or the signal is silent.
3. Stop, device errors, a second concurrent start, backend timeout, and route navigation all leave the capture closed; session duration is bounded.
4. No WAV/audio file, transcript, keyword result, forwarded event, or model handle is created. Session state is process-local and bounded.
5. The diagnostic communicates input state clearly and remains usable on a narrow screen. It cannot run alongside a recording, and leaving Model tuning stops an active diagnostic.

## Implementation record

Implemented the no-model microphone input diagnostic, start/status/stop API, and bounded in-memory workflow. The backend computes transient signal measurements only, closes the device on stop, at 30 seconds, and during shutdown. The diagnostic UI is integrated into Model tuning; the retired `/live-audio` URL redirects there. The frontend prevents simultaneous diagnostic and recording capture. No real microphone hardware test was performed.
