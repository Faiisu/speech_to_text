# 03. Containerize the main service for Linux deployment

Status: wontfix
Execution: image-built; linux-deployed-and-verified
Blocked by: none

Legacy outcome: this image hosted the former Control Center and telemetry stack. That deployment was retired; the current versioned Docker application and operator workflow are documented in [`docs/deployment.md`](../../../docs/deployment.md).

## Scope

Build and run the former Control Center, model deployment, and system telemetry as one Linux container while retaining the loopback-only HTTP boundary and existing observability services. This is a historical deployment ticket; that specific container configuration and operator workflow have since been removed. Current local development setup is documented in [Getting Started](../../../docs/getting-started.md), and the current deployment is documented in [`docs/deployment.md`](../../../docs/deployment.md).

## Acceptance checklist

- [x] Ubuntu 24.04 / Python 3.12 image installs CPU-only PyTorch, OpenVINO, Control Center, telemetry, and microphone dependencies.
- [x] The image includes static UI assets, SQL migrations, and the Feature 01 spec served by the Control Center.
- [x] Compose shares host network and PID namespaces, passes Intel GPU/audio devices, and mounts the host PipeWire runtime directory without privileged mode.
- [x] Only the telemetry writer URL and retention setting are passed from the host environment; administrator credentials are not passed to the application.
- [x] Models and Hugging Face caches persist on bind mounts, and service UID/GID plus render/audio groups are configurable.
- [x] Health check uses `GET /api/system` without loading a model; the application remains loopback-bound and restarts unless stopped.
- [x] Deployment guidance supports staging on an alternate local port before replacing the running service and retains existing database/Grafana volumes.

## Verification

GPU, audio, telemetry PID attribution, staged replacement, and rollback must be exercised on the target Linux host; macOS Docker Desktop is not target hardware evidence.

## Target verification: 2026-10-09

Native AMD64 build succeeded on the UBX-330M. The staged container on port 8767 ran as UID/GID 1001 with CPU/GPU visible, OS default audio device 13, and host RAM total matching 16,235,941,888 bytes. HTTP checks proved the UI assets and Feature 01 specification are present and readiness does not implicitly load a model. The final image has a writable application HOME, no baked `.env` or Git directory, no administrator credentials in the application environment, and no privileged flag.

The real Turbo/source OpenVINO GPU model loaded from the existing model bind mount. The staged 21.129-second Thai clip at five-second chunks produced text and five completed measurements in the DB, retaining UTC timestamps and actual host PID. Aggregate RTF was 0.555; the short final chunk had RTF 1.600, so this is not proof that every chunk met real-time pace. Default microphone capture/start/stop/flush completed and its lifecycle events reached the database; the capture window was silent and recognition quality remains unverified.

Cutover to 8765 succeeded, rollback to the retained `speech-feature-01.service` was exercised, then final container cutover succeeded. The host unit is inactive with autostart disabled; the Docker application is healthy with `restart: unless-stopped`. Existing TimescaleDB and Grafana volumes were retained. The final production container processed the same Thai clip with a 30-second chunk limit in 5.47 seconds, RTF 0.259; the completed measurement and owned-process resource sample reached DB. Model loading was an explicit test action; health checks never loaded a model. The model remains loaded for operator use.

After cutover, the observability smoke check passed database event/RTF insertion, Grafana read-only query, retention, datasource/dashboard provisioning, and Editor folder permissions. Reproducible results are in [Linux container evidence](../evidence/linux-container-20261009.json). Model recognition quality, multi-microphone capacity, and failure-isolation hardware gates remain tracked by [Feature 01 ticket 05](../../new-speech-to-text/issues/05-target-hardware-acceptance.md); they are not closed by this packaging/deployment ticket.
