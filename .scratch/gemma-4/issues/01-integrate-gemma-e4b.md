# Integrate Gemma 4 E4B as an optional transcription model

Status: wontfix

## Scope

Implement the feature described in [spec.md](../spec.md): make `google/gemma-4-E4B-it` available through the existing model catalog, preparation and model selection flow, and callable transcription pipeline. Preserve Whisper defaults and behavior. Do not modify the archived PoC.

## Acceptance criteria

- Fulfill the functional requirements and acceptance criteria in [spec.md](../spec.md).
- Follow root `AGENTS.md`, especially the project structure, documentation SSOT, and `uv` rules.
- Keep code and documentation in English; preserve Thai as user-facing application data.
- Do not add or run tests unless the user explicitly asks; summarize any limits this puts on verification.

## Comments

- 2026-10-27: Initial implementation issue created from the request to use Gemma 4 E4B in this project. Integration is additive; existing default remains unchanged.
- 2026-10-27: Deployment follow-up requested: include the Transformers runtime in the production Docker image and make the existing frontend-to-backend model preparation and inference flow available after deploy.
- 2026-10-27: Deployment boundary revised to use a separate optional Gemma Compose flavor. The existing production/UBX OpenVINO deployment remains unchanged; the Gemma flavor uses Transformers in-process, persists model/application data, includes CTranslate2 after confirming dependency resolution, and omits OpenVINO due dependency incompatibility.

## Legacy outcome

Implemented the additive Gemma 4 E4B local Transformers path, managed source preparation, catalog/runtime selection, and optional Gemma Compose deployment. Existing Whisper defaults and the standard OpenVINO deployment remain unchanged. No tests were run by instruction. Real model inference and target-hardware memory/latency remain unverified.
