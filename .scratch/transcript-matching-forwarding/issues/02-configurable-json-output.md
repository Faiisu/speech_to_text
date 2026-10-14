# 02. Configure workflow JSON delivery

Status: wontfix

Legacy outcome: Configurable terminal JSON output is implemented. Workflow output receiver tests passed (24 tests), including HTTP methods, UTF-8, TLS CA/mTLS, auth, retry/error behavior, and egress validation. Proxy, payload mapping, and durable replay remain explicitly outside this release.

## Scope

Implement the [configurable JSON output contract](../spec.md#configurable-json-output) using the existing workflow-owned sender. Preserve the canonical completed-source record and no-forwarder behavior. Backend integration is tracked by [backend ticket 03](../../backend-transcription-api/issues/03-output-destinations.md).

## Acceptance checklist

- [x] POST, PUT, and PATCH send UTF-8 JSON with stable event identity and bytes on retry.
- [x] Custom headers and secret-reference authentication are validated; credentials are never returned to callers.
- [x] HTTP status, timeout, Retry-After, redirect, and TLS failures follow the output contract.
- [x] Approved destination origins and networks are enforced at the actual connection boundary.
- [x] Receiver tests and existing workflow tests pass.

## Comments

2026-10-14: implementation assigned to a dedicated feature-development sub-agent. The parent reviews integration and verifies the backend gate before running `scripts/run_macos.sh` and starting frontend development.
