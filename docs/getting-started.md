[← Back to README](../README.md)

# Getting Started

This guide installs the callable Python package. The previous Control Center UI and HTTP server have been removed; a replacement frontend/backend is not part of this phase.

## Prerequisites

- Python 3.10 or newer.
- [`uv`](https://docs.astral.sh/uv/) for creating and installing into a local virtual environment.

## Install

From the repository root:

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e .
```

## Install optional runtimes and microphone support

Install only the extras needed for your work:

```bash
uv pip install --python .venv/bin/python -e '.[openvino]'
```

```bash
uv pip install --python .venv/bin/python -e '.[microphone]'
uv pip install --python .venv/bin/python -e '.[thai-word-matching]'
```

For other runtime choices, see the [Feature 01 configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults). OpenVINO GPU availability depends on the host and its drivers; a Mac development environment does not prove Linux target-hardware support.

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
uv pip install --python .venv/bin/python -e '.[test]'
.venv/bin/pytest -q
```

Real microphone and model tests are opt-in and skip by default. Read [Contributing](contributing.md#tests-and-validation) before running hardware proofs.

## See also

- [Configuration](configuration.md) for runtime, flow, and matching settings.
- [Architecture](architecture.md) for module ownership.
- [Callable Interfaces](api.md) for public feature interfaces.
