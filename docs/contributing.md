[← Back to README](../README.md)

# Contributing

Develop each feature against its documented callable contract, with implementation and tests organized by feature. Repository-wide requirements are maintained in [`AGENTS.md`](../AGENTS.md); issue files and specifications follow the [local issue tracker guide](agents/issue-tracker.md).

## Development setup

Create a local environment and install the test dependencies:

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[test]'
```

Install optional model or microphone packages only when needed for the work. See [Getting Started](getting-started.md#install-optional-runtimes-and-microphone-support).

## Tests and validation

Run all default suites from the repository root:

```bash
.venv/bin/pytest -q
```

The suites are organized by boundary:

- `tests/feature_01/` verifies model, clip, microphone, runtime, and process-topology contracts.
- `tests/word_matching/` verifies Thai literal substring matching and configuration validation.
- `tests/workflows/` verifies transcription and matching orchestration through workflow interfaces.
- `tests/backend/` verifies HTTP requests, run monitoring, microphone discovery, and lifecycle responses through the FastAPI app.
- `speech_to_text/frontend/e2e/` drives the browser through profile creation, workflow output monitoring, and stop against the backend API. Its deterministic backend fixture is in `tests/frontend_e2e/`.

Run the frontend browser flow from `speech_to_text/frontend/` after installing the frontend dependencies and Chromium once:

```bash
npm install
npx playwright install chromium
npm run test:e2e
```

The E2E backend uses a deterministic fake microphone workflow, so it does not require a physical microphone or a speech model. It still runs the frontend against the real FastAPI routes and run registry.

Tests marked `hardware` are skipped unless explicitly enabled. Real model proof requires the reference audio and verified transcript settings described in `tests/feature_01/test_hardware_proof.py`. A physical microphone proof also requires a connected, named input device. Keep injected-boundary test results distinct from target hardware evidence.

Clip, microphone, and process-group contracts include per-chunk measurement outputs. There is no separate database deployment smoke check; see the [Feature 01 measurement contract](../.scratch/new-speech-to-text/spec.md#per-chunk-performance-measurement).

## Documentation and feature changes

Before writing documentation, identify the topic, scan existing docs and `.scratch/` specifications, then update the authoritative file when one exists. Add a new page only for material without an existing source of truth, and link other pages to that source. Write code and documentation in English; application data may retain its intended language.

For a feature change, update its existing specification or ticket before expanding its interface. Keep implementation modules under the owning feature folder, put shared behavior in shared modules, and cover the public input/output/error contract with tests. The backend boundary and its responsibilities are documented in [Architecture](architecture.md#backend-boundary); transport handlers should call feature/workflow interfaces rather than own domain logic.

## See also

- [Getting Started](getting-started.md) for environment setup.
- [Architecture](architecture.md) for module boundaries.
- [Agent domain guide](agents/domain.md) and [triage labels](agents/triage-labels.md).
