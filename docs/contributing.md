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

Run the complete software gate on macOS or the UBX-330M (Ubuntu/Linux) from the repository root:

```bash
./scripts/run_full_tests.sh
```

Requirements: `uv`, Node.js/npm, and a running Docker daemon with Docker Compose 2.23.1 or newer. The script installs locked Python dependencies, selects CTranslate2 on macOS or OpenVINO on Linux, starts an isolated MQTT broker on an available loopback port, runs all Python tests, builds the frontend, and runs all Chromium E2E tests including real MQTT publication. It removes only its generated broker project and volume on exit, preserves the failure exit code, and leaves the application broker running.

On a fresh headless Linux host, install Chromium system libraries with:

```bash
FULL_TEST_INSTALL_BROWSER_DEPS=1 ./scripts/run_full_tests.sh
```

Playwright's system dependency installation may require sudo on Linux. `FULL_TEST_RUNTIME_EXTRA=ctranslate2` or `openvino` overrides the platform default. Physical microphone/model proofs remain opt-in and need the reference inputs below; software suite success does not establish target hardware readiness.

Run all default suites from the repository root:

```bash
.venv/bin/pytest -q
```

The suites are organized by boundary:

- `tests/feature_01/` verifies model, clip, microphone, runtime, and process-topology contracts.
- `tests/word_matching/` verifies Thai literal substring matching and configuration validation.
- `tests/workflows/` verifies transcription and matching orchestration through workflow interfaces.
- `tests/backend/` verifies HTTP requests, run monitoring, microphone discovery, and lifecycle responses through the FastAPI app.
- `speech_to_text/frontend/e2e/` drives the browser through profile creation, workflow output monitoring, and stop against the backend API. Its deterministic backend fixture is in `tests/frontend_e2e/`. Capacity lab browser scenarios use mocked HTTP responses to exercise matrix rendering, configuration, polling, and recovery.

Run the frontend browser flow from `speech_to_text/frontend/` after installing the frontend dependencies and Chromium once:

```bash
npm install
npx playwright install chromium
npm run test:e2e
```

The microphone E2E backend uses a deterministic fake microphone workflow, so it does not require a physical microphone or a speech model. Those scenarios run the frontend against the real FastAPI routes and run registry. Mocked Capacity lab browser scenarios prove frontend behavior; backend and hardware capacity proof remain separate.

Tests marked `hardware` are skipped unless explicitly enabled. Real model proof requires the reference audio and verified transcript settings described in `tests/feature_01/test_hardware_proof.py`. A physical microphone proof also requires a connected, named input device. Keep injected-boundary test results distinct from target hardware evidence.

Clip, microphone, and process-group contracts include per-chunk measurement outputs. There is no separate database deployment smoke check; see the [Feature 01 measurement contract](../.scratch/new-speech-to-text/spec.md#per-chunk-performance-measurement).

## Documentation and feature changes

Follow the repository-wide [documentation SSOT rules](../AGENTS.md#documentation-single-source-of-truth-ssot) when changing documentation. They define the scan, update, and cross-link process. Write code and documentation in English; application data may retain its intended language.

For a feature change, update its existing specification or ticket before expanding its interface. Keep implementation modules under the owning feature folder, put shared behavior in shared modules, and cover the public input/output/error contract with tests. The backend boundary and its responsibilities are documented in [Architecture](architecture.md#backend-boundary); transport handlers should call feature/workflow interfaces rather than own domain logic.

## See also

- [Getting Started](getting-started.md) for environment setup.
- [Architecture](architecture.md) for module boundaries.
- [Agent domain guide](agents/domain.md) and [triage labels](agents/triage-labels.md).
