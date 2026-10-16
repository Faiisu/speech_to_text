[← Back to README](../README.md)

# API Interfaces

Feature and workflow capabilities are available as Python callables. The previous Control Center frontend and server have been removed. The React frontend calls the backend through its inbound HTTP API; callable feature and workflow interfaces remain available to Python callers as well.

## Feature 01: model deployment

The public interface provides model discovery, model loading, finite clip transcription, and microphone sessions. See the [Feature 01 callable contract](../.scratch/new-speech-to-text/spec.md#callable-interface) for inputs, outputs, configuration, errors, session events, and process deployment.

```python
from speech_to_text.features.model_deployment import load_model, transcribe_clip

model = load_model({"model": "turbo", "runtime": "openvino-gpu"})
try:
    transcript = transcribe_clip("sample.wav", model, {"language": "th"})
finally:
    model.close()
```

## Word matching and forwarding

The matching feature and its output behavior are composed by the [transcript matching workflow](../.scratch/transcript-matching-forwarding/spec.md). The workflow returns results to its backend caller and can also send completed results to a configured receiver.

## IPCAM RTSP input

Live audio workflows also accept IP camera/NVR audio through RTSP. The [RTSP source contract](../.scratch/ipcam-rtsp/spec.md) owns the capture callable, source fields, secret references, profile persistence, and direct `POST /api/v1/transcriptions/rtsp` request. RTSP runs use the existing status, event polling, stop, matching, forwarding, and recovery interfaces. See [RTSP configuration](configuration.md#ipcam-audio-through-rtsp) for camera and credential setup.

## Backend API v1

The [model tuning contract](../.scratch/model-tuning/spec.md) documents retained audio uploads, host recording, playback/download, and deletion. Uploading audio to that library does not start a transcription or load a model; tuning trials use the existing clip transcription endpoint.

Install the backend extra and run the app with `uv run --no-sync python -m uvicorn speech_to_text.backend.app:app`. The [backend transcription API specification](../.scratch/backend-transcription-api/spec.md) is authoritative for clip uploads, microphone sessions, device discovery, status, buffered events, stop behavior, and the asynchronous file-replay stress-test API. Routes call workflow interfaces; workflow events are buffered per run for independent polling.

Microphone requests can select `shared` or `per_workflow_process` model execution; omitted mode keeps the shared-model default. The API process owns the run registry in either mode. Run state is in-memory and non-durable; restarting the server loses it, and multiple backend worker processes do not share status or event history. See the [backend API specification](../.scratch/backend-transcription-api/spec.md) for the request contract and lifecycle.

Reusable microphone profiles can be created, listed, replaced, deleted, and started through the backend API. Profiles persist in SQLite; the endpoint for listing transcriptions exposes only runs still retained in the current backend process. `SPEECH_TO_TEXT_PROFILE_DB` selects the profile database file. The API specification remains the source of truth for profile and run response shapes.

API callers can configure saved HTTP/HTTPS destinations and send synthetic test JSON. Profiles, direct microphone requests, and clip uploads can select a saved destination; status and events report delivery independently from transcription. See the [destination endpoints](../.scratch/backend-transcription-api/spec.md#destination-endpoints) for the authoritative access rules and request/response contract.

## See also

- [Architecture](architecture.md) for module ownership.
- [Configuration](configuration.md) for optional runtime and matching dependencies.
