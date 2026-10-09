# New Speech-to-Text System: Feature List

## Purpose

Plan the new speech-to-text system from the archived proof of concept (PoC). This file is the single source of truth for the new system's feature list. The PoC remains archived in [`../../legacies-poc/`](../../legacies-poc/); its implementation and audit records are evidence, not new-system requirements by themselves.

## Features

### 01. Select and deploy speech-to-text models with an OpenVINO default

**Goal:** Let a host system start speech-to-text for a microphone connected to the PC, choose an available model, and configure its supported settings through callable feature functions. Separate model loading from input processing so the host can choose one model instance for multiple inputs or one model instance per input process. Keep finite audio clips available for deterministic verification. Start with the PoC's default model, `turbo` (`typhoon-ai/typhoon-whisper-turbo`), and OpenVINO GPU as the default setup.

#### Callable interface

Expose `list_available_models() -> list[ModelInfo]`, `load_model(model_config) -> ModelHandle`, `transcribe_clip(clip, model_handle, flow_config) -> text`, and `start_microphone_flow(device, model_handle, flow_config) -> TranscriptionSession`. `ModelHandle` represents one loaded model/runtime and is passed to inference functions so they do not load the model again. `TranscriptionSession` represents one microphone flow; it exposes a result queue and an idempotent `stop()` operation. Its queue contains typed transcript, error, and completion events. Use `queue.Queue` when producer and consumer share a process; use a process-safe queue/IPC when the consumer is in another process. A caller may reuse one handle for several inputs in the same model-owning process, or call `load_model` separately in each per-input process. A model handle cannot be passed directly across process boundaries; a shared-model process receives audio chunks through a FIFO queue and returns results tagged with their source id and chunk sequence. `list_available_models` rescans local model sources when called and returns `ModelInfo` fields `key`, `display_name`, `repository`, `installed`, `downloadable`, `languages`, and `runtimes`. Each runtime entry contains its key, compatibility, readiness, supported precision options, and an unavailable reason when applicable. Known downloadable models may appear before their weights are installed; duplicate discoveries merge into one entry. `model_config` selects model, runtime, and precision. `flow_config` selects language, silence gate, chunk length, and supported decoding options. Microphone device is passed separately to `start_microphone_flow`; omitted device means the OS-selected default input, while an explicit device is resolved by stable device name, not a transient index. Finite clips accept a filesystem path or WAV bytes in PCM signed 16-bit mono/stereo at 8–48 kHz; the feature resamples and downmixes internally to 16 kHz mono. Model loading, audio capture, decoding, preprocessing, and runtime details stay inside the feature module. Reject unsupported settings explicitly instead of silently ignoring them. See the repository-wide [feature module rule](../../AGENTS.md#modular-programming-and-feature-based-folders).

**Process deployment entry point:** `start_multiprocess_microphone_flows(devices, model_config, flow_config, topology="shared-model")` is the production process deployment API. Shared-model mode starts one model-owning process and one independent capture/chunk producer process per device; `topology="per-input-model"` starts one process per device and loads that process's model there. `start_microphone_flow` is the process-local callable seam for hosts that already own a model handle and for deterministic capture-boundary tests. Both process modes return a group whose sessions expose result queues and whose `stop()` drains terminal events before closing workers.

Each process group accepts optional `flow_configs`, a list aligned with `devices`; each entry overrides the common `flow_config` for that source, including a unique `source_id`, language, chunk size, and threshold. Omit it to use the common settings for every input.

**Audio chunking and text assembly:** The feature owns buffering and inference chunking. Decode and normalize microphone audio inside the feature, then feed the selected model bounded PCM chunks; capture callback sizes do not have to match inference chunk size. Do not overlap adjacent inference chunks. Append successful chunk transcripts in per-source sequence order. If a chunk fails, skip its text, report a non-fatal chunk error separately, and continue with later chunks. When the microphone flow stops with a partial chunk buffered, transcribe that remainder immediately and enqueue its text before reporting flow completion. Some words may be cut at chunk boundaries; this is an accepted trade-off for the initial implementation.

**Model lifecycle and topology:** A `ModelHandle` belongs to the process that loaded it and exposes the selected model key, runtime, precision, and ready/closed/failed state. It remains loaded across all chunks and input flows assigned to it until explicitly closed or that process exits. Never load the model per chunk. In one-model/many-input mode, each microphone process captures and chunks its audio, then sends chunks with source id and sequence through a FIFO IPC queue to one model-owning process; that process schedules inference and routes text/error results back by source id. The shared input queue has a default capacity of 6 chunks and a default enqueue timeout of 1 second; both are configurable. If enqueueing exceeds the timeout, emit a fatal `INPUT_QUEUE_TIMEOUT` event for that source, stop only its microphone producer, discard its queued chunks, and ignore any later result from an already-running inference for that source. Keep the shared model process and other sources running. In one-model/one-input mode, each microphone process calls `load_model` and owns its handle. The caller can select topology by reusing one handle across flows or loading one handle per input process. Keep model/runtime/precision fixed for a handle; changed model configuration requires a new handle. Close handles and flow-owned resources explicitly. The initial deployment preference is one shared model process for multiple microphone inputs; one model process per microphone remains available for comparison and deployment configuration.

#### Session event contract

Every event includes `type` and `source_id`. A `transcript` event contains the monotonically increasing per-source `sequence` and chunk `text`. An `error` event contains a stable `code`, human-readable `message`, optional `sequence`, and `fatal` flag. Chunk inference errors use `CHUNK_INFERENCE_FAILED`, are non-fatal, and do not prevent later chunks. Device/capture failures and `INPUT_QUEUE_TIMEOUT` are fatal for that source. A `completed` event contains `status` (`completed`, `stopped`, or `failed`) and the last sequence, if any. Fatal source errors are followed by exactly one `completed(status="failed")`; normal `stop()` flushes any partial chunk, then emits exactly one `completed(status="stopped")`. Natural end-of-input emits `completed(status="completed")`. Model/configuration errors during `load_model` or before a session starts raise typed exceptions directly; they do not create a session queue.

#### Config scope and defaults

The following table is the authoritative Feature 01 configuration contract. It reflects the validator and initial runtime adapters; catalog readiness and each model adapter may further restrict a choice at load time. Unsupported keys and values raise `ConfigurationError` rather than being ignored.

| Configuration | Option | Default | Valid values and compatibility |
| --- | --- | --- | --- |
| Model | `model` | `turbo` | A non-empty catalog key matching `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` and containing no `..`; path separators are not accepted. The dynamic catalog and selected adapter determine whether a key is known and runnable. |
| Model | `runtime` | `openvino-gpu` | `openvino-gpu`, `openvino-cpu`, or `ctranslate2`; actual availability depends on installed runtime libraries, model support, and host hardware. |
| Model | `precision` | `source` | OpenVINO accepts `source`, `bf16`, `int8`, or `int4`; CTranslate2 accepts `int8` or `float32`. `source` uses the checkpoint's published precision (`bf16` for the initial `turbo` checkpoint). Installed weights and model support may further constrain loading. |
| Model | `queue_capacity` | `6` chunks | Positive integer; bounds the shared-model FIFO queue. |
| Model | `enqueue_timeout_seconds` | `1.0` second | Finite positive number; maximum wait to enqueue a shared-model chunk before that source receives `INPUT_QUEUE_TIMEOUT` and stops. |
| Flow | `source_id` | Generated when omitted | If provided, a non-empty string used to identify events and route results. |
| Flow | `language` | `th` | Non-empty language code or `auto`; the selected model must support the language. |
| Flow | `chunk_seconds` | `5.0` seconds | Finite number in `(0, 30]` for the initial Whisper adapters. Chunks have zero overlap. |
| Flow | `silence_threshold` | `0.05` | Finite RMS threshold in `[0, 1)`. Chunks below the configured gate skip inference; the feature does not estimate room or microphone noise automatically. |
| Flow decoding | `beam_size` | `1` | Integer in `[1, 10]`; decoder search width. |
| Flow decoding | `temperature` | `0.0` | Finite number in `[0, 1]`; zero keeps greedy decoding. |
| Flow decoding | `condition_on_previous_text` | `false` | Boolean; enables conditioning on prior chunk text where supported by the adapter. |
| Flow decoding | `no_repeat_ngram_size` | `0` | Integer in `[0, 20]`; zero disables the no-repeat n-gram constraint. |
| Flow decoding | `repetition_penalty` | `1.0` | Finite number in `[1, 2]`; one applies no repetition penalty. |

The default decoder settings are runtime-parity settings. Decoding options are forwarded to the selected adapter, which may reject options it cannot support. This is an initial inventory from the PoC, not a limit on settings discovered for additional models.

**PoC baseline:** The archived README reports a mean RTF of `0.53` for `typhoon-whisper-turbo` with OpenVINO GPU and int8 weights on an Advantech UBX-330M (Intel Core Ultra 5 125H, Ubuntu 24.04). The measurement used one fixed 21.1-second Thai clip with 5-second chunks. The same report says int8, int4, and bf16 GPU runs were close in speed; int8 is a candidate because the report observed worse transcripts at lower precision on the noisiest chunk. See the [PoC benchmark results](../../legacies-poc/README.md#measuring-performance--benchmark-studio) and [runtime decision](../../legacies-poc/docs/adr/0005-pluggable-inference-runtimes.md).

**Initial default setup:** `turbo` + `openvino-gpu` + source checkpoint precision (`bf16` for the default checkpoint), matching the PoC station model/runtime defaults and favoring the higher-precision transcript where the PoC found lower precision visibly worse on its noisiest chunk. Int8 remains selectable for benchmarking. The performance result is not a production guarantee: the existing PoC still lists the real three-microphone iGPU run and capacity sweep as pending in [ticket 15](../typhoon-whisper-test/issues/15-production-station-service.md). The current-state audit also could not run OpenVINO GPU on the Mac and did not establish the local clip's target-word provenance; see [model deployment audit](../feature-verification/issues/02-verify-model-deployment-and-inference.md).

#### Acceptance evidence to establish before treating deployment as complete

- Capture from a microphone connected to the PC, defaulting to the input device selected by the host operating system; allow explicit device selection. The Mac Docker Desktop profile uses the [authenticated host bridge contract](#external-dependency-injection) while native deployments continue to capture in the service. Use `turbo`, `openvino-gpu`, and source checkpoint precision when model/runtime/precision are not specified; validate the complete OpenVINO setup on the target Linux machine.
- Verify `list_available_models` returns selectable models and compatible runtimes for the current machine. Verify both topologies through the function interface: load one model handle and reuse it for multiple inputs, and load separate handles in separate per-input processes. Verify that loading and inference errors are observable and that handles are closed correctly.
- Decode and normalize captured audio inside the feature, and apply configured chunk length independently of capture callback sizes. Adjacent inference chunks do not overlap.
- `transcribe_clip` returns the complete transcript as text at end-of-file by appending successful chunk text in order. A microphone session puts each chunk's transcript text in its result queue; on stop, transcribe any buffered partial chunk and enqueue its text before completion. Skip failed chunks, report their errors separately, and continue processing. Keep diagnostics out of transcript text; configuration and inference failures are explicit queue messages.
- When the shared inference queue remains full past its configured enqueue timeout, emit a queue-timeout error and stop only the microphone process that could not enqueue; verify that other microphone flows and the shared model process continue.
- Keep model/runtime/precision fixed for a loaded handle; apply changed model settings by loading a new handle. Keep input-specific settings independent per microphone flow.
- Make every supported, safe-to-adjust model/runtime setting configurable with a documented default and validation; reject unsupported settings clearly.
- Install and load the selected model/runtime on the actual target machine; unavailable devices, missing weights, conversion errors, or load failures must be reported as failures rather than a ready service.
- Make the active model/runtime available as session or handle status, separate from transcript text.
- Rewrite/add tests to exercise the current callable function contracts, including model listing, handle reuse, both topologies, FIFO ordering, queue-timeout shutdown, chunk error skipping, and transcript outputs. Keep hardware/runtime proofs separate from deterministic function-contract tests.
- Replay a versioned, fixed Thai reference clip through the production inference path and record clip identity, model, runtime, device, precision, language, transcript, per-chunk latency, and RTF as verification evidence. The reference transcript must be independently checked; do not treat a non-empty transcript or a filename as proof of recognition accuracy.
- Benchmark both one-model/many-input and one-model-per-microphone-process modes at microphone pace on the target hardware. Measure model load time, total memory, queue growth, dropped chunks, and whether each input sustains real time; the PoC's shared-model result does not establish per-process capacity.
- Record the deployed model/runtime/precision and the measured hardware so the result can be reproduced.
- Use a versioned Thai reference WAV with independently checked transcript; deterministic contract tests assert exact fake-runtime chunk ordering, while real-model acceptance records normalized Thai character error rate and requires it to be at most 20% on the reference set.
- The initial capacity target is three concurrent microphone inputs on the Advantech UBX-330M target; report the measured result separately for shared-model and per-input-model topologies. This is a benchmark target, not a performance guarantee before the target run passes.

### Per-chunk performance measurement

For each chunk that reaches inference, record its audio duration and inference duration separately. The real-time factor is `RTF = inference_seconds / audio_seconds`; `RTF <= 1.0` means inference completed within that chunk's audio duration, while `RTF > 1.0` means it did not sustain real time for that chunk. Keep and display each chunk's numeric RTF instead of replacing the individual values with a run-wide average. Each record includes `type: "measurement"`, `feature_id`, `operation`, OS process ID (`pid`), UTC completion timestamp (`completed_at`), `source_id`, `sequence`, `elapsed_seconds`, `audio_seconds`, `inference_seconds`, numeric `rtf`, `status`, and an optional `error`. Do not emit a fabricated RTF for a silent chunk skipped before inference. Clip responses return their chunk measurements, microphone sessions publish a measurement event for each inferred chunk, and process-group event streams publish the same per-chunk records; no database or remote telemetry service is required.

**Decisions resolved for Feature 01:** Model catalog discovery is dynamic and local, with install/readiness and runtime compatibility exposed to the caller; known downloadable models may appear before installation. Initial default precision is the source checkpoint precision (`bf16` for `turbo`), while int8 remains selectable. Verification clips are PCM16 WAV files supplied by path or bytes, mono/stereo at 8–48 kHz, normalized internally to 16 kHz mono. The shared FIFO queue defaults to 6 chunks with a 1-second enqueue timeout; timeout terminates only the microphone source that cannot enqueue. Session events follow the typed contract above. The first capacity target is three microphones on the UBX-330M. Real-model transcript acceptance uses a verified Thai reference set and normalized character error rate no greater than 20%; deterministic function tests use exact expected fake-runtime outputs.

**Docker network contract:** Linux Compose uses bridge networking, listens on container port `8765`, and publishes host `0.0.0.0:18765` by default (`SPEECH_TO_TEXT_HOST_PORT` overrides the host port). The Mac Docker Desktop test profile publishes on host `0.0.0.0:18766`; its host microphone bridge remains bound to `127.0.0.1:18767`. Direct local CLI execution still defaults to loopback. Compose HTTP ports are reachable through host IPv4 interfaces, and the Control Center has no authentication. Deployment commands and tunnel endpoint details are maintained in [Deployment](../../docs/deployment.md#linux-service-container).

**Validation still required:** Run the Mac microphone smoke test, then verify OpenVINO GPU model installation, loading, inference, the 3-microphone capacity target, and both topologies on the UBX-330M. These are execution proofs against the decisions above, not open product/interface decisions.

**Capacity tool:** Run `python -m speech_to_text.features.model_deployment.capacity --topology shared-model --device 'MIC 1' 'MIC 2' 'MIC 3' --duration-seconds 60 --output shared-model.json` and repeat with `--topology per-input-model --output per-input-model.json`. It reports platform/model/runtime/precision, per-source startup and terminal status, measured shared queue depth (per-input queue depth is explicitly unavailable), discarded/failed chunks, process memory when `psutil` is installed, per-chunk latency/RTF, source identity, capture and drain duration, and model utilization. A capacity verdict requires each source to produce at least `max(5 seconds, 95% of the capture window)` eligible audio, clean terminal status, no errors/drops, and utilization at or below 1.05 to allow 5% scheduling/frame tolerance. Silence or insufficient eligible audio returns an inconclusive verdict, not a capacity pass. CLI exits 0 for a pass, 1 for a failed run, 2 when runtime/capture prerequisites are unavailable, and 3 when workload evidence is inconclusive. No capacity result is fabricated.

## Feature 01 executable acceptance contract

Tests live in [`../../tests/feature_01/`](../../tests/feature_01/), separately from the new application package. The new public module is `speech_to_text.features.model_deployment`. Until that module is implemented, deterministic tests fail at setup with an explicit missing-implementation message; they are not marked as expected failures or replaced with a fake feature implementation.

The callable functions above are the agreed test boundaries. Configuration, catalog entries, runtime entries, and session events use mappings with the fields documented here. Model handles and sessions expose attributes/methods. `ModelHandle.close()` is idempotent, releases its runtime, and changes `state` to `closed`; using a closed handle raises `ModelClosedError`. The handle snapshots configuration so changing the caller's mapping does not change a loaded model. Successful chunk texts are stripped, empty texts omitted, and remaining texts joined with a single space in sequence order. Sequences start at zero and count inference chunks, including failed chunks. A finite clip's skipped-chunk diagnostics use `ChunkInferenceWarning`; the return value remains transcript text alone.

### External dependency injection

`load_model(model_config, *, runtime_factory=None)` optionally accepts a factory called once with the resolved configuration mapping. The runtime returned exposes `transcribe(audio, *, language, decoding_options) -> str` and `close()`. Audio passed to that boundary is normalized mono `numpy.float32` PCM at 16 kHz. Omitting the factory selects the production model/runtime adapter. Tests inject a scripted external runtime, while all decoding, buffering, chunking, scheduling, transcript assembly, and lifecycle behavior must still run in the real feature module.

`start_microphone_flow(device=None, model_handle=..., flow_config=..., *, audio_source_factory=None)` optionally accepts a factory called with keyword arguments `device`, `on_audio`, and `on_error`. Its source exposes `sample_rate`, `channels`, `start()`, and `stop()`. `on_audio` receives mono or frame-by-channel float32 PCM at the source's sample rate; `on_error` receives the capture exception. The feature performs any required normalization/resampling. Omitting this factory opens the actual OS microphone. The source factory is an external capture boundary, not an alternative feature implementation. `session.stop(timeout=...)` waits for capture shutdown, accepted-chunk processing, partial-chunk flush, and the single terminal event; it must raise on shutdown timeout rather than silently reporting completion. Per-source settings remain independent even when sources share a loaded model.

The Mac Docker Desktop profile cannot access CoreAudio devices from its Linux VM. It enables an authenticated HTTP audio-ingest adapter on the existing `POST /microphones` session. The Mac host bridge captures the selected input and posts ordered batches of mono float32 PCM to `POST /sessions/{source_id}/audio`; each request is limited to one second and requires that session's unguessable bearer token. The adapter passes samples to the same source callback used by native capture, preserving Feature 01 buffering, resampling, inference chunking, partial flush, events, and per-chunk telemetry. The host bridge binds to Mac loopback, uses wildcard CORS by default to support loopback hostname aliases, and offers strict local-origin checking as an option. It bounds its capture queue, drains accepted frames before session stop, and fails the session rather than silently dropping audio. This adapter is for one Control Center microphone session; process-group and capacity APIs continue to require direct host capture.

`list_available_models(*, catalog_path=None)` optionally selects a local JSON registry (`{"model-key": "organization/repository"}`). An explicit registry isolates discovery to the known builtin candidates, that registry, and converted weights beside it, without reading the host's global cache. Omission uses normal local discovery. Discovery never downloads weights or queries the network. Rescanning observes registry changes without a restart. Readiness is reported separately from being a known downloadable candidate.

### Configuration validation and error types

Model configuration accepts `model`, `runtime`, `precision`, `queue_capacity`, and `enqueue_timeout_seconds`. Defaults are documented above; `precision="source"` means the checkpoint's published precision. Queue capacity is a positive integer; enqueue timeout is a finite positive number of seconds. Runtime/precision compatibility must be validated before loading. Unknown keys/options raise `ConfigurationError` rather than being ignored.

Flow configuration accepts `source_id`, `language`, `chunk_seconds`, `silence_threshold`, and `decoding_options`. The default chunk length is 5 seconds; accepted lengths are finite values in `(0, 30]` for the initial Whisper adapters. RMS threshold is in `[0, 1)`; default `0.05`. Silence skips inference without producing invented text. No overlap is supported. Decoding options are validated against the selected adapter; unsupported options fail explicitly. Source IDs identify events and IPC routing; an omitted ID is generated uniquely.

The public error classes are `ConfigurationError`, `ModelLoadError`, `ModelClosedError`, and `AudioInputError`; missing devices/loading failures before session creation raise directly. Runtime load errors preserve the actionable cause in their message. Asynchronous capture failures emit `CAPTURE_FAILED`; chunk errors emit `CHUNK_INFERENCE_FAILED`; queue overload emits `INPUT_QUEUE_TIMEOUT`. Error events contain `code`, `message`, `sequence` (or `None`), and `fatal` in addition to `type`/`source_id`. Completion events contain `last_sequence` (or `None`) and `status`. This fixes the concrete names used by the executable tests.

### Coverage and execution

| Suite | Evidence |
| --- | --- |
| `test_model_contract.py` | Catalog metadata/rescan, model/runtime defaults and overrides, one load across chunks/clips, immutable loaded settings, load/config errors, close/reuse, independent model ownership in spawned input processes |
| `test_clip_contract.py` | WAV path/bytes, downmix/resample, ordered text, no overlap, final partial chunk, silence gate, skipped-chunk diagnostics, language/decoder configuration, invalid input/settings |
| `test_microphone_contract.py` | Default/explicit microphone selection, callback-independent chunking, stop/flush/idempotence, error events, shared-model source routing/settings, global FIFO, isolated timeout shutdown, queued/in-flight result discard, capture shutdown |
| `test_hardware_proof.py` | Opt-in real OpenVINO transcript/CER/RTF proof and real microphone-to-model transcript/shutdown proof; records evidence through JUnit properties |

Install `requirements-test.txt` into the chosen test environment. With the existing root environment:

```bash
.venv/bin/python -m pytest tests/feature_01 -q
.venv/bin/python -m pytest tests/feature_01 --collect-only -q
```

Hardware proofs are skipped by default. After independently checking a versioned Thai reference WAV/transcript on the Linux target:

```bash
FEATURE01_RUN_HARDWARE=1 \
FEATURE01_REFERENCE_AUDIO=/absolute/path/reference.wav \
FEATURE01_REFERENCE_TEXT=/absolute/path/reference.txt \
FEATURE01_REFERENCE_VERIFIED=1 \
.venv/bin/python -m pytest tests/feature_01/test_hardware_proof.py \
  -k real_openvino --junitxml=/tmp/feature01-openvino.xml
```

The quality test computes normalized character edit distance divided by reference character count, after Unicode NFC normalization, case folding, and removing whitespace/punctuation. It requires CER at most 20% and clip inference RTF below 1; model loading time is excluded from RTF. This is an initial acceptance threshold, not a measured accuracy claim. No model assets are automatically downloaded by the deterministic suite.

For the macOS microphone proof, explicitly select an installed runtime; the production default remains OpenVINO GPU. Speak a Thai phrase during the six-second capture window:

```bash
FEATURE01_RUN_HARDWARE=1 \
FEATURE01_MICROPHONE_RUNTIME=ctranslate2 \
FEATURE01_MICROPHONE_DEVICE='MacBook Air Microphone' \
.venv/bin/python -m pytest tests/feature_01/test_hardware_proof.py \
  -k real_microphone --junitxml=/tmp/feature01-microphone.xml
```

Passing injected-source tests proves the callable pipeline contract, not physical capture or GPU capability. Spawned-process tests exercise real IPC routing, ordering, queue saturation, and independent model ownership with deterministic injected sources; they do not prove physical three-microphone capacity. The target capacity comparison, OpenVINO inference, per-chunk target timing/queue-growth/memory measurements, and device-disconnect/reconnect trials remain hardware deployment proofs; this suite does not claim those passed.

Current software verification: `.venv/bin/python -m pytest tests/feature_01 -q` passes 70 deterministic tests and skips the 2 opt-in hardware proofs. Production adapter API tests use mocked external libraries to verify precision forwarding and source-conversion cache reuse; they do not mock the feature implementation. The Mac host-bridge API and browser lifecycle have separate deterministic authentication, ordering, and UI-routing coverage. `.venv/bin/python -m speech_to_text.features.model_deployment.capacity --help` succeeds. The parent-run CTranslate2 smoke evidence is in [`evidence/local-runtime-smoke.json`](evidence/local-runtime-smoke.json); it proves model load, clip inference, and close only, with quality unproven and CPU RTF above real time. Physical microphone capture through the local session API is in [`evidence/local-microphone-smoke.json`](evidence/local-microphone-smoke.json); it proves capture/resampling/flush/shutdown using an injected runtime. Spawned IPC capture was also exercised on the named Mac microphone in both topologies; [`evidence/local-ipc-microphone-smoke.json`](evidence/local-ipc-microphone-smoke.json) proves process routing and shutdown with an injected runtime, not model recognition, target capacity, or GPU performance. OpenVINO GPU, verified Thai CER, and three-microphone target capacity remain pending.

## Planning status

Feature 01 work is tracked in nine tickets under `issues/`: model catalog/runtime handles, finite clips, microphone sessions, process topologies/capacity tooling, target hardware acceptance, configuration documentation, the Mac Docker microphone bridge, removal of persistent observability, and Linux Docker port publication. Software tickets 01–04 and 07–09 are implemented; ticket 08 records the completed Mac and Linux deployment transition. Target hardware ticket 05 remains `ready-for-human` with execution pending prerequisites. Ticket 06 records the completed documentation SSOT work. The archived application remains unchanged.

## Development tool: local feature test console

The shared browser control-center plan is tracked in [`../feature-test-console/spec.md`](../feature-test-console/spec.md). Its first feature page targets Feature 01. Develop a page and interactive test structure alongside every future feature's backend implementation; feature contracts and automated tests remain defined with that feature.
