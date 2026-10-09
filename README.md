# Speech-to-Text

Speech-to-Text is a modular Python package for loading speech models, transcribing audio clips or microphone input, matching Thai words and phrases, and forwarding results. It reports per-chunk real-time factor (RTF) with each clip result or microphone event. The former proof of concept remains archived in [`legacies-poc/`](legacies-poc/README.md).

## Install

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e .
```

This repository currently provides callable feature modules and workflows; the previous Control Center frontend and HTTP server have been removed. See [Getting Started](docs/getting-started.md) for installing optional runtimes and calling Feature 01.

## Project guides

- [Getting Started](docs/getting-started.md)
- [Configuration](docs/configuration.md)
- [Callable Interfaces](docs/api.md)
- [Architecture](docs/architecture.md)
- [Project structure](docs/project-structure.md)
- [Contributing](docs/contributing.md)
- [Feature 01 specification](.scratch/new-speech-to-text/spec.md)
- [Transcript matching and forwarding specification](.scratch/transcript-matching-forwarding/spec.md)
- [Retired system observability specification](.scratch/system-observability/spec.md)
