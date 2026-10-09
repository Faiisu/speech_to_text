[← Back to README](../README.md)

# Deployment

The service runs directly on the host for development or in one of two speech-service-only Docker Compose profiles. Per-chunk RTF is returned with inference results and microphone events; neither profile requires a database, Grafana, credentials, or a telemetry sidecar.

## Linux service container

The Linux image uses Ubuntu 24.04, Python 3.12, CPU-only PyTorch, OpenVINO, and the host Intel OpenCL driver interface. It uses Compose bridge networking and publishes the API on all IPv4 host interfaces; the service receives the Intel GPU, audio devices, and host PipeWire runtime socket. Its health check calls `/api/system` without loading a model.

Copy the optional deployment defaults, prepare model and download-cache directories, then build and start the speech service:

```bash
test -f .env || cp .env.example .env
mkdir -p .cache/huggingface models
docker compose -p speech-feature-01 -f compose.speech-service.yml up -d --build --wait speech-service
curl -fsS http://127.0.0.1:18765/api/system
```

The container listens on `0.0.0.0:8765`, and Docker publishes it as `0.0.0.0:18765` on the host by default. `SPEECH_TO_TEXT_HOST_PORT` changes the host port; `CONTROL_CENTER_PORT` changes the container port. The service is reachable through host network interfaces, subject to host firewall and network routing rules; the existing SSH tunnel can also target host port `18765`. The Control Center has no authentication, so use this binding only on a network where its users are trusted. The model directory is writable in this profile. The host UID/GID owns mounted cache files; `RENDER_GID` and `AUDIO_GID` grant device access, `/dev/dri` and `/dev/snd` are passed through, and `/run/user/$HOST_UID` is mounted read-only for PipeWire. Start the host user's PipeWire session before Compose; its runtime directory must exist. Rebuild after changing `HOST_UID` or `HOST_GID`.

The project name remains `speech-feature-01`. If retiring an earlier observability stack under this project name, first wait for the speech service to become healthy, then remove its orphan containers with the same standalone compose file:

```bash
docker compose -p speech-feature-01 -f compose.speech-service.yml ps
curl -fsS http://127.0.0.1:18765/api/system
docker compose -p speech-feature-01 -f compose.speech-service.yml up -d --remove-orphans
```

The last command stops containers no longer defined by this Compose file, such as the old database and Grafana services. It does not remove named volumes. Do not use `down -v` when retiring those containers. Old `.env` database/Grafana keys are ignored by these compose files and are not needed by the speech service.

For host-service rollback, stop the container and re-enable the existing systemd unit:

```bash
docker compose -p speech-feature-01 -f compose.speech-service.yml stop speech-service
sudo systemctl enable --now speech-feature-01.service
```

## Mac Docker Desktop test profile

The Mac profile exercises the app and existing model in a native ARM64 CPU container on Apple Silicon. It does not pass through the Mac GPU, audio devices, or prove the Linux AMD64 image or target hardware. Docker Desktop does not expose CoreAudio devices from its Linux VM, so the optional Mac host bridge provides one microphone session.

Prepare the existing model and Hugging Face cache directories, then start the isolated `speech-mac-test` project. The model directory is mounted read-only, while the cache is writable:

```bash
test -f .env || cp .env.example .env
mkdir -p .cache/huggingface models
HOST_UID="$(id -u)" HOST_GID="$(id -g)" docker compose -p speech-mac-test \
  -f compose.mac-test.yml up -d --build --wait speech-service
curl -fsS http://127.0.0.1:18766/api/system
```

The container API listens on `0.0.0.0:8765` internally and Docker publishes `0.0.0.0:18766` on the Mac by default. `MAC_TEST_PLATFORM` defaults to `linux/arm64`; set it to `linux/amd64` only to use Docker Desktop emulation. The project name and API port remain `speech-mac-test` and `18766`. The UI and API can be reached through the Mac's network interfaces, subject to firewall rules. The Mac microphone bridge still listens only on the Mac's loopback address, so microphone capture through that bridge requires opening the UI in a browser on the Mac itself.

If this project previously included the observability services, run the single-service Compose command after `/api/system` succeeds to stop those orphans:

```bash
HOST_UID="$(id -u)" HOST_GID="$(id -g)" docker compose -p speech-mac-test \
  -f compose.mac-test.yml up -d --remove-orphans
```

This keeps the model and cache mounts and does not remove named volumes. The Mac profile does not use the old database or Grafana volumes.

### Mac microphone bridge

The host bridge captures a selected Mac input and forwards bounded authenticated PCM batches to the active Feature 01 session. Install microphone capture support in the host `.venv`, then start the bridge after the container is healthy:

```bash
uv pip install --python .venv/bin/python -e '.[microphone]'
SPEECH_TO_TEXT_BRIDGE_SERVICE_URL=http://127.0.0.1:18766 \
SPEECH_TO_TEXT_BRIDGE_CONTROL_ORIGIN='*' \
  .venv/bin/python -m speech_to_text.features.mac_microphone_bridge
```

The bridge listens on `127.0.0.1:18767`. On first capture, macOS may ask permission for the terminal or Python process. Select an input in the Feature 01 microphone tab. The host bridge owns physical capture; Feature 01 owns chunking, inference, transcript and measurement events. Stopping from the page drains accepted audio and flushes the final partial chunk. Process groups and capacity capture remain unavailable in this Docker profile because they require direct host access to microphones.

Wildcard CORS allows loopback aliases such as `127.0.0.1`, `localhost`, and `[::1]`; a specific local HTTP origin enables strict origin checking. The bridge remains loopback-only, and audio writes require the per-session bearer token. If the control-center or bridge port changes, set `SPEECH_TO_TEXT_BRIDGE_SERVICE_URL`, `SPEECH_TO_TEXT_BRIDGE_CONTROL_ORIGIN`, and `SPEECH_TO_TEXT_BRIDGE_PORT`; see [Configuration](configuration.md#environment-variables).

## Target hardware

The default runtime is `openvino-gpu`, but a Mac container or an injected runtime does not prove OpenVINO GPU support on the target Linux host. Run the model, microphone, and capacity proofs on the intended machine before treating deployment as complete. The acceptance gates are listed in the [Feature 01 specification](../.scratch/new-speech-to-text/spec.md#acceptance-evidence-to-establish-before-treating-deployment-as-complete).

The Feature 01 capacity tool accepts named devices and both process topologies. For example:

```bash
.venv/bin/python -m speech_to_text.features.model_deployment.capacity \
  --topology shared-model \
  --device "Microphone A" "Microphone B" \
  --duration-seconds 60 \
  --output shared-model.json
```

Run again with `--topology per-input-model` to compare the alternative. The tool reports measured evidence and can return pass, fail, unavailable, or inconclusive; unavailable values are never treated as zero.

## Network exposure

Docker publishes Linux `18765` and the Mac profile's `18766` on `0.0.0.0`, exposing both services through the host's IPv4 interfaces. The Linux container listens on `0.0.0.0:8765` on its Compose bridge network. The Control Center permits CORS requests from any origin, method, and header, with credentials disabled; it has no authentication. Restrict access with trusted network placement and host firewall rules. The Mac microphone bridge remains bound to `127.0.0.1`, even though the Mac UI/API port is published on all interfaces.

## See also

- [Configuration](configuration.md) for environment variables and model settings.
- [Getting Started](getting-started.md) for local development.
- [Architecture](architecture.md) for process ownership and per-chunk measurements.
- [Retired System Observability specification](../.scratch/system-observability/spec.md) for context about the removed database and Grafana deployment.
