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
- [Configuration](docs/configuration.md)
- [API Interfaces](docs/api.md)
- [Architecture](docs/architecture.md)
- [Project structure](docs/project-structure.md)
- [Contributing](docs/contributing.md)
- [Feature 01 specification](.scratch/new-speech-to-text/spec.md)
- [Transcript matching and forwarding specification](.scratch/transcript-matching-forwarding/spec.md)
- [Backend transcription API specification](.scratch/backend-transcription-api/spec.md)
- [Retired system observability specification](.scratch/system-observability/spec.md)
