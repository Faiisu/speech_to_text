# 05. System observation sampler and API

Status: wontfix
Blocked by: none
Execution: proposal-not-implemented; intermediate-telemetry-successor-retired

## Decision

This proposal was not carried into the current application. A later database/Grafana telemetry implementation was itself retired. The current application exposes local per-chunk measurements through Feature 01 and the transcription API; it has no system-observation API or historical telemetry store. See the [current measurement contract](../../new-speech-to-text/spec.md#per-chunk-performance-measurement).

The current source for local measurements is the [Feature 01 contract](../../new-speech-to-text/spec.md#per-chunk-performance-measurement). The retired [System Observability record](../../system-observability/spec.md) documents the intermediate implementation only.
