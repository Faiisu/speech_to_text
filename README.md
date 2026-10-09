# Speech-to-Text

Speech-to-Text is a modular Python service for loading speech models, transcribing audio clips or microphone input, and checking feature behavior through a local control center. It reports per-chunk real-time factor (RTF) locally with each clip result or microphone event; it does not require a database or telemetry service. The former proof of concept remains archived in [`legacies-poc/`](legacies-poc/README.md).

## Quick start

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[control-center]'
.venv/bin/python -m speech_to_text.control_center
```

Open <http://127.0.0.1:8765/>. See [Getting Started](docs/getting-started.md) for adding a model runtime and microphone capture.

## Deployment

- Linux Intel GPU host:
  - **Turnkey deployment (recommended)**: run `docker compose -p speech-feature-01 --profile linux pull speech-service && docker compose -p speech-feature-01 --profile linux up -d speech-service` using the pre-built image (`ghcr.io/faiisu/speech-to-text:latest`) containing pre-baked OpenVINO and CTranslate2 models. Open <http://127.0.0.1:18765/> locally or via host IP.
  - **Local build**: copy [`.env.example`](.env.example) to `.env`, prepare `models/`, then run `docker compose -p speech-feature-01 --profile linux up -d --build`.
- Apple Silicon Docker Desktop: run `docker compose -p speech-mac-test --profile mac up -d --build`, then open <http://127.0.0.1:18766/> locally or use the Mac's host IPv4 address on the network. The Mac microphone bridge runs on the host and listens only at <http://127.0.0.1:18767/>, so bridge-based microphone capture requires a browser on the Mac.

Both Compose profiles publish their HTTP port on all IPv4 host interfaces. The Control Center has no authentication; use these bindings on trusted networks and configure host firewall rules as needed. See [Deployment](docs/deployment.md#network-exposure).

See [Deployment](docs/deployment.md) for Linux host access and the Mac microphone bridge.

## Project guides

- [Getting Started](docs/getting-started.md)
- [Configuration](docs/configuration.md)
- [API Reference](docs/api.md)
- [Architecture](docs/architecture.md)
- [Deployment](docs/deployment.md)
- [Contributing](docs/contributing.md)
- [Feature 01 specification](.scratch/new-speech-to-text/spec.md)
- [Control Center specification](.scratch/feature-test-console/spec.md)
- [Retired system observability specification](.scratch/system-observability/spec.md)
