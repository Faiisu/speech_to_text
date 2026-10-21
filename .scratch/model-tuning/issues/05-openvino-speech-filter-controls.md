# Tune OpenVINO decoder speech filtering

Status: wontfix

Legacy outcome: Implemented Profile and Model tuning enable/threshold controls, custom validation, profile round-trip/duplication, immutable trial snapshots, and disabled dormant controls for CTranslate2. Desktop/mobile visual review passed.

## Scope

Implement the [OpenVINO filter trials contract](../spec.md#openvino-speech-filter-trials) through a dedicated feature-development sub-agent. Add controls to Profile and Model tuning within their existing decoder forms.

## Acceptance

- Enable switch and both thresholds validate correctly and round-trip through saved profiles.
- CTranslate2 controls are disabled with an explanation and preserve dormant values.
- Trial submission and effective snapshots retain custom values after later form/runtime changes.
- Build, focused browser checks, and desktop/mobile visual review pass.

## Comments

2026-10-22: Assigned to a `gpt-6-luna` feature-development sub-agent with high reasoning. Parent owns documentation and integration verification.


2026-10-22 parent integration verification: `uv run --no-sync python -m pytest -q --import-mode=importlib --disable-warnings --tb=short` passed 554 tests with 9 optional skips. Frontend production build passed. `PYTHON="uv run --no-sync python" npm run test:e2e -- e2e/model_tuning.spec.ts e2e/profile_run_lifecycle.spec.ts e2e/live_audio.spec.ts` passed all 29 browser checks. Final `git diff --check` passed. Real OpenVINO CPU evidence and the observed default-threshold limitation are recorded in [Feature 01 issue 15](../../new-speech-to-text/issues/15-openvino-speech-filter.md). OpenVINO GPU/UBX and optional hardware/MQTT checks were not exercised.
