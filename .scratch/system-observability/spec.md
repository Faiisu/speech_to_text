# System Observability

## Purpose

Persist operational measurements from the Speech-to-Text services and the host PC in a database, then inspect them in Grafana. The Control Center remains a feature test and control interface; it does not render the system-observation dashboards.

## Storage and deployment

Use TimescaleDB (PostgreSQL-compatible) for persisted telemetry. This matches the database already used by the archived PoC, while Grafana's built-in PostgreSQL data source can query it directly. Provide Compose services for TimescaleDB and Grafana, bound to loopback by default, with persistent database and Grafana volumes. The model-serving application can run on the host or in the Linux deployment image. The container shares the host network and PID namespace and receives the Intel GPU, audio devices, and host PipeWire runtime socket; its HTTP server still binds to loopback only.

Configure the application with `SPEECH_TO_TEXT_TELEMETRY_DATABASE_URL`. The application writer uses a dedicated telemetry database role. Grafana uses a separate read-only role. Credentials come from an untracked environment file; example configuration contains placeholders only.

## Records

Every record uses a UTC timestamp and identifies its originating service/feature. The shared service contract must support:

- **Service events:** severity (`debug`, `info`, `warning`, `error`), stable event name/code, originating service/feature, OS process ID, UTC timestamp, optional source ID, and an allowlisted set of structured attributes. Persist errors and lifecycle/queue events as searchable rows. Do not persist free-form messages, audio, transcripts, credentials, or arbitrary payloads.
- **Operation measurements:** feature ID, operation, OS process ID, timestamp, source ID, sequence, elapsed processing seconds, and optional feature-specific fields.
- **Feature 01 chunk measurements:** one record for each chunk that reaches inference, with audio duration, inference duration, and `RTF = inference_seconds / audio_seconds`. `RTF <= 1.0` means the chunk met real-time pace. Preserve per-chunk values; do not replace them with only an average. Silent chunks skipped before inference produce no RTF record. These fields follow the [Feature 01 spec](../new-speech-to-text/spec.md#per-chunk-performance-measurement).
- **Host samples:** aggregate CPU, physical memory, swap, model-volume storage, uptime, and load average when supported.
- **Owned process samples:** service/feature, role, source, PID, lifecycle state, CPU, RSS, memory share, thread count, and process start time when available. Discover only process IDs explicitly reported by Speech-to-Text feature providers; never enumerate unrelated PC processes or store command lines.

Sample host and owned process resources every two seconds. Missing or unsupported values have an explicit status and reason, never a fabricated zero. GPU and temperature remain unavailable until a supported host adapter is verified.

The database writer is asynchronous and bounded so telemetry I/O cannot hold up audio inference. Batch records and retry transient database failures. On queue saturation or a permanent write failure, increment/report dropped telemetry and keep core transcription operational; do not block a microphone worker on a database transaction. Drain queued records during orderly service shutdown within a documented timeout.

Retain telemetry for 30 days by default, configurable with `SPEECH_TO_TEXT_TELEMETRY_RETENTION_DAYS`. Provide a database policy/migration for retention; do not delete persisted records on application restart.

## Grafana

Provision a PostgreSQL-compatible TimescaleDB data source using the read-only Grafana role and version-controlled dashboards. Allow dashboard edits from the Grafana UI and keep Grafana's database on a persistent volume so saved edits survive container replacement. Document that a future update to the provisioned dashboard source overwrites UI-saved changes; operators can export UI edits back to the dashboard source when they want them version-controlled. The initial dashboard includes:

- Per-chunk RTF as a numeric table with timestamp, feature/operation, PID, source, sequence, audio duration, inference duration, and status.
- RTF over time with a visible real-time target at `1.0`, filterable by feature, operation, process, and source.
- Host CPU and memory/swap over time.
- Speech-to-Text process CPU and RSS over time, grouped by role/source/PID.
- Telemetry writer health, queue depth, and dropped-record count when available.
- Recent service events and error counts, filterable by service, feature, severity, process, and source.

Grafana is the operator-facing observation tool. Do not add a parallel Control Center observation page or API for browsing historical telemetry.

## Feature contribution contract

Each feature publishes measurements through the shared telemetry writer contract; it does not create its own dashboard schema or couple inference code to SQL. The service owns the database writer lifecycle and maps the stable record contract into persisted tables. Adding a feature adds its feature ID and measurements, then its Grafana queries can filter by that ID.

The host service applies no database DDL with its insert-only writer role. A privileged deployment step applies checked-in migrations and the retention policy; new Compose databases apply the same schema during initialization. If the database URL is absent, the writer and sampler stay disabled and inference continues.

## Verification

Automated tests cover schema creation, serialization, per-chunk RTF values, PID/timestamp/source attribution, asynchronous write behavior, retry/queue saturation behavior, and Grafana provisioning/dashboard validity. A Compose smoke check verifies database readiness, writer inserts, Grafana data-source provisioning, and dashboard loading. Linux target-host validation remains separate from macOS development proof.

## Local operation

Copy `.env.observability.example` to `.env` and replace every placeholder with unique local credentials. Run `docker compose -f compose.observability.yml up -d` for database and Grafana only, or use the combined observability and speech-service Compose files for Linux deployment as described in [Deployment](../../docs/deployment.md#linux-service-container). The database and Grafana ports bind to `127.0.0.1` by default. On a fresh database, Compose creates the roles, schema, indexes, hypertables, and retention policy. The service uses `SPEECH_TO_TEXT_TELEMETRY_DATABASE_URL`.

For an existing database or a retention change, set `SPEECH_TO_TEXT_TELEMETRY_MIGRATION_DATABASE_URL` to a privileged PostgreSQL URL and run `.venv/bin/python -m speech_to_text.features.system_observability.migrations`. Install `.[telemetry]` into the repository `.venv` for the psycopg writer and psutil sampler. The regular service URL must use the insert-only `telemetry_writer` role.

Run the deployment smoke check after exporting `.env` values with `set -a; source .env; set +a; .venv/bin/python scripts/observability_smoke.py`. It provisions Editor folder permission and verifies a telemetry insert/read through the Grafana database role, the Grafana data source, and the dashboard. Sign in at `http://127.0.0.1:3000`; the initial admin username and password are the local values in `.env`.
