# UBX-330M Docker deployment

Status: wontfix
Legacy outcome: the initial v0.1.2 image trial completed and remains recorded below. The current trial/release and operator instructions are maintained in [`docs/deployment.md`](../../docs/deployment.md).

## Purpose

Build the current application as a versioned Docker image with the default `turbo` OpenVINO model included, then trial it on the UBX-330M without replacing or interrupting the existing service.

Deployment choices and operator behavior are authoritative in the [deployment plan](../../docs/deployment.md). This spec records implementation acceptance only.

## Acceptance criteria

- [x] A reproducible multi-stage Ubuntu 24.04 Docker build installs the backend's OpenVINO, Intel OpenCL, and microphone dependencies, builds the React frontend, and includes the converted `turbo` model at `source` precision.
- [x] Model conversion in the build does not require Intel GPU access; the final runtime image loads that model on UBX-330M OpenVINO GPU.
- [x] FastAPI serves the React application and `/api/v1` from port 8000 and exposes a lightweight health endpoint that does not load the model.
- [x] Docker Compose passes Intel GPU and host audio devices, persists only the SQLite profile database on the host, and runs one backend process with a restart policy.
- [x] GitHub Actions published a version-tagged `linux/amd64` image to the public GHCR package.
- [x] Local validation covers frontend build, health and static serving, model catalog readiness, and Compose configuration.
- [x] The UBX-330M trial runs on an alternate host port, verifies health, OpenVINO GPU readiness, frontend/API access, and microphone discovery without changing the existing deployment.
- [x] Deployment docs describe the build, trial, rollback, persistent profile database, and the go-live gate for HTTP forwarding.

## Trial evidence

- Image: `ghcr.io/faiisu/speech_to_text:v0.1.2`, digest `sha256:46315cd8d4f78e8ed2702b8f60026b0edcd1ccb5d5bc40aba456fce12abc6cc6`, image size 4,575,609,819 bytes.
- Trial URL: `http://192.168.1.106:18000/`; UBX-330M reports LAN address `192.168.1.106` and Tailscale address `100.115.94.105`.
- `/healthz` returned `{"status":"ok"}`; `/` returned HTTP 200; `/api/v1/models` reported `turbo` installed and `openvino-gpu` ready; `/api/v1/microphones` listed the HDA Intel PCH microphone devices.
- Docker health status was healthy, restart policy was `unless-stopped`, and `/dev/dri` plus `/dev/snd` were passed through with host groups 992 and 29.
- The app was reachable from the deployment workstation over Tailscale at `http://100.115.94.105:18000/`. The workstation could not route to `192.168.1.106`; this did not prevent local UBX checks or Tailscale access.
- No existing container was stopped or changed. The obsolete failed `v0.1.0` image cache was removed to recover disk space; the deployed `v0.1.2` image remains active. Temporary SSH key access was revoked after deployment.
- GitHub Actions run: https://github.com/Faiisu/speech_to_text/actions/runs/38048198132. GHCR tag and digest were available and pulled successfully while the workflow was still reporting `in_progress` during the final Buildx step.

## Non-goals

- Do not implement or enable outbound forwarding as part of this deployment package; normal user go-live remains gated on that separate integration.
- Do not add authentication, HTTPS, a reverse proxy, durable workflow/event storage, a delivery queue, or a lower deployment-specific microphone limit.
- Do not replace, stop, or reconfigure the existing UBX-330M service during the trial.
