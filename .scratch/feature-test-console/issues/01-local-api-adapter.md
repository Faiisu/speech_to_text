# 01. Control center foundation and local API adapter

Status: ready-for-human
Blocked by: none
Execution: complete

## Scope

Add a FastAPI service and shared control-center shell. Serve native HTML/CSS/JavaScript modules and invoke feature APIs in-process. Define a typed feature contribution/registry contract and request/response/event schemas that support Feature 01 and later feature-owned adapters.

## Acceptance checklist

- [x] The service binds to loopback only by default and rejects unsupported remote binding configuration.
- [x] Add FastAPI and its ASGI server as an optional `control-center` dependency; static frontend files load from the same service origin.
- [x] A documented local command starts the control center from the repository root.
- [x] The API adapter can register feature-owned routes/operations without moving feature logic into shared routes.
- [x] The Feature 01 adapter exposes catalog/readiness, model load/close, clip transcription, microphone start/stop, process-group start/stop, and capacity measurement.
- [x] The shared shell includes feature navigation, service connection state, a system overview, and shared event output.
- [x] Feature modules provide a typed registry entry with their page module and API adapter; adding a page does not require editing a monolithic feature view.
- [x] Uploaded WAV data is validated through Feature 01 and temporary data is not retained after the request.
- [x] Microphone and process events reach the browser as ordered typed events with source ID and sequence.
- [x] Model handles and sessions have explicit ownership and cleanup on stop/service shutdown.
- [x] API errors preserve actionable configuration, audio, model-load, and lifecycle information.
- [x] Integration tests call the real feature implementation with only runtime/audio boundaries injected.
- [x] Framework and transport choices are recorded in the [console spec](../spec.md#local-service-api-and-run-command).
- [x] Integration tests prove a registered feature appears in navigation and unregistered features are not exposed.
- [x] Service cleanup closes feature-owned handles and sessions through the app lifespan.

## Comments

Contract source: [console spec](../spec.md) and [Feature 01 spec](../../new-speech-to-text/spec.md). Do not move feature validation logic into HTTP routes.

Implementation: FastAPI + uvicorn optional `control-center` extra, native same-origin modules, explicit `FeatureRegistry`, and feature-owned `feature_01_control` router. Each contribution registers its module, page template, stylesheet, and implementation/verification status. API paths, loopback command, and boundary-injection policy are recorded in the [console spec](../spec.md#local-service-api-and-run-command). Start with `python -m speech_to_text.control_center`; remote host values are rejected. TestClient integration exercises real Feature 01 catalog, model lifecycle, WAV transcription, microphone and process event streams, capacity measurement reporting, unregistered features, a second contribution page seam, errors, and app shutdown using injected runtime/capture boundaries. Hardware and real-runtime proof remain separate.
