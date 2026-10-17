# Delete local model assets from Models

Status: wontfix
Legacy outcome: Implemented guarded local model deletion with preview, explicit frontend confirmation, and catalog refresh.

## Scope

Implement the [local deletion contract](../spec.md#deleting-local-models): scoped file cleanup, usage/preparation/profile guards, preview and confirmation, and catalog refresh. Preserve unrelated data and active workflow behavior.

## Validation

Cover exact key matching, source/all-runtime cleanup, registry preservation, symlink/traversal rejection, implicit legacy/cache preservation, preparation and run-admission serialization, blockers, confirmation/cancellation/errors, and frontend build. Use temporary fixture files rather than real user assets.

## Comments

Python regression: 378 passed, 9 skipped. Final focused deletion/workflow/API checks: 30 passed. Frontend build and 13 Models browser scenarios passed. A real temporary-filesystem API proof confirmed exact cleanup, unrelated-model/registry preservation, and repeated-delete 404. User model files and shared Hub cache were not deleted. See the [verification record](../spec.md#verification-record-2026-10-17) for scope and limitations.
