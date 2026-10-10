[← Back to README](../README.md)

# Getting Started

This guide installs the callable Python package, optional API server, and frontend preview. The previous Control Center UI and HTTP server have been removed; the replacement frontend calls the FastAPI backend.

## Prerequisites

- Python 3.10 or newer.
- [`uv`](https://docs.astral.sh/uv/) for creating and installing into a local virtual environment.

## Install

From the repository root:

```bash
uv sync --locked
```

The root `uv.lock` pins the resolved dependency versions. `--locked` installs from that lockfile and fails if it no longer matches `pyproject.toml`.

## Install optional runtimes and microphone support

Install only the extras needed for your work:

```bash
uv sync --locked --extra openvino
```

```bash
uv sync --locked --extra microphone
```

```bash
uv sync --locked --extra ctranslate2
```

For other runtime choices, see the [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults). OpenVINO GPU availability depends on the host and its drivers; a Mac development environment does not prove Linux target-hardware support.

## Run the backend API

```bash
uv sync --locked --extra backend --extra microphone --extra openvino --extra ctranslate2
uv run --no-sync uvicorn speech_to_text.backend.app:app --host 127.0.0.1 --port 8000
```

The workflow service loads the model on the first transcription request. See [Configuration](configuration.md#local-environment-variables) to override its model settings, and the [Backend API v1 contract](../.scratch/backend-transcription-api/spec.md) for endpoints and process-local state limits.

## Run the frontend in development

Keep the backend running, then open a second terminal:

```bash
cd speech_to_text/frontend
npm ci
npm run dev
```

Open the Vite URL printed in the terminal (normally `http://localhost:5173`). The development server forwards `/api` requests to the backend at `http://localhost:8000`. The frontend includes profile management, workflow monitoring, and the [Capacity lab](../.scratch/new-speech-to-text/spec.md#frontend-design) at `/stress-tests`. It also includes a catch-all page for unknown URLs; visit `http://localhost:5173/unknown/path` to see the 404 route.

To build frontend assets, run `npm run build` from `speech_to_text/frontend/`. Production static hosting and FastAPI static-file mounting are not configured yet; the Vite development server is the supported preview path for this phase.

## Call Feature 01

```python
from speech_to_text.features.model_deployment import load_model, transcribe_clip

model = load_model({"model": "turbo", "runtime": "openvino-gpu"})
try:
    transcript = transcribe_clip("sample.wav", model, {"language": "th"})
    print(transcript)
finally:
    model.close()
```

Feature 01 accepts WAV paths or bytes at its Python callable boundary. Its [specification](../.scratch/new-speech-to-text/spec.md) documents model configuration, clip and microphone behavior, session events, and process topologies.

## Run tests

Install the test extra if it is not already present, then run the feature contract suite:

```bash
uv sync --locked --extra test
.venv/bin/pytest -q
```

Real microphone and model tests are opt-in and skip by default. Read [Contributing](contributing.md#tests-and-validation) before running hardware proofs.

## See also

- [Configuration](configuration.md) for runtime, flow, and matching settings.
- [Architecture](architecture.md) for module ownership.
- [Callable Interfaces](api.md) for public feature interfaces.
