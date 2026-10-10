[← Back to README](../README.md)

# Project Structure

This document is the source of truth for where project files belong. Follow it whenever adding, moving, renaming, or removing project files and folders. Keep implementation code, tests, documentation, and local assets in their assigned areas.

## Repository layout

```text
.
├── .scratch/                   # Feature specifications, issues, and task evidence
├── audio/                      # Audio inputs and reference samples
├── data/                       # Ignored persistent runtime and generated deployment state
├── docs/                       # Maintained project documentation
│   └── agents/                  # Instructions for repository workflows
├── legacies-poc/               # Archived proof of concept; isolated from new code
├── models/                     # Local model artifacts, not application source
├── scripts/                    # Repository and deployment utility scripts
├── .github/workflows/          # CI and release automation
├── speech_to_text/             # Installable Python application package
│   ├── frontend/               # Frontend source, assets, and browser E2E tests
│   │   └── e2e/
│   ├── backend/                # Inbound API and server lifecycle
│   │   └── api/
│   │       ├── routes/
│   │       └── schemas/
│   ├── features/               # Callable feature modules
│   │   ├── model_deployment/
│   │   └── word_matching/
│   └── workflows/              # Use cases and their output behavior
│       ├── transcribe_match_forward/
│       └── file_replay_stress/
├── tests/                      # Automated tests organized by owner and boundary
│   ├── feature_01/
│   ├── word_matching/
│   ├── backend/
│   ├── workflows/
│   └── frontend_e2e/            # Test-only backend fixtures for browser E2E tests
├── AGENTS.md                   # Repository-wide agent instructions
├── .gitignore                  # Generated files excluded from version control
├── README.md                   # Project entry point
├── Dockerfile                  # Versioned container build definition
├── compose.yaml                # Local and host Compose deployment
├── pyproject.toml              # Package metadata and Python dependencies
├── uv.lock                     # Locked Python dependency resolution
├── pytest.ini                  # Pytest configuration
└── requirements-test.txt        # Test dependency list
```

The frontend owns UI code in `speech_to_text/frontend/src/`. Shared navigation, API transport, and UI primitives live under `src/shared/`; page behavior lives under `src/features/`:

```text
speech_to_text/frontend/src/
├── shared/
└── features/
    ├── dashboard/
    ├── profiles/
    ├── runs/
    └── stress-tests/             # Capacity matrix controls, polling, and reports
```

The implemented backend module layout is:

```text
speech_to_text/backend/
├── app.py                      # FastAPI app factory and process lifecycle
├── dependencies.py             # Own workflow service, worker pool, and run registries
├── profile_store.py            # Persist reusable microphone profiles in SQLite
└── api/
    ├── router.py               # Combine and mount inbound API routes
    ├── microphone_lifecycle.py # Shared microphone start/stop lifecycle helpers
    ├── routes/                  # Endpoint handlers grouped by resource or use case
    │   ├── microphones.py
    │   ├── models.py
    │   ├── profiles.py
    │   ├── stress_tests.py
    │   └── transcription.py
    └── schemas/                 # API request and response models
        ├── stress_tests.py
        └── transcription.py
```

Frontend code calls the backend API. Backend routes validate requests and call workflow functions. A workflow calls features, receives their results, and owns the use case's output choices: it returns results to the backend and can send them to another backend or system. The backend maps returned results to frontend responses or streams. Feature logic stays under `features/`; use-case orchestration and delivery behavior stay under `workflows/`.

## Placement rules

- Put a feature's implementation and public callable interface in `speech_to_text/features/<feature_name>/`. Export the supported interface from that package's `__init__.py`.
- Keep each feature independent of other feature modules: a feature must not import or call another feature's functions. When a use case needs to call functions from multiple features, put that orchestration in `speech_to_text/workflows/<workflow_name>/` and have the workflow call each feature's public interface.
- Put each use case's orchestration and output delivery behavior in `speech_to_text/workflows/<workflow_name>/`. Keep output choices owned by that workflow.
- Keep code that sends a workflow's results to another backend or system inside that owning workflow. Do not create a top-level `integrations/` folder.
- Put inbound HTTP app setup, route handlers, and HTTP-only schemas in `speech_to_text/backend/`. Keep domain decisions in callable features and workflows.
- Put Python feature, backend, and workflow tests under `tests/`, grouped by the boundary they verify. Keep browser E2E specs under `speech_to_text/frontend/e2e/`, beside their frontend tooling; keep test-only Python backend fixtures under `tests/frontend_e2e/`.
- Put maintained documentation under `docs/`. Keep feature specifications, issue tracking, and task evidence under `.scratch/<feature_name>/` as described by the [issue tracker guide](agents/issue-tracker.md).
- Put container build assets at the repository root (`Dockerfile`, `.dockerignore`, and `compose.yaml`), model-export utilities under `scripts/`, and GitHub automation under `.github/workflows/`.
- Keep new application code outside `legacies-poc/`. Modify the archive only when a task explicitly targets legacy code.
- Keep downloaded or machine-specific model artifacts under `models/`, audio inputs under `audio/`, and reusable project commands under `scripts/`. Do not place these assets inside Python packages.
- Keep ignored persistent runtime data and generated deployment state under `data/`.
- Name modules after their responsibility. Put inbound API route modules under `backend/api/routes/`; do not put HTTP endpoints in feature or workflow modules.

When a task introduces a lasting folder category or changes ownership boundaries, update this document in the same change. For documentation topics, first update their existing authoritative document and link to it rather than copying its content here.

## Package discovery

The installable package is discovered from `speech_to_text*` by the configuration in `pyproject.toml`. New application Python packages belong under `speech_to_text/`; tests, docs, scripts, and local assets remain outside that package.
