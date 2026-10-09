# 04. Verify the Mac Docker Desktop CPU deployment

Status: ready-for-human
Execution: native-arm64-image-built; Mac-deployment-and-real-cpu-inference-verified
Blocked by: 02, 03

## Scope

Provide a companion Compose profile for exercising the app, CPU model, and telemetry in a native ARM64 container on Apple Silicon with Docker Desktop. The profile is for container and application integration checks; it must not be reported as macOS hardware telemetry, microphone, GPU, or Linux target evidence. Operator steps and the resource-measurement boundary are documented in [Deployment](../../../docs/deployment.md#mac-docker-desktop-test-profile).

## Acceptance checklist

- [x] A separate `Dockerfile.mac-test` and `compose.mac-test.yml` provide a native `linux/arm64` CPU image with CTranslate2, leaving the Linux production Dockerfile and service Compose file unchanged.
- [x] The Mac profile pins the locally verified `faster-whisper`, CTranslate2, FastAPI, and Uvicorn versions.
- [x] The app uses Docker bridge networking, no host PID namespace, no audio/GPU devices, and no PipeWire bind mount.
- [x] The app listens on the bridge interface internally, publishes only on Mac loopback, and uses a dedicated default API port that avoids the Linux service port.
- [x] The app connects to TimescaleDB at `timescaledb:5432` using only its insert-only writer credential; the existing database/Grafana services publish on dedicated Mac loopback ports.
- [x] Existing model files and Hugging Face cache are mounted at their production container paths; model files are read-only and the cache is writable.
- [x] Docker Compose configuration resolves with the local `.env` without exposing secret values in recorded output.
- [x] The ARM64 image builds and the API becomes healthy in Docker Desktop.
- [x] A request to `/api/system` succeeds through the Mac loopback port, and a telemetry write is readable from TimescaleDB/Grafana.
- [x] The existing `turbo` CTranslate2 int8 model loads and transcribes a clip successfully.
- [x] Recorded resource samples are identified as Docker Desktop Linux VM measurements, not native macOS host measurements.

## Verification

`docker compose -p speech-mac-test -f compose.observability.yml -f compose.mac-test.yml config -q` passes with local Compose v5.1.1. A sanitized resolved-config inspection confirmed `linux/arm64`, the `Dockerfile.mac-test` build, loopback ports, and internal writer target `timescaledb:5432`; credential values were omitted. Image build, API/database integration, model transcription, and resource-sample results on the Apple Silicon Mac are recorded below. This ticket does not close Linux target deployment, microphone, Intel GPU, Thai recognition-quality, or capacity gates.

## Mac runtime verification: 2026-10-09

Built `speech-to-text:mac-test` natively as Linux ARM64 with the current Mac UID/GID 501:20. All three containers in the isolated `speech-mac-test` project are healthy. The application is reachable on loopback port 18766, TimescaleDB on 15433, and Grafana on 13001; the existing local observability project was retained. Readiness did not load a model. HTTP checks verified the shell/assets/spec and a real headless Chrome run rendered the feature page, service-ready state, and one explicitly loaded model. The mounted Hugging Face cache is writable, and the image does not contain `.env`.

Explicitly loaded Turbo with CTranslate2 int8 and transcribed the existing 21.129-second Thai clip with a 30-second chunk limit. The request completed in 7.192 seconds and wrote a completed per-chunk measurement with RTF 0.340 to the isolated database. Grafana read that actual RTF through its provisioned read-only datasource. The observability smoke check passed event/measurement insertion, read-only queries, retention, datasource/dashboard provisioning, and Editor permission.

The process record uses container PID 7. The host sample reported 8,217,341,952 memory bytes, matching Docker Desktop's Linux VM, while macOS reports 17,179,869,184 bytes. These are Docker VM/container measurements; the Mac microphone, Mac GPU, production Linux image, independent transcript CER, and multi-input capacity were not verified by this run. The model remains loaded for operator use. See [Mac deployment evidence](../evidence/mac-container-20261009.json).
