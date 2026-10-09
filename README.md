# Speech-to-Text

Speech-to-Text is a modular Python service for loading speech models, transcribing audio clips or microphone input, and checking feature behavior through a local control center. It reports per-chunk real-time factor (RTF) locally with each clip result or microphone event; it does not require a database or telemetry service. The former proof of concept remains archived in [`legacies-poc/`](legacies-poc/README.md).

## Quick start

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[control-center]'
.venv/bin/python -m speech_to_text.control_center
```

Open <http://127.0.0.1:8765/>. See [Getting Started](docs/getting-started.md) for adding a model runtime and microphone capture.

## Project guides

- [Getting Started](docs/getting-started.md)
- [Configuration](docs/configuration.md)
- [API Reference](docs/api.md)
- [Architecture](docs/architecture.md)
- [Contributing](docs/contributing.md)
- [Feature 01 specification](.scratch/new-speech-to-text/spec.md)
- [Control Center specification](.scratch/feature-test-console/spec.md)
- [Retired system observability specification](.scratch/system-observability/spec.md)
