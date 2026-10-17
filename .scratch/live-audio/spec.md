# Live audio input test

## Purpose

Let an operator verify that a host microphone input device is connected and receiving sound. This is a diagnostic capture only: do not load a model, transcribe, match keywords, forward data, or save audio.

## Scope

- Add `/live-audio` and a **Microphone test** navigation item.
- Discover host microphone input devices through the existing device list. Allow the OS default input or a specific selectable device. Explain duplicate/ambiguous names as unavailable.
- Start a bounded input-test session from the frontend. The backend opens the selected microphone and reports transient signal measurements: device, sample rate, channel count, captured frame count, current RMS level, and peak level. Show a responsive live level meter and elapsed time.
- Stop the session from the page. The backend also ends a session automatically after a bounded duration and closes the device. Only one diagnostic session may be active per backend process.
- Retain at most the latest diagnostic status in process memory. Do not persist audio, measurements, or credentials. Do not share the device with the transcription workflow implicitly.
- Poll while the page is open, stop polling after completion/failure, and make API/start/device errors actionable.
- Responsive layout and accessible labels/status announcements.

## Out of scope

RTSP sources, profile editing, model loading, transcription, keyword matching, forwarding, audio recording/playback, and historical diagnostics. Live transcription remains in the existing profile and run workflow.

## Ownership

The backend diagnostic use case lives in `speech_to_text/workflows/audio_input_test/`; the microphone adapter remains owned by Feature 01's model-deployment feature. Routes live in `backend/api/routes/microphones.py`. Frontend implementation lives in `speech_to_text/frontend/src/features/live-audio/`, with shared navigation/routes in `src/shared/`.

The workflow defaults to a 30 second maximum duration and keeps one current session record. RMS and peak are recomputed from each incoming callback block while only aggregate counters/current levels and lifecycle metadata remain in memory. A new session replaces the prior terminal record. Backend shutdown stops and closes any active source. The source factory is exposed by Feature 01's callable interface without constructing or loading a model. HTTP request, response, status, and error mappings are specified by the [backend API contract](../backend-transcription-api/spec.md#microphone-input-test-endpoints).

## Acceptance

1. The page lists selectable host microphone inputs and the OS default, then starts and stops a diagnostic capture without invoking a model.
2. While running, signal measurements update and show when no audio frames arrive or the signal is silent.
3. Stop, device errors, a second concurrent start, backend timeout, and route navigation all leave the capture closed; session duration is bounded.
4. No WAV/audio file, transcript, keyword result, forwarded event, or model handle is created. Session state is process-local and bounded.
5. The page communicates input state clearly and remains usable on a narrow screen.

## Implementation record

Implemented the no-model microphone input test page, start/status/stop API, and bounded in-memory diagnostic workflow. The backend computes transient signal measurements only, closes the device on stop, at 30 seconds, and during shutdown. The frontend build and Python compile checks passed. Automated tests were not added or run for this request. No real microphone hardware test was performed.
