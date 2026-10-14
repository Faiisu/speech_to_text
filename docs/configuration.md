[← Back to README](../README.md)

# Configuration

The [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults) is the source of truth for model and flow options, defaults, valid ranges, and runtime compatibility. Use this page for installation and local configuration guidance.

## Model configuration

Pass model selection and shared-model queue settings to `load_model()`. See the [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults) for fields, defaults, validation, and runtime/precision compatibility.

Model, runtime, and precision are fixed for the lifetime of a loaded handle. Load a new handle to change them.

## Flow configuration

Pass flow settings to a clip or microphone flow; each input can have independent settings. See the [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults) for the complete option list and validation rules.

The Python callable accepts decoding options listed in the [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults).

## Local environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `SPEECH_TO_TEXT_MODELS_DIR` | `models/` under the current working directory; falls back to `legacies-poc/models/` when it contains installed weights | Local model files used by catalog discovery and runtime loading. |
| `SPEECH_TO_TEXT_MODEL` | Feature 01 model default | Backend workflow service model key, fixed when its lazy model handle is loaded. |
| `SPEECH_TO_TEXT_RUNTIME` | Feature 01 runtime default | Backend workflow service runtime, fixed when its lazy model handle is loaded. |
| `SPEECH_TO_TEXT_PRECISION` | Feature 01 precision default | Backend workflow service precision, fixed when its lazy model handle is loaded. |
| `SPEECH_TO_TEXT_PROFILE_DB` | `~/.local/share/speech_to_text/profiles.sqlite3` | SQLite file for saved microphone workflow profiles. Its parent directory is created when the backend loads its profile/destination store. |

No credentials or database server are required to call Feature 01 locally. The backend initializes the SQLite profile/destination store while loading saved destinations; mount its parent directory as persistent storage when running in a container. The previous Control Center and its Mac host microphone bridge are not included in the current application.

## Transcript matching and forwarding

Configure target keywords with `WordMatchingConfig` and workflow output delivery with `HttpForwarderConfig`. Matching uses normalized literal substring searches and requires no optional tokenizer dependency. The workflow owns whether and where results are sent. Keep bearer tokens in the caller's secret configuration. The backend selects an optional saved destination through `destination_id`; local operators manage destination definitions and test delivery through the [destination API](../.scratch/backend-transcription-api/spec.md#destination-endpoints). Profiles retain their selection, and each run uses a start-time sender snapshot. Matching defaults, retry behavior, and the forwarded JSON contract are maintained in the [transcript matching and forwarding specification](../.scratch/transcript-matching-forwarding/spec.md).

### Output deployment settings

The [workflow destination contract](../.scratch/transcript-matching-forwarding/spec.md#current-destination-interface) is authoritative for these settings and security behavior. Set environment variables before starting the backend or `scripts/run_macos.sh`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SPEECH_TO_TEXT_DESTINATION_ALLOWED_NETWORKS` | Empty | Comma-separated IPv4/IPv6 CIDRs explicitly approving private/loopback receivers; prefer a single receiver address such as `192.168.1.50/32`. |
| `SPEECH_TO_TEXT_DESTINATION_ALLOWED_ORIGINS` | Empty | Additional approved origins; locally saved destination definitions also approve their own origin. |
| `SPEECH_TO_TEXT_DESTINATION_SECRET_REFS` | Empty | Comma-separated names of environment variables allowed to supply bearer, Basic, or API-key credentials. Set those variables separately to their secret values. |
| `SPEECH_TO_TEXT_DESTINATION_RESOURCE_REFS` | Empty | Comma-separated names of environment variables allowed to supply absolute CA/client-certificate/client-key paths. |
| `SPEECH_TO_TEXT_OPERATOR_ORIGINS` | `http://127.0.0.1:5173,http://localhost:5173` | Browser origins permitted for local destination administration and synthetic tests. |
| `SPEECH_TO_TEXT_API_PORT` | `8000` | Expected local API Host port for operator requests; configure alongside the server's actual listening port. |

Example for one credential-free LAN receiver:

```bash
export SPEECH_TO_TEXT_DESTINATION_ALLOWED_NETWORKS=192.168.1.50/32
bash scripts/run_macos.sh
```

Configure its actual URL and method in the destination settings, run the synthetic test, and select that destination in a microphone profile. A saved entry is not proof that the receiver accepts the payload. Secret-bearing receivers require HTTPS; use a deployment-approved private CA for an internal certificate.

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

## File-replay stress workflow

The callable interface, fixed WAV input, replay timing, topology matrix, hardware preflight, and capacity verdict are specified in the [Feature 01 file-replay stress contract](../.scratch/new-speech-to-text/spec.md#file-replay-stress-workflow). Call `run_file_replay_stress()` from `speech_to_text.workflows.file_replay_stress` to run the 1/2/4-workflow cold-start matrix. The default model settings come from Feature 01 and can be overridden with `model_config` and `flow_config`; `output_directory` writes one JSON report per topology. Install the `benchmark` extra to collect process and physical-memory measurements. The fixed asset is `audio/test-audio.wav`.

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
- [Callable Interfaces](api.md) for model and flow entry points.
