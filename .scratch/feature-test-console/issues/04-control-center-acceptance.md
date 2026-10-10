# 04. System overview and end-to-end acceptance

Status: wontfix
Blocked by: 01, 02, 03

Legacy outcome: the former Control Center acceptance plan was superseded by the current React/FastAPI application. Current browser and service workflows are documented in [`docs/getting-started.md`](../../../docs/getting-started.md) and [`docs/architecture.md`](../../../docs/architecture.md).

## Scope

Verify the shared system overview and complete browser-to-Python-to-Feature 01 path. Document local development and show how future feature slices extend the control center in parallel with backend work. Record hardware evidence separately from automated UI integration tests.

## Acceptance checklist

- [ ] The overview presents actual service/model readiness, active sessions/inputs, and recent errors.
- [ ] Document installation, startup, and shutdown from the repository root.
- [ ] Prove catalog, model load, file transcription, microphone events, stop/flush, and cleanup through the browser.
- [ ] Demonstrate adding a registered feature page using its feature-owned module and API adapter without modifying the Feature 01 page.
- [ ] Record the rule that each subsequent feature ships its frontend page and test flow alongside its backend implementation.
- [ ] Confirm local service binds only to loopback and browser requests cannot change that default.
- [ ] Record one successful physical-microphone session with selected device/runtime and clean completion.
- [ ] Mark OpenVINO GPU, verified Thai accuracy, and target multi-microphone capacity as pending until their hardware criteria pass.
- [ ] Run existing Feature 01 tests separately from the UI suite.

## Comments

Manual hardware acceptance extends the evidence in [Feature 01 ticket 05](../../new-speech-to-text/issues/05-target-hardware-acceptance.md); it does not close target-only requirements by itself.
