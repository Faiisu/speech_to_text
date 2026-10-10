# 03. Per-feature frontend contribution contract

Status: wontfix
Blocked by: 01, 02

Legacy outcome: this Control Center registry/page contribution plan was superseded by the current React feature-folder architecture. Current placement rules are in [`docs/project-structure.md`](../../../docs/project-structure.md).

## Scope

Define and validate the reusable process for delivering a feature page and its test structure alongside each feature's backend implementation. Feature 01 is the first completed example of this contract.

## Acceptance checklist

- [ ] Document the page/API/test contribution required in every future feature spec and ticket set.
- [ ] Provide a feature page starter structure or written checklist that keeps page, API adapter, and UI/API tests under the feature module.
- [ ] Define completion criteria requiring a registered page and feature-specific interactive test flow for UI-testable features.
- [ ] Define how non-UI or hardware-dependent features expose status and proof without fabricated results.
- [ ] Feature 01 demonstrates the contract with catalog/load, clip, microphone, and process workflows.
- [ ] Simulated/injected and real-runtime/hardware evidence are labeled separately.

## Comments

See [the Feature 01 requirements](../../new-speech-to-text/spec.md) and the control-center contribution rules in [the console spec](../spec.md).
