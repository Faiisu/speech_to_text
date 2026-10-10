# UBX-330M Docker deployment

Status: in progress

## Purpose

Build the current application as a versioned Docker image with the default `turbo` OpenVINO model included, then trial it on the UBX-330M without replacing or interrupting the existing service.

Deployment choices and operator behavior are authoritative in the [deployment plan](../../docs/deployment.md). This spec records implementation acceptance only.

## Acceptance criteria

- [ ] A reproducible multi-stage Ubuntu 24.04 Docker build installs the backend's OpenVINO, Intel OpenCL, and microphone dependencies, builds the React frontend, and includes the converted `turbo` model at `source` precision.
- [ ] Model conversion in the build does not require Intel GPU access; the final runtime image loads that model on UBX-330M OpenVINO GPU.
- [ ] FastAPI serves the React application and `/api/v1` from port 8000 and exposes a lightweight health endpoint that does not load the model.
- [ ] Docker Compose passes Intel GPU and host audio devices, persists only the SQLite profile database on the host, and runs one backend process with a restart policy.
- [ ] GitHub Actions builds and pushes a version-tagged `linux/amd64` image to the public GHCR package.
- [ ] Local validation covers frontend build, health and static serving, model catalog readiness, and Compose configuration.
- [ ] The UBX-330M trial runs on an alternate host port, verifies health, OpenVINO GPU readiness, frontend/API access, and microphone discovery without changing the existing deployment.
- [ ] Deployment docs describe the build, trial, rollback, persistent profile database, and the go-live gate for HTTP forwarding.

## Non-goals

- Do not implement or enable outbound forwarding as part of this deployment package; normal user go-live remains gated on that separate integration.
- Do not add authentication, HTTPS, a reverse proxy, durable workflow/event storage, a delivery queue, or a lower deployment-specific microphone limit.
- Do not replace, stop, or reconfigure the existing UBX-330M service during the trial.
