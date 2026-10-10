[← Back to README](../README.md)

# Deployment plan: UBX-330M

## Goal

Deploy the current Speech-to-Text application on the UBX-330M Linux host as a simple LAN service. This document is the operator guide for the Docker image and Compose deployment.

## Shape

- Build one `linux/amd64` Docker image containing the Python backend, the built React frontend, and the converted `turbo` OpenVINO source-precision model. The model is exported in a CPU-only build stage from a pinned Hugging Face revision; runtime uses OpenVINO GPU. FastAPI serves the frontend assets and `/api/v1` from the same origin, with `/healthz` as a model-free health endpoint.
- Run one backend process in one Docker Compose service. Publish port `8000` to the LAN and open `http://<UBX-330M-IP>:8000` in a browser.
- Use HTTP without login for LAN users. Restrict access to the LAN through the host firewall.
- Pass the Intel GPU device (`/dev/dri`) and the host microphone/audio devices into the container. Use the existing OpenVINO GPU model setup.
- Keep only the SQLite profile database on a host-mounted path. The default model ships in each versioned image, so rollback also restores the matching model.

## Image release and update

GitHub Actions builds and tags a `linux/amd64` image for each `v*` release tag, then pushes it to the public package `ghcr.io/faiisu/speech_to_text` using the same `vX.Y.Z` tag plus `latest`. UBX-330M can pull the image without registry credentials. The model-export stage runs without GPU access; its first build downloads the pinned checkpoint and converts it to OpenVINO IR, which can use substantial memory and disk space. A host operator selects the image version with `SPEECH_TO_TEXT_IMAGE` in the Compose environment, pulls it, and recreates the service. Keep the previous image tag available for rollback. Deployments may briefly interrupt active workflows; run status and events are process-local and are lost when the service restarts.

## Host data and configuration

The image contains `openvino-turbo-source` under `/opt/models`; do not mount a models directory over it. Compose mounts `./data` at `/data` and sets `SPEECH_TO_TEXT_PROFILE_DB=/data/profiles.sqlite3`. Create the host directory with `mkdir -p data` before the first start. The application creates the SQLite file on first use. Keep only this database in that directory. Profiles are shared by all LAN users and survive container replacement. The project does not create an additional backup. Run records, transcripts, matches, and events remain in memory and are lost on restart.

Set the service defaults to `SPEECH_TO_TEXT_MODEL=turbo`, `SPEECH_TO_TEXT_RUNTIME=openvino-gpu`, and `SPEECH_TO_TEXT_PRECISION=source`. Pass only the host device permissions needed for Intel GPU and audio access.

Compose publishes `${HOST_PORT:-8000}:8000`, so the trial can use `HOST_PORT=18000 docker compose up -d --build` and leave the existing service on port 8000 untouched. Set `RENDER_GID` and `AUDIO_GID` to the numeric host groups that own `/dev/dri/renderD*` and `/dev/snd` when the defaults differ. One backend worker is started; the container's `/healthz` check does not load the model.

## Build, trial, rollback

For a local image build, run `docker compose build`. This builds the frontend, exports the pinned turbo model in the build stage, and assembles the runtime image. To trial a published version on another port, run `mkdir -p data`, then `SPEECH_TO_TEXT_IMAGE=ghcr.io/faiisu/speech_to_text:vX.Y.Z docker compose pull` followed by `SPEECH_TO_TEXT_IMAGE=ghcr.io/faiisu/speech_to_text:vX.Y.Z HOST_PORT=18000 docker compose up -d --no-build`. Check `curl -fsS http://127.0.0.1:18000/healthz`, open `http://127.0.0.1:18000`, and check `/api/v1/models` for an installed, ready `openvino-gpu` entry with `source` precision. Check `/api/v1/microphones` for host device discovery. GPU readiness requires an Intel GPU host; CPU-only local systems can verify model artifact discovery but cannot pass the GPU-ready check.

For the UBX-330M trial, use the same alternate port and a versioned release image, then verify health, UI and API access, the ready `openvino-gpu` catalog entry, and microphone discovery before considering any port or forwarding changes. Stop only the trial Compose service with `HOST_PORT=18000 docker compose down`; the pre-existing service remains in place. To roll back after a release update, set `SPEECH_TO_TEXT_IMAGE` to the previously working version tag and recreate the Compose service. The profile SQLite file is retained.

## Service operation

- Use a lightweight health endpoint that does not load the model for the container healthcheck.
- Restart the service with Docker Compose's `unless-stopped` policy and inspect output with Docker logs on the host.
- Keep a single backend process. Do not add a lower deployment-specific microphone limit; operators can use the existing capacity workflow to measure the host. The backend's current API still has its own limit of 16 active microphone workflows per process.
- Do not add a persistent queue for outbound delivery. The existing forwarding contract retries transient failures up to three attempts and reports failure after retries are exhausted.

## Go-live gate

The current backend does not connect its HTTP API workflows to `HttpForwarder`. Complete that integration and verify delivery to the destination before opening the service for normal LAN use. Once connected, configure the destination and any outbound credential through the deployment environment, not in the image. Forwarding failure behavior remains bounded in-memory retry with no delivery queue across restarts.

## See also

- [Getting Started](getting-started.md) for the current local development setup.
- [Architecture](architecture.md) for process-local run state and workflow ownership.
- [Transcript matching and forwarding specification](../.scratch/transcript-matching-forwarding/spec.md) for the HTTP payload and retry contract.
