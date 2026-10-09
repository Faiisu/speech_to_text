[← Back to README](../README.md)

# Deployment

The model-serving service runs on the host so it can access local microphones and hardware runtimes. The optional TimescaleDB and Grafana services run in Docker Compose and bind to loopback by default. Deployment and telemetry behavior are specified in the [System Observability specification](../.scratch/system-observability/spec.md).

## Database and Grafana

1. Create a local environment file and replace every placeholder with a unique password:

   ```bash
   cp .env.observability.example .env
   ```

2. Start TimescaleDB and Grafana:

   ```bash
   docker compose -f compose.observability.yml up -d
   ```

   On a new database volume, Compose creates database roles, schema, indexes, hypertables, and retention policies. The application uses the insert-only `telemetry_writer` role; Grafana uses the read-only `telemetry_grafana` role.

3. Start the host service with the database URL loaded:

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
