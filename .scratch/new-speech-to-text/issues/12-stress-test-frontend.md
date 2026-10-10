# 12. Stress-test frontend

Status: ready-for-agent
Execution: completed
Blocked by: 11 and backend stress-test API

## Scope

Implement the [frontend design](../spec.md#frontend-design) in EchoDesk. The [backend API specification](../../backend-transcription-api/spec.md#post-stress-tests) owns transport and lifecycle details.

## Acceptance checklist

- [x] Add the Stress test navigation entry and `/stress-tests` route.
- [x] Use backend defaults and support model/runtime/precision overrides with compatibility and readiness guidance.
- [x] Prevent starting while microphone activity or a local stress run is active; explain conflicts and link blocking microphone runs.
- [x] Display the real 1/2/4 trial progression for shared-model and per-input-model topology.
- [x] Show completed trial verdicts, response timing, RTF, memory, utilization, startup, queue state, and chunk evidence without fabricated measurements.
- [x] Restore the last run after refresh and handle unavailable history and expired event cursors.
- [x] Support narrow screens, keyboard use, text state labels, and reduced motion.
- [x] Verify the production build and focused browser flows without loading real speech models.

## Verification

On 2026-10-10, from `speech_to_text/frontend/`:

- `npm run build`: passed.
- Focused Capacity lab browser suite: 5 tests passed.
- `npm run test:e2e`: 15 tests passed, including 10 existing lifecycle scenarios.
- Desktop and 390px mobile screenshots inspected; mobile checks cover document overflow and navigation placement.
- `git diff --check`: passed.

Capacity lab scenarios use mocked HTTP responses. These results prove frontend behavior and regression coverage, not real model or hardware capacity.
