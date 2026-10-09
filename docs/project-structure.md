[← Back to README](../README.md)

# Project Structure

This document is the source of truth for where project files belong. Follow it whenever adding, moving, renaming, or removing project files and folders. Keep implementation code, tests, documentation, and local assets in their assigned areas.

## Repository layout

```text
.
├── .scratch/                   # Feature specifications, issues, and task evidence
├── audio/                      # Audio inputs and reference samples
├── docs/                       # Maintained project documentation
│   └── agents/                  # Instructions for repository workflows
├── legacies-poc/               # Archived proof of concept; isolated from new code
├── models/                     # Local model artifacts, not application source
├── scripts/                    # Repository and deployment utility scripts
├── speech_to_text/             # Installable Python application package
│   ├── backend/                # Inbound HTTP application boundary
│   │   └── http/
│   │       ├── routes/
│   │       └── schemas/
│   ├── features/               # Callable feature modules
│   │   ├── model_deployment/
│   │   └── word_matching/
│   ├── integrations/
│   │   └── http_forwarder/
│   └── workflows/
│       └── transcribe_match_forward/
├── tests/                      # Automated tests organized by owner and boundary
├── AGENTS.md                   # Repository-wide agent instructions
├── .gitignore                  # Generated files excluded from version control
├── README.md                   # Project entry point
├── pyproject.toml              # Package metadata and Python dependencies
├── pytest.ini                  # Pytest configuration
└── requirements-test.txt        # Test dependency list
```

The backend and HTTP subpackages currently reserve package boundaries. The intended backend module layout is:

```text
speech_to_text/backend/
├── app.py                      # FastAPI app factory and process lifecycle
├── dependencies.py             # Construct and provide workflow dependencies
└── http/
    ├── router.py               # Combine and mount HTTP routes
    ├── routes/                  # Endpoint handlers grouped by resource or use case
    │   ├── health.py
    │   └── transcription.py
    └── schemas/                 # HTTP request and response models
        └── transcription.py
```

Add these modules when their behavior and HTTP contracts are specified. The HTTP layer validates transport input, calls workflows or feature interfaces, and maps results to responses. Feature logic stays under `features/`; workflow orchestration stays under `workflows/`; external-system adapters stay under `integrations/`.

## Placement rules

- Put a feature's implementation and public callable interface in `speech_to_text/features/<feature_name>/`. Export the supported interface from that package's `__init__.py`.
- Put orchestration that composes multiple features or integrations in `speech_to_text/workflows/<workflow_name>/`.
- Put code that communicates with an external service or device in `speech_to_text/integrations/<integration_name>/`.
- Put inbound HTTP app setup, route handlers, and HTTP-only schemas in `speech_to_text/backend/`. Keep domain decisions in callable features and workflows.
- Put tests under `tests/`, grouped by the feature or boundary they verify. Keep the established `tests/feature_01/` suite with Feature 01; add new feature suites under `tests/<feature_name>/` and workflow or integration suites under `tests/workflows/` or `tests/integrations/`.
- Put maintained documentation under `docs/`. Keep feature specifications, issue tracking, and task evidence under `.scratch/<feature_name>/` as described by the [issue tracker guide](agents/issue-tracker.md).
- Keep new application code outside `legacies-poc/`. Modify the archive only when a task explicitly targets legacy code.
- Keep downloaded or machine-specific model artifacts under `models/`, audio inputs under `audio/`, and reusable project commands under `scripts/`. Do not place these assets inside Python packages.
- Name modules after their responsibility. Put inbound HTTP route modules under `backend/http/routes/`; do not put HTTP endpoints in feature modules.

When a task introduces a lasting folder category or changes ownership boundaries, update this document in the same change. For documentation topics, first update their existing authoritative document and link to it rather than copying its content here.

## Package discovery

The installable package is discovered from `speech_to_text*` by the configuration in `pyproject.toml`. New application Python packages belong under `speech_to_text/`; tests, docs, scripts, and local assets remain outside that package.
