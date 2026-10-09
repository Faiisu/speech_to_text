[← Back to README](../README.md)

# API Reference

The Control Center HTTP API adapts requests to the callable feature modules. It binds to loopback by default and rejects non-loopback bind addresses. Its CORS middleware allows all origins, methods, and headers without credentials; the loopback bind remains in force. The feature function contract is defined in the [Feature 01 specification](../.scratch/new-speech-to-text/spec.md#callable-interface); the control-center route design is recorded in the [control center specification](../.scratch/feature-test-console/spec.md#local-service-api-and-run-command).

Set `BASE_URL` to `http://127.0.0.1:8765` in the examples below.

```bash
export BASE_URL=http://127.0.0.1:8765
```

## Shared endpoints

| Method and path | Purpose |
| --- | --- |
| `GET /` | Serve the browser control center. |
| `GET /api/features` | List registered feature pages and contract links. |
| `GET /api/system` | Report service readiness, feature count, loaded models, active sessions, and recent errors. |
| `GET /docs/features/{feature_id}` | Return the registered feature specification as Markdown. |

## Feature 01: model deployment

All routes use the prefix `/api/features/feature-01-model-deployment`.

| Method and path | Purpose |
| --- | --- |
| `GET /` | Feature readiness and currently loaded model handles. |
| `GET /catalog` | Rescan available models and runtime readiness. |
| `GET /devices` | List host audio input devices; reports the OS default input. |
| `GET /capture-capabilities` | Report whether a deployment enables the Mac host bridge. |
| `POST /models` | Load a model; JSON fields include `model`, `runtime`, `precision`, `queue_capacity`, and `enqueue_timeout_seconds`. |
| `DELETE /models/{handle_id}` | Stop flows owned by the handle and close it. |
| `POST /clips` | Transcribe an uploaded WAV using multipart form data. |
| `POST /microphones` | Start a microphone session. |
| `POST /process-groups` | Start shared-model or per-input-model microphone processes. |
| `POST /capacity` | Measure real microphone capacity and return the recorded verdict and measurements. |
| `GET /events/{source_id}` | Stream transcript, error, and completion events as Server-Sent Events. |
| `POST /sessions/{source_id}/stop` | Stop one microphone session and flush accepted audio. |
| `POST /sessions/{source_id}/audio` | In the enabled Mac profile, append one authenticated float32 PCM batch of at most one second to that session. |
| `POST /sessions/{source_id}/capture-error` | In the enabled Mac profile, end the authenticated capture session with a `CAPTURE_FAILED` event. |
| `POST /process-groups/{group_id}/stop` | Stop all flows and model workers in a process group. |

### Load a model

```bash
curl -sS -X POST "$BASE_URL/api/features/feature-01-model-deployment/models" \
  -H 'Content-Type: application/json' \
  -d '{"model":"turbo","runtime":"openvino-gpu","precision":"source"}'
```

The response includes a `handle_id`; pass it to clip and microphone routes. Loading may fail if the selected model, runtime, device, or required weights are unavailable.

### Transcribe a WAV clip

```bash
curl -sS -X POST "$BASE_URL/api/features/feature-01-model-deployment/clips" \
  -F 'file=@sample.wav' \
  -F 'handle_id=MODEL_HANDLE_ID' \
  -F 'language=th' \
  -F 'chunk_seconds=5' \
  -F 'silence_threshold=0.05'
```

The result contains the transcript, elapsed time, generated source ID, model configuration, and a `measurements` array with one record per chunk that reached inference. A measurement has `type: "measurement"`, `feature_id`, `operation`, `pid`, the UTC `completed_at` timestamp, `source_id`, `sequence`, `elapsed_seconds`, `audio_seconds`, `inference_seconds`, `rtf`, `status`, and an optional `error`. `rtf` is inference time divided by chunk audio duration; a value at or below `1.0` met real-time pace for that chunk. Silent chunks skipped before inference have no measurement. Feature 01 accepts WAV paths or bytes at its Python callable boundary; the HTTP adapter accepts an uploaded WAV.

### Start and stop a microphone session

```bash
curl -sS -X POST "$BASE_URL/api/features/feature-01-model-deployment/microphones" \
  -H 'Content-Type: application/json' \
  -d '{"handle_id":"MODEL_HANDLE_ID","device":null,"flow_config":{"language":"th","chunk_seconds":5,"silence_threshold":0.05}}'
```

Omit `device` or pass `null` to use the OS-selected input. The response returns a `source_id`. Subscribe to the event stream and stop the session with:

The microphone event stream includes a `type: "measurement"` event for every inferred chunk before completion. Its JSON data carries the same per-chunk fields as clip measurements, including the UTC `completed_at` timestamp. Transcript, error, and terminal completion events remain separate. Process-group SSE streams also carry one measurement event per inferred chunk with process and source attribution. The [Feature 01 specification](../.scratch/new-speech-to-text/spec.md#per-chunk-performance-measurement) defines the measurement contract.

The Mac Docker profile uses the host bridge from the Control Center page. The bridge creates this same session with `capture_mode: "host-bridge"` and a host-selected `sample_rate`; the response also contains a per-session `ingest_token`. The browser page keeps that token out of its requests and asks the loopback bridge to capture and forward audio. The bridge feeds batches through the same microphone session and drains them before calling stop. Process groups and capacity capture still require direct host access to microphones.

In one terminal, follow the live event stream:

```bash
curl -N "$BASE_URL/api/features/feature-01-model-deployment/events/SOURCE_ID"
```

In another terminal, stop the session:

```bash
curl -sS -X POST "$BASE_URL/api/features/feature-01-model-deployment/sessions/SOURCE_ID/stop"
```

### Start a process group

```bash
curl -sS -X POST "$BASE_URL/api/features/feature-01-model-deployment/process-groups" \
  -H 'Content-Type: application/json' \
  -d '{"devices":["Microphone A","Microphone B"],"topology":"shared-model","model_config":{"model":"turbo","runtime":"openvino-gpu","precision":"source"},"flow_config":{"chunk_seconds":5}}'
```

`topology` may be `shared-model` or `per-input-model`. The returned group ID is used at `/process-groups/{group_id}/stop`. For all payloads, accepted defaults, and typed session events, see the [Feature 01 specification](../.scratch/new-speech-to-text/spec.md).

## See also

- [Getting Started](getting-started.md) to start the service.
- [Configuration](configuration.md) for request settings and defaults.
- [Architecture](architecture.md) for the callable feature boundaries.
- [Deployment](deployment.md) for Linux and Mac service setup.
