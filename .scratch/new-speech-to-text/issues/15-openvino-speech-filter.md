# Configure OpenVINO decoder speech filtering

Status: wontfix

Legacy outcome: Implemented the public normalization helper and optional per-flow decoder filter for OpenVINO CPU/GPU, including a scoped compatibility bridge for the locked Transformers/Optimum scorer, independent settings, and retained model-call RTF. Real OpenVINO CPU demonstrated threshold-controlled rejection; noise rejection and GPU capacity are not guaranteed.

## Scope

Implement the [OpenVINO speech-filter contract](../spec.md#openvino-speech-filtering) through a dedicated feature-development sub-agent. Reuse nested decoding options and the existing runtime adapter. Preserve disabled behavior, independent VAD, CTranslate2 defaults, and measured RTF.

## Acceptance

- Public normalization helper with complete detached defaults and strict ranges/types.
- Enabled OpenVINO uses timestamp decoding and both configured thresholds; disabled calls preserve prior behavior.
- Empty decoder output is successful processing with a retained measurement.
- Shared model generation defaults stay isolated; CPU and GPU use the same adapter behavior.
- Version-specific Optimum/Transformers argument compatibility is proven by a real OpenVINO CPU smoke, not solely stub tests.

## Comments

2026-10-22: Assigned to a `gpt-6-luna` feature-development sub-agent with high reasoning. Parent owns specification, review, and integration verification.


2026-10-22 real CPU verification: installed the existing locked OpenVINO extra (no dependency changes). OpenVINO 2026.4.1, Optimum Intel 2.2.0, and Transformers 4.57.6 exposed a real argument mismatch in Whisper no-speech scoring: the scorer passes `input_ids`, while Optimum requires `decoder_input_ids`. The adapter now provides a call-scoped argument bridge only while the filter is enabled, and restores the original method in `finally`. Regression checks cover both normal and failed generation restoration.

Using the local `turbo` source-precision artifact on OpenVINO CPU, the first two seconds of `audio/test-audio.wav` produced non-empty Thai text with default thresholds. The same speech with test-only boundary thresholds `(0, 0)` produced empty text. Both records had `status=completed`, no error, the original two-second denominator, and measured RTF including scoring/filter work. Boundary thresholds demonstrate filter execution; they are not recommended operating settings. This does not establish recognition accuracy or real-time capacity.

The initial real run also showed non-empty hallucinations on silent and seeded-noise samples at the default thresholds. This confirms the limitation: the joint decoder filter does not guarantee rejection of non-speech. Use VAD and evaluate thresholds against representative noise and quiet speech. OpenVINO GPU/UBX-330M was not exercised.

- `default-threshold-speech`: transcript 'เมื่อตอนนี้เล่นเกมจริง', processing 8.922464 s, RTF 4.461233, status `completed`.

- `forced-rejection`: transcript '', processing 12.635710 s, RTF 6.317856, status `completed`.


2026-10-22 parent integration verification: `uv run --no-sync python -m pytest -q --import-mode=importlib --disable-warnings --tb=short` passed 554 tests with 9 optional skips. Frontend production build passed. `PYTHON="uv run --no-sync python" npm run test:e2e -- e2e/model_tuning.spec.ts e2e/profile_run_lifecycle.spec.ts e2e/live_audio.spec.ts` passed all 29 browser checks. Final `git diff --check` passed. Real OpenVINO CPU evidence and the observed default-threshold limitation are recorded in this issue. OpenVINO GPU/UBX and optional hardware/MQTT checks were not exercised.
