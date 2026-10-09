# Speech-to-Text

Speech-to-Text is a modular Python system for loading speech models, transcribing audio clips or microphone input, and testing feature behavior through a local control center. Optional telemetry is stored in TimescaleDB and explored in Grafana; the former proof of concept is archived in [`legacies-poc/`](legacies-poc/README.md).

## Quick Start

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[control-center,telemetry]'
.venv/bin/python -m speech_to_text.control_center
```

Open <http://127.0.0.1:8765/>. See [Getting Started](docs/getting-started.md) to install a model runtime, enable microphone capture, or connect telemetry.

## Documentation

- [Getting Started](docs/getting-started.md)
- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [API Reference](docs/api.md)
- [Deployment](docs/deployment.md)
- [Contributing](docs/contributing.md)
- [Agent domain documentation guide](docs/agents/domain.md)
- [Issue tracker guide](docs/agents/issue-tracker.md)
- [Triage labels](docs/agents/triage-labels.md)
- [Feature 01 specification](.scratch/new-speech-to-text/spec.md)
- [Control center specification](.scratch/feature-test-console/spec.md)
- [System observability specification](.scratch/system-observability/spec.md)
- [Archived proof-of-concept guide](legacies-poc/README.md)
