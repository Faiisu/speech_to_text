# Configure and compare speech gate settings

Status: wontfix

Legacy outcome: Implemented Profile and Model tuning speech-gate controls, persistence/duplication, effective snapshots, and detector-only skip timing displays. Existing recording and isolated tuning behavior remains intact.

## Scope

Implement [speech gate trials](../spec.md#speech-gate-trials), profile editor controls, and effective run display through a dedicated feature-development sub-agent. Reuse the existing UI and preserve in-progress tuning process-isolation changes.

## Acceptance

- VAD toggle, probability threshold, minimum speech duration, and adjustable RMS in Profile and Model tuning.
- Profile settings round-trip and duplicate; old profiles default to VAD disabled.
- Tuning submits the complete VAD object and retains effective settings for each trial.
- No-speech skipped rows show real processing/RTF, including VAD; unchanged recordings allow repeat comparisons.
- Frontend build and focused browser regressions pass.

## Comments

2026-10-22: Assigned to a `gpt-6-luna` feature-development sub-agent with high reasoning. Parent owns documentation and integration verification.


2026-10-22: Parent verification: `npm run build` passed. `PYTHON="uv run --no-sync python" npm run test:e2e -- e2e/model_tuning.spec.ts e2e/profile_run_lifecycle.spec.ts e2e/live_audio.spec.ts` passed all 28 tests. Desktop and mobile control screenshots were visually inspected; VAD checkbox and settings are visible. Reference to temporary screenshot paths is intentionally omitted because they are not durable project artifacts.
