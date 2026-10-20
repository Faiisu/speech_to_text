[← Back to README](../README.md)

# Project Structure

This document is the source of truth for where project files belong. Follow it whenever adding, moving, renaming, or removing project files and folders. Keep implementation code, tests, documentation, and local assets in their assigned areas.

## Repository layout

```text
.
├── .scratch/                   # Feature specifications, issues, and task evidence
├── audio/                      # Audio inputs and reference samples
├── data/                       # Ignored persistent runtime and generated deployment state
├── docs/                       # Maintained project documentation
│   └── agents/                  # Instructions for repository workflows
├── legacies-poc/               # Archived proof of concept; isolated from new code
├── models/                     # Local model artifacts, not application source
├── scripts/                    # Repository and deployment utility scripts
├── speech_to_text/             # Installable Python application package
│   ├── frontend/               # Frontend source, assets, and browser E2E tests
│   │   └── e2e/
│   ├── backend/                # Inbound API and server lifecycle
│   │   └── api/
│   │       ├── routes/
│   │       └── schemas/
│   ├── features/               # Callable feature modules
│   │   ├── model_deployment/       # Capture/inference adapters and generic chunk preprocessing seam
│   │   ├── noise_filtering/     # Band-pass filtering and optional stationary-noise denoising
│   │   └── word_matching/
│   └── workflows/              # Use cases and their output behavior
│       ├── audio_recording/
│       ├── audio_input_test/      # Bounded no-model microphone signal diagnostics
│       ├── transcribe_match_forward/ # Transcription orchestration and audio preprocessing snapshots
│       ├── file_replay_stress/
│       ├── model_management/     # Bounded download/conversion jobs and preparation lifecycle
│       └── noise_filtering/      # Standalone WAV filtering and output behavior
├── tests/                      # Automated tests organized by owner and boundary
│   ├── feature_01/
│   ├── noise_filtering/
│   ├── word_matching/
│   ├── backend/
│   ├── workflows/
│   └── frontend_e2e/            # Test-only backend fixtures for browser E2E tests
├── AGENTS.md                   # Repository-wide agent instructions
├── .gitignore                  # Generated files excluded from version control
├── README.md                   # Project entry point
├── compose.mqtt.yaml           # Standalone MQTT broker with health check and persistence
├── Dockerfile                  # Versioned container build definition
├── pyproject.toml              # Package metadata and Python dependencies
├── uv.lock                     # Locked Python dependency resolution
├── pytest.ini                  # Pytest configuration
└── requirements-test.txt        # Test dependency list
```

The frontend owns UI code in `speech_to_text/frontend/src/`. Shared navigation, API transport, and UI primitives live under `src/shared/`; page behavior lives under `src/features/`:

```text
speech_to_text/frontend/src/
├── shared/
└── features/
    ├── dashboard/
    ├── profiles/
    ├── destinations/             # Saved HTTP/HTTPS and MQTT receiver settings and synthetic delivery tests
    ├── runs/
    ├── models/                   # Hugging Face downloads, runtime conversion, and compatibility
    ├── stress-tests/             # Capacity matrix controls, polling, and reports
    ├── model-tuning/             # Host recording, microphone diagnostics, audio uploads, and model comparisons
    └── noise-filtering/          # Saved-audio band-pass controls, listening, and spectrogram comparison
```

The implemented backend module layout is:

```text
speech_to_text/backend/
├── app.py                      # FastAPI app factory and process lifecycle
├── dependencies.py             # Own workflow service, worker pool, and run registries
├── live_audio.py               # WebSocket transport for bounded workflow audio subscriptions
├── profile_store.py            # Persist microphone profiles and output destinations in SQLite
├── profile_credentials.py      # Encrypt saved camera passwords and manage the private database-side key
└── api/
    ├── router.py               # Combine and mount inbound API routes
    ├── microphone_lifecycle.py # Shared microphone start/stop lifecycle helpers
    ├── routes/                  # Endpoint handlers grouped by resource or use case
    │   ├── destinations.py      # Destination configuration and synthetic delivery tests
    │   ├── recordings.py        # Host recording and audio upload lifecycle
    │   ├── microphones.py
    │   ├── models.py
    │   ├── profiles.py
    │   ├── stress_tests.py
    │   ├── noise_filtering.py
    │   └── transcription.py
    └── schemas/                 # API request and response models
        ├── stress_tests.py
        ├── noise_filtering.py
        ├── models.py             # Model inspection and preparation requests
        └── transcription.py
```

Frontend code calls the backend API. Backend routes validate requests and call workflow functions. A workflow calls features, receives their results, and owns the use case's output choices: it returns results to the backend and can send them to another backend or system. The backend maps returned results to frontend responses or streams. Feature logic stays under `features/`; use-case orchestration and delivery behavior stay under `workflows/`.

The model-deployment feature owns Hugging Face inspection, source downloads, runtime conversion, staged validation, and artifact publication in `model_deployment/model_management.py`. The model-management workflow owns bounded background jobs under `workflows/model_management/`; `backend/api/routes/models.py` and `schemas/models.py` own HTTP transport. The frontend model page lives under `src/features/models/`; Model tuning owns host recording, microphone diagnostics, uploads, and model comparisons under `src/features/model-tuning/`. The diagnostic API remains in the microphone routes and the former `/live-audio` URL redirects to Model tuning. Sources live under ignored `models/sources/<model_key>/`, converted weights use the existing precision-specific runtime directories, and `models/models.local.json` records repository aliases. Tests follow the existing feature/workflow/backend/frontend boundaries. The [model-management contract](../.scratch/model-management/spec.md) owns configuration, job state, credentials, and verification behavior.

The audio-recording workflow owns generated IDs, retained WAV metadata, and the storage lifecycle around host capture and audio uploads. The model-deployment feature owns host capture, WAV validation, and compressed-audio conversion. Recordings live under ignored `data/` storage. Its contract is maintained in the [model-tuning specification](../.scratch/model-tuning/spec.md).

The model-deployment feature also owns IP camera audio capture in `model_deployment/rtsp.py`, exported through its public package interface. The transcription and audio-recording workflows select the source through that interface. Backend profile persistence and the existing live run lifecycle own saved profile source definitions and recovery snapshots; the audio-recording workflow owns finite RTSP recording metadata and storage under its [model-tuning contract](../.scratch/model-tuning/spec.md#ipcam-recording). RTSP adapter tests belong under `tests/feature_01/`, recording workflow tests under `tests/workflows/`, API/persistence/recovery tests under `tests/backend/`, and browser lifecycle coverage under `frontend/e2e/`. The [RTSP specification](../.scratch/ipcam-rtsp/spec.md) owns the capture source contract.

`backend/profile_credentials.py` owns saved camera password encryption and the private `<profile database filename>.key` file beside the configured SQLite database. `profile_store.py` persists encrypted passwords and exposes sanitized profile responses plus a private workflow-start credential boundary. That key is runtime deployment state in the existing persistent profile directory, not application source. The [credential contract](../.scratch/ipcam-rtsp/spec.md#saved-profiles) owns key lifecycle and response/edit behavior.

The transcribe-match-forward workflow owns destination configuration and validation in `destinations.py`, outbound JSON/HTTP transport in `http_output.py`, MQTT publication in `mqtt_output.py`, and its run-scoped audio preprocessing snapshot in `audio_preprocessing.py`. It calls the public noise-filtering feature interface while preparing saved noise WAVs, then passes one immutable callable to model deployment for each inference chunk. The model-deployment feature owns the generic callback validation and application seam, not noise-filter settings or algorithms. The backend's destination routes manage stored definitions through the existing profile store and call the workflow's public sender for synthetic tests. Destination credentials remain deployment-managed references, not database secret values. The [workflow output specification](../.scratch/transcript-matching-forwarding/spec.md) owns destination configuration, while the [noise-filtering specification](../.scratch/noise-filtering/spec.md) owns preprocessing settings and behavior.

The audio-input-test workflow owns one bounded, process-local microphone signal diagnostic session and its transient RMS/peak/frame measurements. It opens a source through Feature 01's model-free microphone-source boundary, retains only the latest status, and never stores audio. The microphone routes expose its start/status/stop API. The [microphone diagnostic specification](../.scratch/live-audio/spec.md) owns the duration and lifecycle contract; the [backend API specification](../.scratch/backend-transcription-api/spec.md#microphone-input-test-endpoints) owns the HTTP response shape.

The noise-filtering feature owns reusable Butterworth band-pass processing and optional stationary-noise estimation and denoising; the noise-filtering workflow owns bounded WAV validation, noise analysis and filtering orchestration, writing filtered copies into the audio-recording library, and comparable bounded spectrogram data. Its [specification](../.scratch/noise-filtering/spec.md) defines defaults and validation.

The canonical outgoing payload schema is packaged as `workflows/transcribe_match_forward/match_results_v1.schema.json`. Browser-only receiver fixtures belong under `tests/frontend_e2e/`, including `fake_receiver.py`; browser assertions remain in `frontend/e2e/`.

Reusable host commands include `scripts/run_macos.sh` for the macOS development servers and `scripts/run_full_tests.sh` for the complete macOS/Linux software gate with an isolated MQTT test broker.

## Placement rules

- Put a feature's implementation and public callable interface in `speech_to_text/features/<feature_name>/`. Export the supported interface from that package's `__init__.py`.
- Keep each feature independent of other feature modules: a feature must not import or call another feature's functions. When a use case needs to call functions from multiple features, put that orchestration in `speech_to_text/workflows/<workflow_name>/` and have the workflow call each feature's public interface.
- Put each use case's orchestration and output delivery behavior in `speech_to_text/workflows/<workflow_name>/`. Keep output choices owned by that workflow.
- Keep code that sends a workflow's results to another backend or system inside that owning workflow. Do not create a top-level `integrations/` folder.
- Put inbound HTTP app setup, route handlers, and HTTP-only schemas in `speech_to_text/backend/`. Keep domain decisions in callable features and workflows.
- Put Python feature, backend, and workflow tests under `tests/`, grouped by the boundary they verify. Keep browser E2E specs under `speech_to_text/frontend/e2e/`, beside their frontend tooling; keep test-only Python backend fixtures under `tests/frontend_e2e/`.
- Put maintained documentation under `docs/`. Keep feature specifications, issue tracking, and task evidence under `.scratch/<feature_name>/` as described by the [issue tracker guide](agents/issue-tracker.md).
- Put container build assets at the repository root (`Dockerfile` and `.dockerignore`), and model-export utilities under `scripts/`. The UBX-330M installer owns and generates its Compose configuration under `data/ubx330m/`.
- Keep new application code outside `legacies-poc/`. Modify the archive only when a task explicitly targets legacy code.
- Keep downloaded or machine-specific model artifacts under `models/`, audio inputs under `audio/`, and reusable project commands under `scripts/`. Do not place these assets inside Python packages.
- Keep ignored persistent runtime data and generated deployment state under `data/`.
- Name modules after their responsibility. Put inbound API route modules under `backend/api/routes/`; do not put HTTP endpoints in feature or workflow modules.

When a task introduces a lasting folder category or changes ownership boundaries, update this document in the same change. For documentation topics, first update their existing authoritative document and link to it rather than copying its content here.

## Package discovery

The installable package is discovered from `speech_to_text*` by the configuration in `pyproject.toml`. New application Python packages belong under `speech_to_text/`; tests, docs, scripts, and local assets remain outside that package.

Feature 01 owns bounded transient PCM monitoring in `model_deployment/live_audio.py`, including capture fanout and process IPC. The transcription workflow exposes subscriptions; `backend/live_audio.py` owns WebSocket delivery; `frontend/src/features/runs/LiveAudio.tsx` owns browser playback. The [live audio API contract](../.scratch/backend-transcription-api/spec.md#ws-transcriptionsworkflow_idaudio) defines lifecycle and limits.
