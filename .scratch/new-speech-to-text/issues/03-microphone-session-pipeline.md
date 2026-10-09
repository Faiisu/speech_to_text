# 03. Microphone capture and session lifecycle

Status: ready-for-agent
Execution: completed
Blocked by: 01, 02

## Scope

Implement the callable microphone flow with real OS capture plus the documented source-factory seam. Capture callbacks feed feature-owned normalization, buffering, chunking, model scheduling, typed result events, and explicit stop/error lifecycle. Multiple same-process sessions may share one loaded model while retaining independent source settings.

## Acceptance checklist

- [x] Default input selection and stable explicit device-name resolution are handled by the real capture adapter; missing devices fail before session creation.
- [x] Capture callback sizes do not determine inference chunk sizes; microphone data is normalized and buffered by the feature.
- [x] A shared model handle serializes FIFO inference across sources and routes events to the matching source id.
- [x] Stop is idempotent, waits for source shutdown and accepted work, flushes partial audio, and emits exactly one terminal completion.
- [x] Capture failure and enqueue timeout are fatal only to the affected source; errors have stable codes and are followed by one failed completion.
- [x] Failed chunks are non-fatal and later chunks continue; stale queued/in-flight results from a failed source are discarded.
- [x] Session result queue and all event fields match the executable contract.
- [x] Existing Feature 01 microphone contract tests pass.

## Comments

The same-process injectable audio source is only an external hardware boundary. It must not implement system buffering or scheduling.

Verification: `.venv/bin/python -m pytest tests/feature_01 -q` (70 passed, 2 hardware skips). Parent-run real Mac capture proof is linked from the evidence section in `../spec.md`; it used an injected runtime and does not establish recognition quality.
