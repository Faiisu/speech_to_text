[← Back to README](../README.md)

# Deployment

The service can run directly on the host for development or in the Linux Docker image for deployment. The container shares the host network and PID namespace so the loopback-only Control Center, host telemetry, Intel GPU, and PipeWire microphone retain their host integration. TimescaleDB and Grafana run in Docker Compose and bind to loopback by default. Deployment and telemetry behavior are specified in the [System Observability specification](../.scratch/system-observability/spec.md).

## Linux service container

The image uses Ubuntu 24.04, Python 3.12, CPU-only PyTorch, OpenVINO, and the host Intel OpenCL driver interface. Build and start the database, Grafana, and service from the repository root:

```bash
test -f .env || cp .env.observability.example .env
# Replace password placeholders and set deployment values described in Configuration.
mkdir -p .cache/huggingface models
docker compose -p speech-feature-01 -f compose.observability.yml -f compose.speech-service.yml up -d --build
```

See [Configuration](configuration.md#container-deployment-settings) for the image tag, port, user and group IDs, model path, and telemetry environment settings. Set `SPEECH_TO_TEXT_IMAGE` to use a prebuilt image with `up --no-build`.

Use the same Compose project name as the existing observability stack so it attaches to that stack's named database and Grafana volumes. The Linux target uses `speech-feature-01`; if the existing stack was started without `-p` from this repository, use `-p speech_to_text` or remove `-p` from every command below. The Compose environment passes only the telemetry writer URL and retention setting to the service; database administrator and Grafana credentials stay with their respective services. The service binds to `127.0.0.1:${CONTROL_CENTER_PORT:-8765}`.

To cut over from a host service managed by systemd, first stage the image on port `8767`, validate its health and target behavior, then stop the host unit before recreating the container on port `8765`:

```bash
HOST_SERVICE_UNIT=speech-feature-01.service
CONTROL_CENTER_PORT=8767 docker compose -p speech-feature-01 -f compose.observability.yml -f compose.speech-service.yml up -d --build speech-service
curl -fsS http://127.0.0.1:8767/api/system
# Validate model loading, clip inference, telemetry, and microphone access on the staged service.
sudo systemctl stop "$HOST_SERVICE_UNIT"
CONTROL_CENTER_PORT=8765 docker compose -p speech-feature-01 -f compose.observability.yml -f compose.speech-service.yml up -d speech-service
docker compose -p speech-feature-01 -f compose.observability.yml -f compose.speech-service.yml ps
curl -fsS http://127.0.0.1:8765/api/system
sudo systemctl disable "$HOST_SERVICE_UNIT"
```

After `/api/system` succeeds on port `8765` and Compose reports the service healthy, disable the host unit's autostart to prevent a port collision after reboot. The Linux target's unit is `speech-feature-01.service`; replace that name if your host uses a different unit. Keep the unit installed for rollback. To roll back, stop the container and re-enable/start the unit:

```bash
docker compose -p speech-feature-01 -f compose.observability.yml -f compose.speech-service.yml stop speech-service
sudo systemctl enable --now speech-feature-01.service
```

The service uses the host UID/GID for model and Hugging Face cache writes, adds the configured render/audio groups, passes through `/dev/dri` and `/dev/snd`, and mounts `/run/user/$HOST_UID` read-only for the PipeWire socket. Start the host user's PipeWire session before starting Compose; its runtime directory must already exist. Rebuild the image after changing `HOST_UID` or `HOST_GID`; ensure `.cache/huggingface` and the configured models directory are writable by `HOST_UID`. Host networking and the shared PID namespace are Linux deployment settings; Docker Desktop on macOS does not expose the host GPU or audio devices this way. Compose restarts the application unless stopped and checks `GET /api/system` without loading a model. The API continues to accept loopback addresses only.

To run a prepared image after changing its configuration, recreate the service with the same combined Compose files. The existing `telemetry-database` and `telemetry-grafana` named volumes are shared with the observability stack and are not removed by rebuilding the service image.

## Mac Docker Desktop test profile

Use the Mac profile to exercise the app, model, and telemetry in a native ARM64 CPU container on Apple Silicon. It builds a Mac-test image with CTranslate2 and the existing `turbo` int8 weights; the production Linux AMD64 image and Dockerfile remain unchanged. This is a software and container integration check. Host CPU and memory measurements come from Docker Desktop's Linux VM, and the profile does not pass through the Mac GPU or microphone. It does not prove the Linux AMD64 image or target-hardware behavior.

The profile uses bridge networking. Uvicorn listens on `0.0.0.0:8765` inside the app container so Docker can forward requests, while the published API stays bound to `127.0.0.1:18766` on the Mac. TimescaleDB and Grafana publish on `127.0.0.1:15433` and `127.0.0.1:13001`. These defaults avoid the common local ports `8765`, `5433`, and `3000`. Compose 2.24.4 or newer is required for the port replacement syntax. The default image platform is `linux/arm64`; set `MAC_TEST_PLATFORM=linux/amd64` to use emulation if the native CTranslate2 wheel is unavailable.

Create `.env` from `.env.observability.example` if needed and replace its placeholders. The app's database URL is built inside Compose with `timescaledb:5432` and the `TELEMETRY_WRITER_PASSWORD`; administrator and Grafana credentials are not passed to the app. Since Compose inserts that password directly into a URL, use URI-unreserved characters (letters, digits, `-`, `.`, `_`, `~`) for `TELEMETRY_WRITER_PASSWORD`. Set up the existing model and Hugging Face cache directories, then start the isolated Mac test project:

```bash
test -f .env || cp .env.observability.example .env
mkdir -p .cache/huggingface models
HOST_UID="$(id -u)" HOST_GID="$(id -g)" docker compose -p speech-mac-test \
  -f compose.observability.yml -f compose.mac-test.yml up -d --build
curl -fsS http://127.0.0.1:18766/api/system
```

The model directory is mounted read-only and should contain the existing `ctranslate2-turbo` int8 weights. In the Feature 01 page, select model `turbo`, runtime `ctranslate2`, and precision `int8` before loading the model. Runtime conversion cannot write into this mount. The Hugging Face cache remains writable and persistent, and the UID/GID build arguments align that cache with the current Mac user.

The smoke check must target the Mac test project's database and Grafana ports. Load `.env` without printing it, then construct host-side writer and migration URLs for the published database port. Use URI-unreserved characters for both database passwords so they can be inserted directly into these URLs:

```bash
set -a
source .env
set +a
export TELEMETRY_DATABASE_PORT="${MAC_TEST_TELEMETRY_DATABASE_PORT:-15433}"
export GRAFANA_HOST="http://127.0.0.1:${MAC_TEST_GRAFANA_PORT:-13001}"
export SPEECH_TO_TEXT_TELEMETRY_DATABASE_URL="postgresql://telemetry_writer:${TELEMETRY_WRITER_PASSWORD}@127.0.0.1:${TELEMETRY_DATABASE_PORT}/${TELEMETRY_DATABASE:-speech_telemetry}"
export SPEECH_TO_TEXT_TELEMETRY_MIGRATION_DATABASE_URL="postgresql://${TELEMETRY_ADMIN_USER:-telemetry_admin}:${TELEMETRY_ADMIN_PASSWORD}@127.0.0.1:${TELEMETRY_DATABASE_PORT}/${TELEMETRY_DATABASE:-speech_telemetry}"
.venv/bin/python scripts/observability_smoke.py
```

Stop the test stack with:

```bash
docker compose -p speech-mac-test -f compose.observability.yml -f compose.mac-test.yml down
```

The separate `speech-mac-test` project uses its own database and Grafana volumes. The test does not replace Linux deployment services or prove macOS hardware metrics, real microphone capture, Intel GPU behavior, or target capacity.

## Database and Grafana

1. Create a local environment file and replace every placeholder with a unique password:

   ```bash
   test -f .env || cp .env.observability.example .env
   ```

2. Start TimescaleDB and Grafana:

   ```bash
   docker compose -f compose.observability.yml up -d
   ```

   On a new database volume, Compose creates database roles, schema, indexes, hypertables, and retention policies. The application uses the insert-only `telemetry_writer` role; Grafana uses the read-only `telemetry_grafana` role.

3. For direct host development, start the service with the database URL loaded:

   ```bash
   set -a
   source .env
   set +a
   .venv/bin/python -m speech_to_text.control_center
   ```

4. Open Grafana at <http://127.0.0.1:3000/> and sign in with the local admin credentials from `.env`. The provisioned dashboard is named **Speech-to-Text Operations**. You can edit panels and save dashboard changes in Grafana.

For an existing database or a retention-policy change, apply checked-in migrations using the privileged migration URL:

```bash
set -a; source .env; set +a
.venv/bin/python -m speech_to_text.features.system_observability.migrations
```

The application writer does not apply DDL. Do not use the migration credential for normal service writes. Run the deployment verification after startup:

```bash
set -a; source .env; set +a
.venv/bin/python scripts/observability_smoke.py
```

The smoke check inserts a temporary event and RTF measurement, verifies database and Grafana reads, checks dashboard provisioning and retention, then removes its temporary rows.

Grafana UI edits persist in Grafana's volume. A later update to the checked-in provisioned dashboard can replace the provisioned dashboard content; export UI edits to `deploy/observability/grafana/dashboards/speech-telemetry.json` when they should be version-controlled.

## Target hardware

The default runtime is `openvino-gpu`, but successful tests on a Mac or an injected runtime do not prove the configured OpenVINO GPU works on the target Linux host. Before treating deployment as complete, run the real model, microphone, and capacity proofs on the intended machine. The authoritative acceptance gates are listed in the [Feature 01 specification](../.scratch/new-speech-to-text/spec.md#acceptance-evidence-to-establish-before-treating-deployment-as-complete).

The Feature 01 capacity tool accepts named devices and both process topologies. For example:

```bash
.venv/bin/python -m speech_to_text.features.model_deployment.capacity \
  --topology shared-model \
  --device "Microphone A" "Microphone B" \
  --duration-seconds 60 \
  --output shared-model.json
```

Run again with `--topology per-input-model` to compare the alternative. The tool reports measured evidence and can return pass, fail, unavailable, or inconclusive; do not treat unavailable measurements as zero.

## Network exposure

The control center only accepts loopback host addresses. Compose publishes database and Grafana ports on `127.0.0.1` by default. Remote access, authentication, and public exposure require a separately designed deployment boundary; do not change the host binding casually.

## See also

- [Configuration](configuration.md) for environment variables and model settings.
- [Getting Started](getting-started.md) for local development setup.
- [Architecture](architecture.md) for process ownership and telemetry flow.
- [System Observability specification](../.scratch/system-observability/spec.md) for retention and dashboard contracts.
