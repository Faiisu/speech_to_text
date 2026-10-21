# Exclude VAD no-speech skips from run RTF aggregates

Status: wontfix

Legacy outcome: Implemented the exclusion in the shared RunRecord aggregation method and passed measurement status/reason from both clip and live paths. VAD-only skips retain events/latest RTF, while minimum/average/maximum/count exclude them; inferred chunks and valid failed-inference measurements retain previous timing and eligibility.

## Scope

Apply the [run RTF aggregation contract](../spec.md#get-transcriptionsworkflow_id) at the shared backend aggregation boundary for both finite clips and live workflows. The parent coordinates documentation/review and a dedicated feature-development sub-agent implements the narrow change.

## Acceptance

- Measurements with both `status: "skipped"` and `skip_reason: "no_speech"` do not affect minimum, average, maximum, or count.
- Per-chunk events, latest RTF, and detector timing stay available; inferred chunks still include enabled VAD cost.
- Normal and failed-inference numeric measurements retain their existing eligibility and numeric validation.
- Mixed runs expose aggregates from eligible chunks only; all-VAD-skipped runs expose null aggregates and zero count.
- The same API results feed workflow detail, overview, and Model tuning; focused clip/live regressions pass.

## Comments

2026-10-22: Assigned to a `gpt-6-luna` feature-development sub-agent with high reasoning. No schema, dependency, or frontend calculation change is required.


2026-10-22 parent verification: `uv run --no-sync python -m pytest tests/backend -q --import-mode=importlib --disable-warnings --tb=short` passed 203 tests with 4 optional MQTT skips. Focused Chromium checks passed 3 tests covering real Model tuning VAD-skipped trials, retained run summaries, and the dashboard. The tuning regression verifies null aggregates/zero count and retained latest RTF for all-skipped trials. `git diff --check` passed. No model, VAD inference, capacity scoring, schema, or dependency changes were made.
