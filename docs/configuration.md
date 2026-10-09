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

Copy [`.env.example`](../.env.example) to `.env` to override deployment defaults. No credentials or database settings are required. Keep `.env` out of version control.

| Variable | Default | Purpose |
| --- | --- | --- |
| `COMPOSE_PROFILES` | `linux` | Active Docker Compose profile (`linux` for Linux Intel GPU / ALSA, or `mac` for macOS development with host microphone bridge). |
| `SPEECH_TO_TEXT_MODELS_DIR` | `models/` under the current working directory for a host process (falling back to `legacies-poc/models/` when empty); `/app/models` in containers | Local model files used by catalog discovery and runtime loading. Container images copy host models into `/app/models` during build. The Compose bind mount can be uncommented to override container models with a host path (e.g. `./models` or `./legacies-poc/models`). |
| `MAC_TEST_CONTROL_CENTER_PORT` | `18766` | Mac Docker Desktop test profile API port; published on all IPv4 host interfaces (`0.0.0.0`). |
| `MAC_TEST_MICROPHONE_BRIDGE_PORT` | `18767` | Mac host microphone bridge port; bound to Mac loopback. |
| `MAC_TEST_PLATFORM` | `linux/arm64` | Native Mac test image platform. Use `linux/amd64` only for Docker Desktop emulation fallback. |

Other compose defaults for image tags and host UID/GID are listed below and in [`.env.example`](../.env.example).

## Container deployment settings

These values configure the services defined in `docker-compose.yml` (as well as the standalone `compose.speech-service.yml` and `compose.mac-test.yml` files). The unified image builds natively on both Linux AMD64 and Apple Silicon ARM64. The optional Mac Docker Desktop profile is documented in [Deployment](deployment.md#mac-docker-desktop-test-profile).

| Variable | Default | Purpose |
| --- | --- | --- |
| `SPEECH_TO_TEXT_IMAGE` | `speech-to-text:local` | Container image tag. Set to a built or registry image when starting with `--no-build`. |
| `CONTROL_CENTER_PORT` | `8765` | Port the containerized Control Center listens on inside the Compose network. |
| `SPEECH_TO_TEXT_HOST_PORT` | `18765` | Linux host port published on all IPv4 host interfaces (`0.0.0.0`); the existing SSH tunnel can target this port on Linux. |
| `HOST_UID` / `HOST_GID` | `1001` / `1001` | Numeric host identity used by the service process and persistent bind-mounted files. |
| `RENDER_GID` | `992` | Host render-device group added to the container process. |
| `AUDIO_GID` | `29` | Host audio-device group added to the container process. |
| `HF_HOME` | `/home/speech/.cache/huggingface` in the container | Persistent Hugging Face download and conversion cache. |
| `SPEECH_TO_TEXT_MAC_TEST_IMAGE` | `speech-to-text:mac-test` | Native ARM64 Mac Docker Desktop test profile image tag. |
| `SPEECH_TO_TEXT_HOST_MICROPHONE_BRIDGE` | Disabled | Enables authenticated PCM ingest in the service container; enabled by the Mac Docker Desktop profile. |
| `SPEECH_TO_TEXT_HOST_MICROPHONE_BRIDGE_URL` | `http://127.0.0.1:18767/api` | Host bridge URL supplied to the Control Center page in the Mac profile. |

See [Deployment](deployment.md#linux-service-container) for the service Compose command and required Linux host access.

The Mac host bridge also accepts `SPEECH_TO_TEXT_BRIDGE_PORT` (`18767`), `SPEECH_TO_TEXT_BRIDGE_SERVICE_URL` (`http://127.0.0.1:18766`), and `SPEECH_TO_TEXT_BRIDGE_CONTROL_ORIGIN` (`*`). Wildcard CORS supports loopback aliases used by the Control Center; a specific loopback HTTP origin can be set to enable strict origin checking. See [Deployment](deployment.md#mac-microphone-bridge) for startup and stop commands.

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
- [Deployment](deployment.md) for Linux and Mac service setup.
