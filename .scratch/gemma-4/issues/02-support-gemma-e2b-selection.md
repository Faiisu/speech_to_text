# Add Gemma 4 E2B alongside E4B

Status: wontfix

## Scope

Implement the updated [Gemma 4 contract](../spec.md) so users can prepare and select `google/gemma-4-E2B-it` or the existing `google/gemma-4-E4B-it` through the current model-management and transcription flow.

## Acceptance criteria

- Add local key `gemma-4-e2b` for `google/gemma-4-E2B-it` and preserve `gemma-4-e4b` unchanged.
- Both models are independently discoverable, downloadable, runtime-checked, selected, and loaded through the Transformers adapter.
- E2B advertises and accepts BF16 only; E4B retains BF16 and float32. The optional Gemma Compose deployment defaults to E2B; E4B remains selectable.
- Preserve safe local-only loading, existing audio prompt/language handling, and the 30-second audio limit.
- Update existing authoritative configuration, getting-started, and deployment documentation to cover both models and E2B's 16 GB memory limitation.
- Do not change Whisper behavior or OpenVINO/UBX defaults. Do not edit `legacies-poc/`.
- Follow root `AGENTS.md`, including `uv`-only Python tooling and no test runs unless requested. Run focused static checks such as `git diff --check` and syntax/config validation where relevant.

## Implementation notes

- Runtime and model-management feature code owns repository compatibility, local keys, supported precision, and adapter loading.
- Existing model-management UI/API should provide selection without adding a parallel flow.
- Do not add E2B float32 as an option: it is likely to exceed the target 16 GB host memory budget once runtime overhead is included.

## Legacy outcome

Added Gemma 4 E2B to the existing catalog, model preparation, precision validation, and Transformers transcription flow alongside E4B. E2B exposes BF16 only; E4B retains BF16 and float32. At implementation time the Gemma Compose default was E4B; it now defaults to E2B, while E4B remains selectable. Updated the existing feature and deployment documentation. Static syntax and Compose configuration checks passed; no tests were run by instruction. Inference and memory use on the UBX-330M remain unverified.
