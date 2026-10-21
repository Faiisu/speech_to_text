# Runtime-independent VAD speech gate

Status: wontfix

Legacy outcome: Implemented Silero VAD for clip, local live, and both process topologies with strict per-flow configuration, packaged weights, isolated state, explicit failures, and VAD-inclusive RTF. Real local noise/speech integration passed; target-hardware acoustic accuracy and capacity remain unverified.

## Scope

Implement the [VAD contract](../spec.md#voice-activity-detection) through a dedicated feature-development sub-agent. Preserve adjustable RMS gating and support both model runtimes, finite clips, local capture, both process topologies, and final partial chunks. Include VAD work in RTF even when a chunk is rejected without inference.

## Acceptance

- Strict normalized configuration and backward-compatible disabled defaults.
- Packaged Silero weights, bounded process-local reuse, isolated recurrent state, explicit detector failures.
- Noise skips the model; speech keeps original processed frames and duration.
- VAD time survives queue/IPC transport and is included once in RTF.
- Deterministic execution-path tests and real Silero noise/speech smoke.

## Comments

2026-10-22: Assigned to a `gpt-6-luna` feature-development sub-agent with high reasoning. Parent owns specification updates, review, and integrated verification.


2026-10-22 parent integration evidence: the full Python suite passed with 507 tests and 9 optional skips before the final queue-failure review. Frontend build and 28 browser lifecycle/tuning/live-audio tests passed. Real Silero rejected seeded random noise and a 440 Hz tone while retaining reference speech through the callable feature with an injected ASR boundary.

A separate real runtime smoke loaded local `turbo` / `ctranslate2` / `int8` weights with RMS threshold zero and VAD enabled. A five-second noise sample returned an empty transcript and one `skipped` / `no_speech` measurement: processing 0.591069 s, RTF 0.118214. The first five seconds of `audio/test-audio.wav` returned a non-empty Thai transcript with a `completed` measurement: processing 7.146260 s, RTF 1.429254. Both used the original five-second denominator and included VAD. The first detector call includes lazy initialization. This Mac CPU sample proves the real integration; it is not a recognition-accuracy or real-time-capacity acceptance result for UBX-330M/OpenVINO.

2026-10-22 final review: shared detector failures retire queued work before terminal completion. VAD skip queue pressure follows existing drop/fail policies without publishing out-of-order measurements. The final VAD/process regression run passed all 25 tests after these corrections. `git diff --check` passed.
