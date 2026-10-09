# 01. Model catalog, configuration, and runtime handles

Status: ready-for-agent
Execution: completed
Blocked by: none

## Scope

Implement the public `speech_to_text.features.model_deployment` package foundation: typed errors, validated immutable model configuration, local catalog discovery, runtime compatibility/readiness metadata, process-owned model handles, lazy production adapters, and optional runtime dependency declarations. OpenVINO GPU with `turbo`/source precision is the default; CTranslate2 remains explicitly selectable.

## Acceptance checklist

- [x] `list_available_models` dynamically scans known candidates and local sources without downloading or network access; explicit registries isolate discovery and duplicate repositories merge.
- [x] Catalog results include the documented model/runtime fields, readiness reasons, and the `turbo` candidate.
- [x] `load_model` validates all accepted fields/options before invoking one runtime factory, snapshots configuration, and raises typed actionable errors.
- [x] Production runtime imports are lazy and fail with an actionable `ModelLoadError` when dependencies, model assets, or requested device are unavailable.
- [x] A handle exposes model/runtime/precision/state, enforces owning-process use, reuses one runtime, and closes idempotently.
- [x] Packaging/install metadata exposes optional OpenVINO and CTranslate2 runtime extras without forcing heavyweight runtimes into deterministic tests.
- [x] Existing Feature 01 model-contract tests pass, including spawned processes loading independent handles.

## Comments

Execution is tracked separately from canonical triage status. Contract tests are software evidence; GPU installation and inference remain hardware acceptance work.

Verification: `.venv/bin/python -m pytest tests/feature_01 -q` (70 passed, 2 hardware skips); production adapter mock regressions verify OpenVINO precision forwarding and source conversion cache reuse. Real OpenVINO GPU proof remains ticket 05.
