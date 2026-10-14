# 03. Configure output destinations in the frontend

Status: wontfix

Legacy outcome: the destination page, optional profile selection/duplication, and separate run delivery display are implemented. The production frontend build and all 19 browser tests passed. The parent reviewed desktop/mobile receiver screenshots and verified the live destination page and API proxy after restarting scripts/run_macos.sh.

## Scope

Add the destination configuration page to the existing EchoDesk frontend. Use the [destination API contract](../../backend-transcription-api/spec.md#destination-endpoints) and [workflow destination settings](../spec.md#current-destination-interface) rather than duplicating configuration semantics.

## Acceptance checklist

- [x] Sidebar navigation opens a responsive destination list and create/edit form.
- [x] API callers can save, update, delete, and send synthetic test JSON with clear outcomes.
- [x] The form covers URL, method, non-secret headers, secret-reference auth, retry/timeouts, and supported TLS settings.
- [x] JSON preview matches the canonical outgoing contract; tests use saved configuration and synthetic data.
- [x] Profile create/edit/duplicate preserves optional destination selection.
- [x] Run Detail shows delivery independently from transcription.
- [x] Loading, validation, API failure, and referenced-destination delete errors are actionable.
- [x] Frontend build and browser tests pass, including a narrow viewport.

## Comments

2026-10-14: frontend work started only after the parent verified the backend gate (192 passed, two hardware tests skipped) and ran `scripts/run_macos.sh`. Both local servers responded successfully before feature implementation started. Assigned to a separate feature-development sub-agent using the repository's default model and reasoning effort.

Final verification: Python suite 196 passed, two opt-in hardware tests skipped; frontend `npm run build` passed; browser suite `npm run test:e2e` 19 passed. Receiver tests use controlled HTTP/HTTPS endpoints and injected transcription boundaries; production receiver compatibility and physical microphone accuracy are not claimed.
