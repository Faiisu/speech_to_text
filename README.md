# Speech-to-Text

Speech-to-Text is a modular Python package for loading speech models, transcribing audio clips or microphone input, matching Thai words and phrases, and forwarding results. It reports per-chunk real-time factor (RTF) with each clip result or microphone event. The former proof of concept remains archived in [`legacies-poc/`](legacies-poc/README.md).

## Install

```bash
uv sync --locked
```

The repository provides a React frontend, a FastAPI backend, and callable feature and workflow modules. See [Getting Started](docs/getting-started.md) for setup and [API Interfaces](docs/api.md) for the backend API.

## Project guides

- [Getting Started](docs/getting-started.md)
- [Deployment plan: UBX-330M](docs/deployment.md)
- [UBX-330M installer](scripts/install_ubx330m.sh) — run with `sudo ./scripts/install_ubx330m.sh` on the target host.
- [Configuration](docs/configuration.md)
- [API Interfaces](docs/api.md)
- [Architecture](docs/architecture.md)
- [Project structure](docs/project-structure.md)
- [Contributing](docs/contributing.md)
- [Feature 01 specification](.scratch/new-speech-to-text/spec.md)
- [Transcript matching and forwarding specification](.scratch/transcript-matching-forwarding/spec.md)
- [Backend transcription API specification](.scratch/backend-transcription-api/spec.md)

## Historical records

Historical specifications are retained under `.scratch/` for reference. The retired [system observability specification](.scratch/system-observability/spec.md) describes an earlier telemetry deployment, not the current application.
