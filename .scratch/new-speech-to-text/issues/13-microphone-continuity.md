# 13. Keep microphone capture running through recoverable pressure

Status: wontfix

Legacy outcome: Microphone continuity is implemented and its software regression suite passes. Physical UBX-330M disconnect/reconnect and sustained capture remain part of the target hardware gate.

## Scope

Implement the [Feature 01 session and overload contract](../spec.md#session-event-contract). Preserve callable `overload_policy="fail"` behavior and add `drop` for real-time backend runs. Genuine capture failures remain terminal to the session and are reopened by the backend lifecycle.

## Acceptance checklist

- [x] Input overflow alone reports a non-fatal diagnostic and permits current and later usable audio.
- [x] Queue pressure in drop mode reports dropped audio without terminating capture or growing audio queues; chunk drops have a sequence and raw callback-frame drops have none.
- [x] Shared and process-isolated execution preserve accepted-result order and terminate cleanly despite dropped sequences.
- [x] Existing fail-mode contracts and explicit-stop behavior remain covered.
- [x] Unexpected input-process exit produces a fatal event and completion after pending output drains, allowing backend recovery.
- [x] Focused software regression tests pass; target UBX-330M disconnect/reconnect and sustained capture remain explicitly unverified.

## Comments

2026-10-16: Implementation assigned to the dedicated capture-continuity sub-agent. The parent reviews integration with backend recovery.

Verification: `uv run --no-project --with pytest --with numpy python -m pytest tests/feature_01 -q` — 85 passed, 2 hardware tests skipped. The suite includes both spawned topologies, input-overflow/fatal-notification races, queue-pressure recovery, explicit stop, and child-process termination.
