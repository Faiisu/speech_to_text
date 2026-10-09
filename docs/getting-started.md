[← Back to README](../README.md)

# Getting Started

This guide starts the current Speech-to-Text control center from a repository-local Python environment. Feature behavior and acceptance criteria remain in the [Feature 01 specification](../.scratch/new-speech-to-text/spec.md#features).

## Prerequisites

- Python 3.10 or newer.
- [`uv`](https://docs.astral.sh/uv/) for creating and installing into a local virtual environment.
- An operating-system audio input device for live microphone tests.

## Install and start the control center

From the repository root, create `.venv/` and install the control center:

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[control-center]'
.venv/bin/python -m speech_to_text.control_center
```

The service binds to `127.0.0.1:8765` by default and is intended to run directly on the host. Transcription and per-chunk RTF reporting work locally without a database or telemetry service.

## Add a model runtime and microphone support

Install the optional runtime and capture dependencies into the same local environment:

```bash
uv pip install --python .venv/bin/python -e '.[openvino,microphone]'
```

Feature 01's model and flow defaults, supported options, validation ranges, and runtime compatibility are listed in its [configuration contract](../.scratch/new-speech-to-text/spec.md#config-scope-and-defaults). OpenVINO GPU availability depends on the host and its drivers; a Mac development environment does not prove Linux target-hardware support. See the [Feature 01 acceptance evidence](../.scratch/new-speech-to-text/spec.md#acceptance-evidence-to-establish-before-treating-deployment-as-complete) for the required target proof.

In the control center, inspect the model catalog, load a compatible model, then transcribe a WAV clip or start a microphone session. Clip results include per-inferred-chunk measurements. Microphone sessions publish a measurement event for each inferred chunk alongside transcript, error, and completion events. The model remains loaded until it is closed or the service exits. See the [API reference](api.md#feature-01-model-deployment) for equivalent HTTP calls.

## Run tests

Install the test extra if it is not already present, then run the contract and integration suites:

```bash
uv pip install --python .venv/bin/python -e '.[test]'
.venv/bin/pytest -q
```

Real microphone and model tests are opt-in and skip by default. Read [Contributing](contributing.md#tests-and-validation) before running hardware proofs.

## See also

- [Configuration](configuration.md) for runtime, flow, and local environment settings.
- [Architecture](architecture.md) for service ownership and local RTF measurements.
