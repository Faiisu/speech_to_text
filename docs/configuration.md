[← Back to README](../README.md)

# Configuration

The [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults) is the source of truth for model and flow options, defaults, valid ranges, and runtime compatibility. Use this page for entry points and local command guidance.

## Model configuration

Pass model selection and shared-model queue settings to `load_model()` or `POST /api/features/feature-01-model-deployment/models`. See the [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults) for fields, defaults, validation, and runtime/precision compatibility.

Model, runtime, and precision are fixed for the lifetime of a loaded handle. Load a new handle to change them.

## Flow configuration

Pass flow settings to a clip or microphone flow; each input can have independent settings. See the [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults) for the complete option list and validation rules.

The HTTP clip adapter currently accepts `beam_size`, `temperature`, and `condition_on_previous_text` as form fields. The Python callable accepts all decoding options in the [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults).

## Local environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `SPEECH_TO_TEXT_MODELS_DIR` | `models/` under the current working directory; falls back to `legacies-poc/models/` when it contains installed weights | Local model files used by catalog discovery and runtime loading. |
| `SPEECH_TO_TEXT_HOST_MICROPHONE_BRIDGE` | Disabled | Set to `1` to enable authenticated PCM input from the optional host microphone bridge. |
| `SPEECH_TO_TEXT_HOST_MICROPHONE_BRIDGE_URL` | `http://127.0.0.1:18767/api` | Local host bridge URL supplied to the Control Center page when the bridge is enabled. |
| `SPEECH_TO_TEXT_BRIDGE_PORT` | `18767` | Loopback port used by the optional Mac host microphone bridge. |
| `SPEECH_TO_TEXT_BRIDGE_SERVICE_URL` | `http://127.0.0.1:18766` | Local Control Center URL used by the bridge; set this to the service's port if it differs. |
| `SPEECH_TO_TEXT_BRIDGE_CONTROL_ORIGIN` | `*` | Allowed Control Center origin for bridge requests. A specific local HTTP origin enables strict origin checking. |

No credentials or database settings are required for local operation.

## Control center command-line options

Run `python -m speech_to_text.control_center --help` to see these options.

| Option | Default | Values and notes |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Loopback address; explicit `0.0.0.0` is also supported. |
| `--port` | `8765` | Local HTTP port. |
| `--dev` | Disabled | Enables Python auto-reload and browser refresh when Control Center static assets change. See the [local service run command](../.scratch/feature-test-console/spec.md#local-service-api-and-run-command). |

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
