# 11. File-replay stress workflow across model topologies

Status: ready-for-agent
Execution: completed
Blocked by: 01, 02, 03, 04

## Scope

Add a callable stress workflow that replays the fixed PCM WAV input at audio pace through the existing process-topology boundary. Run independent cold-start trials for 1, 2, and 4 concurrent workflows in both shared-model and per-input-model modes. Keep model deployment and input processing inside Feature 01; put matrix orchestration and per-topology reporting behind a public workflow interface.

The authoritative configuration, workload, metrics, preflight, and verdict contract is the [Feature 01 specification](../spec.md#file-replay-stress-workflow).

## Acceptance checklist

- [x] The original M4A is converted to `audio/test-audio.wav` as PCM16, 16 kHz, mono WAV, and replaced only after the converted file is valid and has the same audio duration.
- [x] A file-backed source emits normalized audio at real-time pace and can be used by the existing process topology without bypassing chunking, queueing, inference, events, or cleanup.
- [x] Two complete loops of the fixed clip feed each concurrent workflow continuously; chunks may cross the loop boundary and the final partial chunk is flushed after loop two.
- [x] A callable workflow runs isolated cold-start trials at 1, 2, and 4 concurrent workflows for shared-model and per-input-model topologies, starting each level together after model readiness and cleaning up before the next level.
- [x] Model/runtime/precision use Feature 01 defaults and accept explicit overrides; each result records the effective configuration and hardware/runtime identity.
- [x] Each chunk retains response timing, queue wait, inference time, audio duration, RTF, status, source identity, and sequence. Per-trial summaries include elapsed-time p50/p95/maximum, RTF evidence, startup time, queue/drain state, dropped/failed chunks, memory, and capacity verdict.
- [x] Per-input-model level 1 establishes measured process-memory footprint. Levels 2 and 4 are started only when a measurable projection leaves at least 25% of physical memory available; otherwise that level is reported unavailable without loading the extra models.
- [x] Results are grouped into separate shared-model and per-input-model JSON reports; no unavailable or unexecuted result is fabricated as a pass.
- [x] A clean trial passes only when terminal completion is clean, there are no inference errors or dropped chunks, no queued work remains after drain, and model utilization is at or below 1.05.

## Non-goals

- This workflow does not replace the physical microphone capacity command or claim that file replay proves microphone hardware capacity.
- It does not score transcript quality or calculate CER; this fixed clip is a repeatable load source, not a verified reference transcript.
- It does not add persistent telemetry or a dashboard.

Verification: `python3 -m compileall -q speech_to_text/features/model_deployment speech_to_text/workflows/file_replay_stress` and `git diff --check` pass. The hardware stress matrix was not executed as part of implementation.
