# Speech-to-Text

The previous proof-of-concept codebase is archived in [`legacies-poc/`](./legacies-poc/). Its application code, tests, deployment scripts, project configuration, model files, audio clips, domain glossary, and ADRs remain together there.

See the [legacy README](./legacies-poc/README.md) for setup and operation. Run its commands from `legacies-poc/`.

The [feature verification audit](./.scratch/feature-verification/spec.md) records the existing system's behavior before building the new program. Repository-wide agent rules and issue tracking remain at the repository root.

The existing local `.venv/` remains at the repository root. To run the archived tests with that environment:

```bash
cd legacies-poc
../.venv/bin/python -m pytest -q
```

For an independent legacy environment, run `uv sync --group dev` from `legacies-poc/` before using the commands in the legacy README.
