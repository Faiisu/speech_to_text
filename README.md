# Speech-to-Text

Speech-to-Text provides a React frontend, a FastAPI backend, and callable Python feature and workflow modules for audio transcription and keyword matching. The application follows `frontend → backend API → workflow → features`; see [Architecture](docs/architecture.md) for module responsibilities.

## Current capabilities

- Discover local speech models and transcribe WAV clips or microphone input with OpenVINO, CTranslate2, or the Transformers runtime for Gemma 4 E2B/E4B.
- Download Hugging Face Whisper models for OpenVINO/CTranslate2 or prepare Gemma 4 E2B/E4B for Transformers, then delete unused local models in [Models](.scratch/model-management/spec.md).
- Match configured words and phrases as microphone transcript chunks arrive, with cumulative occurrence counts.
- Save reusable microphone profiles and start, monitor, and stop workflow runs from the frontend.
- Receive IP camera/NVR audio over RTSP in saved profiles, with automatic reconnection and camera credentials entered in the frontend or supplied by deployment-managed references. See [RTSP configuration](docs/configuration.md#ipcam-audio-through-rtsp).
- Record microphone or IPCAM RTSP audio, or upload WAV, MP3, or M4A files in [Model tuning](.scratch/model-tuning/spec.md), and compare settings on saved audio.
- Run the Capacity lab file-replay stress matrix to compare shared-model and per-input-model execution at 1, 2, and 4 concurrent workflows.
- Report per-chunk real-time factor (RTF), timing, and capacity evidence.

Profiles persist in SQLite. Workflow run status, transcripts, matches, and events remain in the active backend process and are lost on restart; see [API Interfaces](docs/api.md#backend-api-v1).

Python and backend HTTP workflows support optional JSON forwarding to saved HTTP/HTTPS or MQTT destinations; see [Configuration](docs/configuration.md#transcript-matching-and-forwarding) and the [receiver verification gate](docs/deployment.md#go-live-gate).

## Getting started

The Python package requires Python 3.10 or newer and `uv`. Install the base package from the repository root:

```bash
uv sync --locked
```

Backend, MQTT, model runtime, microphone, benchmark, and test dependencies are optional extras. Follow [Getting Started](docs/getting-started.md) to select extras, run the backend and frontend, call the Python interface, and run tests. Model files and the requested runtime/device must be available for transcription; see [Configuration](docs/configuration.md).

Run the complete software suite on macOS or UBX-330M/Linux with [`scripts/run_full_tests.sh`](scripts/run_full_tests.sh); see [test prerequisites](docs/contributing.md#tests-and-validation).

## Deployment

The supported deployment target is the UBX-330M running Ubuntu 24.04 on x86_64 with an Intel GPU. macOS and Windows deployment targets are not validated. The [deployment guide](docs/deployment.md) covers the Docker service, first-install and update scripts, USB microphone discovery, and readiness checks.

## Project guides

- [Getting Started](docs/getting-started.md)
- [Deployment](docs/deployment.md) — supported platforms, deployment instructions, and the UBX-330M installer.
- [Configuration](docs/configuration.md)
- [API Interfaces](docs/api.md)
- [Architecture](docs/architecture.md)
- [Project structure](docs/project-structure.md)
- [Contributing](docs/contributing.md)

## Feature contracts

Specifications under `.scratch/` define the callable and API contracts; maintained setup and operational guides live under `docs/`.

- [Feature 01 specification](.scratch/new-speech-to-text/spec.md)
- [Transcript matching and forwarding specification](.scratch/transcript-matching-forwarding/spec.md)
- [Backend transcription API specification](.scratch/backend-transcription-api/spec.md)
- [IPCAM RTSP specification](.scratch/ipcam-rtsp/spec.md)

## Historical records

The former proof of concept is archived in [`legacies-poc/`](legacies-poc/README.md); run its commands from that directory. The retired [system observability specification](.scratch/system-observability/spec.md) describes an earlier telemetry deployment. The current application has no historical telemetry store or Grafana service; see [local RTF measurements](docs/architecture.md#local-rtf-measurements).
