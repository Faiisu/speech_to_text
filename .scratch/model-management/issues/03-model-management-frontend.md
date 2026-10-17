# Frontend model management

Status: wontfix
Legacy outcome: Implemented and verified in the current application; no remaining implementation ticket work.

## Scope

Models navigation, inspection and source download/conversion, job polling, compatibility/precision feedback, and browser tests.

Implement the acceptance criteria in the [model-management specification](../spec.md) for this boundary.

## Validation

Record focused verification and remaining hardware/remote-service limitations before closing.

## Comments

Production frontend build and focused mocked browser tests passed. Browser fixtures cover compatible/incompatible repositories, runtime availability, jobs/download/conversion/failure, token clearing, default precision diagnostics, reload, stale inspection, and mobile layout. Final integration results are recorded in the feature specification.

The full browser integration gate passed 34 tests with one broker-dependent skip; a separate source-only regression verifies download requests omit conversion options and do not claim inference readiness.
