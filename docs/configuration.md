[← Back to README](../README.md)

# Configuration

The [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults) is the source of truth for model and flow options, defaults, valid ranges, and runtime compatibility. Use this page for installation and local configuration guidance.

## Model configuration

Pass model selection and shared-model queue settings to `load_model()`. See the [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults) for fields, defaults, validation, and runtime/precision compatibility.

Model, runtime, and precision are fixed for the lifetime of a loaded handle. Load a new handle to change them.

## Flow configuration

Pass flow settings to a clip or microphone flow; each input can have independent settings. See the [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults) for the complete option list and validation rules.

The Python callable accepts decoding options listed in the [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults).

The frontend Profile editor also saves those decoding options for microphone runs. Save changes, stop the current workflow, and start it again to apply them. Active runs and their automatic recovery keep the original start-time settings; shared-model workflows retain independent decoding settings. See the [profile decoding settings contract](../.scratch/backend-transcription-api/spec.md#profile-decoding-settings) for API persistence and validation behavior.

The Profile editor and Model tuning page also offer **Thai**, **English**, and **Auto detect** language selection. Existing profiles keep Thai by default. Save and restart a profile to apply its language; a tuning trial captures the selection when it starts. Auto delegates language detection to the runtime and does not guarantee accurate recognition of speech that switches languages within a sentence. See the [backend API contract](../.scratch/backend-transcription-api/spec.md) for validation, persistence, and run snapshots.

## Local environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `SPEECH_TO_TEXT_MODELS_DIR` | `models/` under the current working directory; falls back to `legacies-poc/models/` when it contains installed weights | Local model files used by catalog discovery and runtime loading. |
| `SPEECH_TO_TEXT_MODEL` | Feature 01 model default | Backend workflow service model key, fixed when its lazy model handle is loaded. |
| `SPEECH_TO_TEXT_RUNTIME` | Feature 01 runtime default | Backend workflow service runtime, fixed when its lazy model handle is loaded. |
| `SPEECH_TO_TEXT_PRECISION` | Feature 01 precision default | Backend workflow service precision, fixed when its lazy model handle is loaded. |
| `SPEECH_TO_TEXT_PROFILE_DB` | `~/.local/share/speech_to_text/profiles.sqlite3` | SQLite file for saved live audio workflow profiles. Its parent directory is created when the backend loads its profile/destination store. |
| `SPEECH_TO_TEXT_RECORDINGS_DIR` | `data/tuning-recordings` under the current working directory; `/data/tuning-recordings` in the application image | Persistent WAV recordings and metadata for the [model tuning workflow](../.scratch/model-tuning/spec.md). |

No credentials or database server are required to call Feature 01 locally. The backend initializes the SQLite profile/destination store while loading saved destinations; mount its parent directory as persistent storage when running in a container. The previous Control Center and its Mac host microphone bridge are not included in the current application.

## IPCAM audio through RTSP

In the Profile editor, choose **IPCAM (RTSP)** as the input source. Enter the camera/NVR's audio-enabled RTSP URL, such as `rtsp://192.168.1.50:554/stream1`, or choose an environment secret reference for an authenticated URL. The backend host or container must be able to reach the camera. The existing model, language, keyword, execution mode, and destination settings apply to either live input source. See the [RTSP source contract](../.scratch/ipcam-rtsp/spec.md) for validation, defaults, capture errors, and recovery behavior.

Direct URLs cannot contain a username or password. For authenticated cameras or secret query tokens, set an environment variable containing the complete URL and allow its name before starting the backend:

```bash
export FRONT_GATE_RTSP_URL='rtsp://camera-user:camera-password@192.168.1.50:554/stream1'
export SPEECH_TO_TEXT_RTSP_SECRET_REFS=FRONT_GATE_RTSP_URL
./scripts/run_macos.sh
```

Choose the environment secret reference option and enter `FRONT_GATE_RTSP_URL` in the Profile editor. Percent-encode reserved characters in URL credentials. Only the variable name is saved in the profile; its resolved value is held privately for the active run and reused during recovery. Changes to the environment or profile apply to a new run.

For Docker, pass both variables into the application container through its deployment environment. Host environment variables are not automatically available inside a container. The UBX installer generates its managed Compose file; use a separate operator-owned Compose override for credentials so updates do not overwrite them. Start or recreate the service with the managed file and the override together.

RTSP capture uses PyAV, already included in the backend extra and application image. No separate `ffmpeg` executable is required. Confirm the camera enables an audio track and the installed decoder supports its codec. Real camera connectivity and recognition quality require validation with the actual device.

## Transcript matching and forwarding

Configure target keywords with `WordMatchingConfig` and workflow output delivery with `HttpForwarderConfig` or `MqttForwarderConfig`. Matching uses normalized literal substring searches and requires no optional tokenizer dependency. The workflow owns whether and where results are sent. Keep bearer tokens in the caller's secret configuration. The backend selects an optional saved destination through `destination_id`; callers manage destination definitions and test delivery through the [destination API](../.scratch/backend-transcription-api/spec.md#destination-endpoints). Profiles retain their selection, and each run uses a start-time sender snapshot. Matching defaults, retry behavior, and the forwarded JSON contract are maintained in the [transcript matching and forwarding specification](../.scratch/transcript-matching-forwarding/spec.md).

### Output deployment settings

The [workflow destination contract](../.scratch/transcript-matching-forwarding/spec.md#current-destination-interface) is authoritative for these settings and security behavior. Set environment variables before starting the backend or `scripts/run_macos.sh`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SPEECH_TO_TEXT_DESTINATION_ALLOWED_NETWORKS` | Empty locally; the UBX-330M installer sets `0.0.0.0/0,::/0` in its generated Compose file | Comma-separated IPv4/IPv6 CIDRs approving private/loopback receivers. The UBX-330M default permits destinations on any network; link-local and metadata addresses remain blocked. |
| `SPEECH_TO_TEXT_DESTINATION_ALLOWED_ORIGINS` | Empty | Additional approved origins; locally saved destination definitions also approve their own origin. |
| `SPEECH_TO_TEXT_DESTINATION_SECRET_REFS` | Empty | Comma-separated names of environment variables allowed to supply bearer, Basic, API-key, or MQTT username/password credentials. Set those variables separately to their secret values. |
| `SPEECH_TO_TEXT_DESTINATION_RESOURCE_REFS` | Empty | Comma-separated names of environment variables allowed to supply absolute CA/client-certificate/client-key paths. |

Example for one credential-free LAN receiver:

```bash
export SPEECH_TO_TEXT_DESTINATION_ALLOWED_NETWORKS=192.168.1.50/32
bash scripts/run_macos.sh
```

Configure its actual URL and method in the destination settings, run the synthetic test, and select that destination in a microphone profile. A saved entry is not proof that the receiver accepts the payload. Secret-bearing receivers require HTTPS; use a deployment-approved private CA for an internal certificate.

### MQTT destination setup

Install the `mqtt` extra for a manually launched backend; `scripts/run_macos.sh` and the application Docker image include it. Start the [managed MQTT broker](deployment.md#mqtt-broker), then approve its loopback address before launching a host backend:

```bash
export SPEECH_TO_TEXT_DESTINATION_ALLOWED_NETWORKS=127.0.0.1/32
./scripts/run_macos.sh
```

In `/destinations`, select MQTT, set broker URL `mqtt://127.0.0.1:1883`, choose a topic such as `speech/transcriptions`, save, and send the synthetic JSON test. Select that destination in a profile. The [MQTT contract](../.scratch/transcript-matching-forwarding/spec.md#mqtt-destination-contract) defines QoS, retain, TLS, credentials, retry, and acknowledgement semantics. Authenticated remote brokers require `mqtts://` and approved environment secret references.

For a backend inside a container, loopback refers to that container. Use a reachable broker hostname/address on its Docker network or host and approve the corresponding private network; do not copy the host loopback URL into an isolated application container.

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
