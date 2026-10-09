# 04. Process-owned inference topologies and capacity tool

Status: ready-for-agent
Execution: completed
Blocked by: 01, 02, 03

## Scope

Provide explicit process ownership and routing for both deployment topologies: one shared model process fed by per-microphone capture/chunk producers over bounded FIFO IPC, and one model-owning process per microphone. Add a runnable capacity benchmark that exercises both paths at microphone pace and reports queue, latency, real-time factor, memory, and source identity telemetry.

Each source may override the common flow configuration through a `flow_configs` list aligned with the device list; the benchmark intentionally uses a common configuration for comparable runs.

## Acceptance checklist

- [x] A `ModelHandle` is rejected when used from a process other than the process that loaded it.
- [x] Shared-model IPC sends normalized chunks with source id and sequence and returns tagged transcript/error results in FIFO order.
- [x] Queue capacity and finite enqueue timeout are configurable; saturation fails only that source, discards its queued work, and ignores already-running late results.
- [x] Per-input topology loads and closes a distinct model in each owning process; no handle is serialized or inherited as a cross-process API.
- [x] Runnable benchmark covers both topologies at microphone pace and records model load time, total memory, queue growth, dropped chunks, per-chunk latency/RTF, and source identity.
- [x] Benchmark labels unavailable hardware/runtime prerequisites as unavailable and never reports unexecuted target measurements as passing.
- [x] Add deterministic regression tests for process ownership, routing, ordering, and timeout isolation where not covered by existing tests.
- [x] Reject non-finite benchmark durations/timeouts and malformed device collections before process startup; return nonzero CLI status for unavailable, failed, or inconclusive evidence.
- [x] Benchmark failure cleanup force-terminates and joins owned capture/model processes, then stops the dispatcher and manager.

## Comments

This ticket implements software topology and measurement tooling. Physical target capacity evidence is tracked separately in ticket 05.

Verification: `.venv/bin/python -m pytest tests/feature_01 -q` (70 passed, 2 hardware skips); `.venv/bin/python -m speech_to_text.features.model_deployment.capacity --help` succeeds. CLI status, finite argument checks, device collection validation, and abort cleanup have deterministic regressions. Parent-run named Mac microphone IPC proof for both topologies is in `../evidence/local-ipc-microphone-smoke.json`; it uses an injected runtime and is partial physical IPC/capture evidence only. Hardware throughput/capacity remains ticket 05.
