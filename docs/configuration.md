[← Back to README](../README.md)

# Configuration

The [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults) is the source of truth for model and flow options, defaults, valid ranges, and runtime compatibility. Use this page for entry points, environment variables, and local command guidance.

## Model configuration

Pass model selection and shared-model queue settings to `load_model()` or `POST /api/features/feature-01-model-deployment/models`. See the [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults) for fields, defaults, validation, and runtime/precision compatibility.

Model, runtime, and precision are fixed for the lifetime of a loaded handle. Load a new handle to change them.

## Flow configuration

Pass flow settings to a clip or microphone flow; each input can have independent settings. See the [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults) for the complete option list and validation rules.

The HTTP clip adapter currently accepts `beam_size`, `temperature`, and `condition_on_previous_text` as form fields. The Python callable accepts all decoding options in the [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults).

## Environment variables

Copy [`.env.observability.example`](../.env.observability.example) to `.env` and replace its placeholder secrets before starting the observability stack. Keep `.env` out of version control.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SPEECH_TO_TEXT_MODELS_DIR` | `<current directory>/models` | Local converted/model files used by catalog discovery and runtime loading. |
| `SPEECH_TO_TEXT_MODEL_VOLUME_PATH` | `<current directory>/models` | Path whose disk usage is included in host samples. |
| `SPEECH_TO_TEXT_TELEMETRY_DATABASE_URL` | Unset | Writer connection URL. If unset, telemetry writer and sampler are disabled. |
| `SPEECH_TO_TEXT_TELEMETRY_QUEUE_CAPACITY` | `4096` | In-memory telemetry writer queue size. |
| `SPEECH_TO_TEXT_TELEMETRY_BATCH_SIZE` | `100` | Maximum records written per batch. |
| `SPEECH_TO_TEXT_TELEMETRY_SHUTDOWN_TIMEOUT_SECONDS` | `5` | Writer drain timeout during orderly shutdown. |
| `SPEECH_TO_TEXT_TELEMETRY_RETENTION_DAYS` | `30` | Database retention window applied by migrations. |
| `SPEECH_TO_TEXT_TELEMETRY_MIGRATION_DATABASE_URL` | Unset | Privileged connection URL used only to apply schema and retention migrations. |
| `GRAFANA_HOST` | `http://127.0.0.1:3000` | Grafana URL used by `scripts/observability_smoke.py`. |
| `TELEMETRY_ADMIN_USER` | `telemetry_admin` | Database bootstrap administrator account. |
| `TELEMETRY_ADMIN_PASSWORD` | Required by Compose | Database bootstrap administrator password. |
| `TELEMETRY_WRITER_PASSWORD` | Required by Compose | Password for the application insert-only role. |
| `TELEMETRY_GRAFANA_PASSWORD` | Required by Compose | Password for Grafana's read-only database role. |
| `TELEMETRY_DATABASE` | `speech_telemetry` | Database name. |
| `TELEMETRY_DATABASE_PORT` | `5433` | Host port for TimescaleDB; bound to loopback. |
| `GRAFANA_ADMIN_USER` | `admin` | Grafana UI administrator username. |
| `GRAFANA_ADMIN_PASSWORD` | Required by Compose | Grafana UI administrator password. |
| `GRAFANA_PORT` | `3000` | Host port for Grafana; bound to loopback. |
| `MAC_TEST_CONTROL_CENTER_PORT` | `18766` | Mac Docker Desktop test profile API port; bound to loopback. |
| `MAC_TEST_TELEMETRY_DATABASE_PORT` | `15433` | Mac Docker Desktop test profile database port; bound to loopback. |
| `MAC_TEST_GRAFANA_PORT` | `13001` | Mac Docker Desktop test profile Grafana port; bound to loopback. |
| `MAC_TEST_PLATFORM` | `linux/arm64` | Native Mac test image platform. Use `linux/amd64` only for Docker Desktop emulation fallback. |

See [Deployment](deployment.md#database-and-grafana) for how these settings are used.

## Container deployment settings

These values configure the Linux service defined in `compose.speech-service.yml`. The image uses Linux AMD64 for the Intel GPU target. The optional Mac Docker Desktop profile is documented in [Deployment](deployment.md#mac-docker-desktop-test-profile).

| Variable | Default | Purpose |
| --- | --- | --- |
| `SPEECH_TO_TEXT_IMAGE` | `speech-to-text:local` | Container image tag. Set to a built or registry image when starting with `--no-build`. |
| `CONTROL_CENTER_PORT` | `8765` | Loopback port for the containerized Control Center; can be overridden temporarily for staged rollout. |
| `HOST_UID` / `HOST_GID` | `1001` / `1001` | Numeric host identity used by the service process and persistent bind-mounted files. |
| `RENDER_GID` | `992` | Host render-device group added to the container process. |
| `AUDIO_GID` | `29` | Host audio-device group added to the container process. |
| `SPEECH_TO_TEXT_MODELS_DIR` | `./models` on the host | Writable host model directory mounted at `/app/models` in the container. |
| `HF_HOME` | `/home/speech/.cache/huggingface` in the container | Persistent Hugging Face download and conversion cache. |
| `SPEECH_TO_TEXT_TELEMETRY_DATABASE_URL` | Unset | Optional insert-only telemetry writer URL passed to the service container. |
| `SPEECH_TO_TEXT_TELEMETRY_RETENTION_DAYS` | `30` | Retention setting passed to the service container. |
| `SPEECH_TO_TEXT_MAC_TEST_IMAGE` | `speech-to-text:mac-test` | Native ARM64 Mac Docker Desktop test profile image tag. |

See [Deployment](deployment.md#linux-service-container) for the combined Compose commands and required Linux host access.

## Control center command-line options

Run `python -m speech_to_text.control_center --help` to see these options.

| Option | Default | Values and notes |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Must be `127.0.0.1`, `localhost`, or `::1`. |
| `--port` | `8765` | Local HTTP port. |

## Capacity command-line options

Run `python -m speech_to_text.features.model_deployment.capacity --help` for the current CLI help.

| Option | Default | Values and notes |
| --- | --- | --- |
| `--topology` | Required | `shared-model` or `per-input-model`. |
| `--device` | Required | One or more stable host input device names. |
| `--duration-seconds` | `60` | Capture duration for the capacity run. |
| `--stop-timeout` | `60` | Shutdown timeout. |
| `--model` | Feature 01 model default | Model catalog key; see the [configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults). |
| `--runtime` | Feature 01 model default | Selected runtime; see the [configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults). |
| `--precision` | Feature 01 model default | Runtime-supported precision; see the [configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults). |
| `--language` | Feature 01 flow default | Transcription language; see the [configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults). |
| `--chunk-seconds` | Feature 01 flow default | Inference chunk duration; see the [configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults). |
| `--silence-threshold` | Feature 01 flow default | RMS silence gate threshold; see the [configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults). |
| `--queue-capacity` | Feature 01 model default | Shared-model FIFO capacity in chunks; see the [configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults). |
| `--enqueue-timeout` | Feature 01 model default | Maximum queue insertion wait; see the [configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults). |
| `--output` | Standard output | Optional path for JSON evidence. |

## Hardware test environment variables

These variables only configure opt-in proofs in `tests/feature_01/test_hardware_proof.py`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `FEATURE01_RUN_HARDWARE` | Unset | Set to `1` to enable real hardware/model tests. |
| `FEATURE01_REFERENCE_AUDIO` | Unset | Path to the verified reference WAV. |
| `FEATURE01_REFERENCE_TEXT` | Unset | Path to its independently checked transcript. |
| `FEATURE01_REFERENCE_VERIFIED` | Unset | Set to `1` after checking the reference transcript. |
| `FEATURE01_MICROPHONE_DEVICE` | OS default input | Stable microphone name for the real capture test. |
| `FEATURE01_MICROPHONE_RUNTIME` | `openvino-gpu` | Runtime for the microphone proof. |

## See also

- [Getting Started](getting-started.md) for local installation.
- [API Reference](api.md) for where model and flow settings are sent.
- [Deployment](deployment.md) for database setup and migrations.
