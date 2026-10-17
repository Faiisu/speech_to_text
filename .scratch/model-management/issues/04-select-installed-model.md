# Prepare another runtime from the local model catalog

Status: wontfix
Legacy outcome: Implemented catalog selection with persisted source identity and verified runtime/precision preparation controls.

## Scope

Implement the [installed-model selection contract](../spec.md#selecting-an-installed-model): expose persisted repository/revision provenance and let users select a catalog model to prepare another runtime/precision with its original local key. Preserve existing artifacts, token handling, and active workflows.

## Validation

Cover managed source and artifact metadata, installer metadata, unknown repository, pinned revision selection, precision conversion requests, and stale inspection responses. Run focused Python/browser checks and the frontend build.

## Comments

Frontend production build and all nine Models browser tests passed. Focused feature/workflow/API tests passed (16 tests), and parent integration checks for model contracts, fallback roots, catalog API, and transcription workflows passed (60 tests). Coverage includes pinned revisions, source-only selection, unknown provenance, int8-to-float32 preparation requests, and stale inspection responses. No real model download/conversion was repeated for this UI/metadata change; existing preparation callables remain unchanged.
