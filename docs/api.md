[← Back to README](../README.md)

# API Reference

The Control Center HTTP API adapts requests to the callable feature modules. It binds to loopback by default and currently rejects non-loopback bind addresses. The feature function contract is defined in the [Feature 01 specification](../.scratch/new-speech-to-text/spec.md#callable-interface); the control-center route design is recorded in the [control center specification](../.scratch/feature-test-console/spec.md#local-service-api-and-run-command).

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
| `POST /models` | Load a model; JSON fields include `model`, `runtime`, `precision`, `queue_capacity`, and `enqueue_timeout_seconds`. |
| `DELETE /models/{handle_id}` | Stop flows owned by the handle and close it. |
| `POST /clips` | Transcribe an uploaded WAV using multipart form data. |
| `POST /microphones` | Start a microphone session. |
| `POST /process-groups` | Start shared-model or per-input-model microphone processes. |
| `POST /capacity` | Measure real microphone capacity and return the recorded verdict and measurements. |
| `GET /events/{source_id}` | Stream transcript, error, and completion events as Server-Sent Events. |
| `POST /sessions/{source_id}/stop` | Stop one microphone session and flush accepted audio. |
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

The result contains the transcript, elapsed time, generated source ID, and model configuration. Feature 01 accepts WAV paths or bytes at its Python callable boundary; the HTTP adapter accepts an uploaded WAV.

### Start and stop a microphone session

```bash
curl -sS -X POST "$BASE_URL/api/features/feature-01-model-deployment/microphones" \
  -H 'Content-Type: application/json' \
  -d '{"handle_id":"MODEL_HANDLE_ID","device":null,"flow_config":{"language":"th","chunk_seconds":5,"silence_threshold":0.05}}'
```

Omit `device` or pass `null` to use the OS-selected input. The response returns a `source_id`. Subscribe to the event stream and stop the session with:

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

## Database query for per-chunk RTF

In Grafana's PostgreSQL query editor, use `$__timeFilter(recorded_at)` so the query follows the dashboard time range:

```sql
SELECT
    recorded_at AS "time",
    feature_id,
    operation,
    pid,
    source_id,
    sequence,
    audio_seconds,
    inference_seconds,
    rtf,
    status
FROM operation_measurements
WHERE $__timeFilter(recorded_at)
  AND feature_id = 'feature-01-model-deployment'
ORDER BY recorded_at DESC
LIMIT 50;
```

RTF is recorded per inferred chunk as `inference_seconds / audio_seconds`; `rtf <= 1.0` means that chunk completed within its audio duration. See [System Observability](../.scratch/system-observability/spec.md) for the schema and dashboard filters.

## See also

- [Getting Started](getting-started.md) to start the service.
- [Configuration](configuration.md) for request settings and defaults.
- [Architecture](architecture.md) for the callable feature boundaries.
- [Deployment](deployment.md) for database and Grafana setup.
