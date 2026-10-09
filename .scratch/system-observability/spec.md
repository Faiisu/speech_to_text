# System Observability — Retired

This specification is retired. Speech-to-Text no longer persists host, process, service, or Feature 01 measurements in TimescaleDB or presents them in Grafana. Historical tickets and evidence in this directory are retained as records of the implementation and verification that existed at the time; they do not describe the current deployment.

Feature 01 now creates a local per-chunk measurement for each chunk that reaches inference. Clip responses return the records, microphone sessions publish measurement events, and process-group results carry their measurements. The current contract is the [Feature 01 per-chunk performance measurement section](../new-speech-to-text/spec.md#per-chunk-performance-measurement). Current service configuration and operation are documented in [Configuration](../../docs/configuration.md) and [Deployment](../../docs/deployment.md).
