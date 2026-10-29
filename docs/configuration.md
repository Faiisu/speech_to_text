[← Back to README](../README.md)

# Configuration

The [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults) is the source of truth for model and flow options, defaults, valid ranges, and runtime compatibility. Use this page for installation and local configuration guidance.

## Model configuration

Pass model selection and shared-model queue settings to `load_model()`. See the [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults) for fields, defaults, validation, and runtime/precision compatibility.

Model, runtime, and precision are fixed for the lifetime of a loaded handle. Load a new handle to change them.

## Model downloads and conversion

Open **Models** at `/models` to inspect supported Hugging Face speech models, download source weights, or prepare a compatible runtime. Optional tokens are request-scoped; `HF_TOKEN` can provide a server-side default. Install the `model-management` extra together with the target runtime extra. Gemma 4 E2B and E4B require `uv sync --locked --extra backend --extra model-management --extra gemma4`; they run locally through Transformers without conversion. E2B exposes BF16 only because its weights and runtime overhead leave little headroom on a 16 GB system; E4B supports BF16 or float32. The [Gemma 4 contract](../.scratch/gemma-4/spec.md) documents the 30-second audio limit and hardware constraints. The [model-management contract](../.scratch/model-management/spec.md) owns supported formats, defaults, storage, token handling, and the distinction between format compatibility and verified runtime inference. Select **Prepare another runtime** from a local model entry to reuse its repository, local key, and recorded revision before choosing a new runtime or precision; see the [installed-model selection contract](../.scratch/model-management/spec.md#selecting-an-installed-model). The models directory must be writable by the backend user. Use **Delete local model** in the catalog to preview and remove an unused model's local source/runtime files; the [deletion contract](../.scratch/model-management/spec.md#deleting-local-models) defines usage/profile blockers and the cache/legacy boundaries.

## Flow configuration

Pass flow settings to a clip or microphone flow; each input can have independent settings. See the [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults) for the complete option list and validation rules.

The Python callable accepts decoding options listed in the [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults).

The frontend Profile editor also saves those decoding options for microphone runs. Save changes, stop the current workflow, and start it again to apply them. Active runs and their automatic recovery keep the original start-time settings; shared-model workflows retain independent decoding settings. See the [profile decoding settings contract](../.scratch/backend-transcription-api/spec.md#profile-decoding-settings) for API persistence and validation behavior.

The Profile editor and Model tuning page also offer **Thai**, **English**, and **Auto detect** language selection. Existing profiles keep Thai by default. Save and restart a profile to apply its language; a tuning trial captures the selection when it starts. Auto delegates language detection to the runtime and does not guarantee accurate recognition of speech that switches languages within a sentence. See the [backend API contract](../.scratch/backend-transcription-api/spec.md) for validation, persistence, and run snapshots.

### Speech detection and noise-only input

In the Profile editor or Model tuning, enable **VAD** to reject chunks without detected speech before transcription. Adjust the RMS silence threshold separately: it measures signal level, while VAD estimates speech presence. A high RMS floor can reject quiet speech before VAD sees it. Start with the VAD defaults, compare a noise-only recording and a quiet-speech recording in Model tuning, and check both rejected chunks and retained speech before applying settings to a live profile. VAD reduces noise-triggered transcription but does not guarantee that all hallucinations disappear.

The [Feature 01 VAD contract](../.scratch/new-speech-to-text/spec.md#voice-activity-detection) defines defaults, validation, and timing. Existing profiles keep VAD disabled until enabled explicitly. Save and restart a profile to apply changes; active/recovering runs keep their initial snapshot. Model tuning retains each trial's effective settings and reports RTF including VAD work, including a measured skip when VAD rejects a chunk. Model downloads and conversion do not fix these flow-specific settings into model weights.

### Initial prompt

Set **Initial prompt** under decoding settings in a Profile or Model tuning to supply vocabulary or transcription context for either runtime. Leave it blank to disable prompting. For a Profile, save and restart the workflow to apply changes; tuning retains the prompt used for each trial. Defaults, token budgeting, and limitations are defined in the [Feature 01 prompt contract](../.scratch/new-speech-to-text/spec.md#initial-prompt).

### OpenVINO decoder speech filter

In Profile or Model tuning, select an OpenVINO runtime and enable **OpenVINO speech filter** under decoding settings. This adds Whisper's joint no-speech/confidence check after audio reaches the decoder and can be combined with the separate VAD and RMS gates. Defaults, ranges, and the public `normalize_openvino_speech_filter()` callable are defined in the [Feature 01 contract](../.scratch/new-speech-to-text/spec.md#openvino-speech-filtering). The filter starts disabled for existing behavior; save and restart a profile to apply changes. Model tuning retains each trial's effective settings for comparison. The settings remain saved but inactive with CTranslate2 and Transformers; CTranslate2 has a separate native filter.

## Local environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `SPEECH_TO_TEXT_MODELS_DIR` | `models/` under the current working directory, followed by `legacies-poc/models/` as a read-only fallback | Local model files used by catalog discovery and runtime loading. Model management writes to this explicit directory or `models/` under the current working directory; it does not write into the legacy fallback. |
| `SPEECH_TO_TEXT_MODEL` | Feature 01 model default | Backend workflow service model key, fixed when its lazy model handle is loaded. |
| `SPEECH_TO_TEXT_RUNTIME` | Feature 01 runtime default | Backend workflow service runtime, fixed when its lazy model handle is loaded. |
| `SPEECH_TO_TEXT_PRECISION` | Feature 01 precision default | Backend workflow service precision, fixed when its lazy model handle is loaded. |
| `SPEECH_TO_TEXT_PROFILE_DB` | `~/.local/share/speech_to_text/profiles.sqlite3` | SQLite file for saved live audio workflow profiles. Its parent directory is created when the backend loads its profile/destination store. |
| `SPEECH_TO_TEXT_RECORDINGS_DIR` | `data/tuning-recordings` under the current working directory; `/data/tuning-recordings` in the application image | Persistent WAV recordings and metadata for the [model tuning workflow](../.scratch/model-tuning/spec.md). |

No credentials or database server are required to call Feature 01 locally. The backend initializes the SQLite profile/destination store while loading saved destinations; mount its parent directory as persistent storage when running in a container. The previous Control Center and its Mac host microphone bridge are not included in the current application.

## IPCAM audio through RTSP

In the Profile editor or Model tuning capture panel, choose **IPCAM (RTSP)** as the input source. Enter the camera/NVR's audio-enabled RTSP URL, such as `rtsp://192.168.1.50:554/stream1`. For an authenticated camera, enter **Username** and **Password** in the same form; no environment configuration is required. Passwords entered separately can contain characters such as `@` without manual percent-encoding. You can also paste a complete credential URL; the backend normalizes it and removes credentials from public source metadata. The backend host or container must be able to reach the camera. Profiles use the existing model, language, keyword, execution mode, and destination settings for either live input source. Model tuning records a finite sample for repeated clip trials; its [recording contract](../.scratch/model-tuning/spec.md#ipcam-recording) owns capture limits and disconnect behavior. See the [RTSP source contract](../.scratch/ipcam-rtsp/spec.md) for source validation, decoder defaults, and safe errors.

Saved Profile passwords are encrypted by the backend. Editing shows a blank password field: leaving it unchanged keeps the saved password when the URL and username are unchanged; use the explicit clear action to remove it. Duplicating a profile asks for its password again. Model tuning uses entered passwords only for capture and does not save them in recording manifests. Both pages keep entered passwords only in memory for the open page. Use HTTPS when sending camera credentials to a backend across a network.

The backend automatically creates a private encryption key at `<profile database path>.key`, for example `/data/profiles.sqlite3.key` in the application container. Keep that key with the database when backing up or moving the deployment. Losing it makes saved passwords unreadable; the backend reports a safe error instead of replacing the key for existing encrypted credentials. The [credential contract](../.scratch/ipcam-rtsp/spec.md#saved-profiles) owns storage and key lifecycle behavior.

Environment references remain an optional alternative, including for secret query tokens. Set a variable containing the complete URL and allow its name before starting the backend:

```bash
export FRONT_GATE_RTSP_URL='rtsp://camera-user:camera-password@192.168.1.50:554/stream1'
export SPEECH_TO_TEXT_RTSP_SECRET_REFS=FRONT_GATE_RTSP_URL
./scripts/run_macos.sh
```

Choose the environment secret reference option and enter `FRONT_GATE_RTSP_URL` in the Profile editor or Model tuning. Percent-encode reserved characters in credentials embedded in this environment URL. Only the variable name is saved in the profile or recording metadata; its resolved value is held privately for the active capture. Live transcription runs reuse that value during recovery. Changes to the environment or profile apply to a new capture/run.

For Docker, pass both variables into the application container through its deployment environment. Host environment variables are not automatically available inside a container. The UBX installer generates its managed Compose file; use a separate operator-owned Compose override for credentials so updates do not overwrite them. Start or recreate the service with the managed file and the override together.

RTSP capture uses PyAV, already included in the backend extra and application image. No separate `ffmpeg` executable is required. Confirm the camera enables an audio track and the installed decoder supports its codec. Real camera connectivity and recognition quality require validation with the actual device.

## Transcript matching and forwarding

Configure target keywords with `WordMatchingConfig` and workflow output delivery with `HttpForwarderConfig` or `MqttForwarderConfig`. Matching uses normalized literal substring searches and requires no optional tokenizer dependency. The workflow owns whether and where results are sent. Keep bearer tokens in the caller's secret configuration. The backend selects an optional saved destination through `destination_id`; callers manage destination definitions and test delivery through the [destination API](../.scratch/backend-transcription-api/spec.md#destination-endpoints). Profiles retain their selection, and each run uses a start-time sender snapshot. Live HTTP and MQTT destinations send results per chunk; finite HTTP clip results are sent once. Matching defaults, retry behavior, and the forwarded JSON contract are maintained in the [transcript matching and forwarding specification](../.scratch/transcript-matching-forwarding/spec.md).

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

The callable interface, default WAV input, optional saved-recording selection, replay timing, topology matrix, hardware preflight, and capacity verdict are specified in the [Feature 01 file-replay stress contract](../.scratch/new-speech-to-text/spec.md#file-replay-stress-workflow). Call `run_file_replay_stress()` from `speech_to_text.workflows.file_replay_stress` to run the 1/2/4-workflow cold-start matrix. The default model settings come from Feature 01 and can be overridden with `model_config` and `flow_config`; `output_directory` writes one JSON report per topology. Install the `benchmark` extra to collect process and physical-memory measurements. The bundled default asset is `audio/test-audio.wav`.

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
