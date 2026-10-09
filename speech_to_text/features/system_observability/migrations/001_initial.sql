CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE TABLE IF NOT EXISTS telemetry_schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS operation_measurements (
    recorded_at timestamptz NOT NULL,
    feature_id text NOT NULL,
    operation text NOT NULL,
    pid integer NOT NULL,
    source_id text,
    sequence bigint,
    elapsed_seconds double precision,
    audio_seconds double precision,
    inference_seconds double precision,
    rtf double precision,
    status text NOT NULL,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (audio_seconds IS NULL OR audio_seconds >= 0),
    CHECK (inference_seconds IS NULL OR inference_seconds >= 0),
    CHECK (rtf IS NULL OR rtf >= 0)
);
SELECT create_hypertable('operation_measurements', 'recorded_at', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS operation_measurements_feature_time_idx
    ON operation_measurements (feature_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS operation_measurements_source_time_idx
    ON operation_measurements (source_id, recorded_at DESC);

CREATE TABLE IF NOT EXISTS service_events (
    recorded_at timestamptz NOT NULL,
    severity text NOT NULL CHECK (severity IN ('debug', 'info', 'warning', 'error')),
    event_name text NOT NULL,
    service text NOT NULL,
    feature_id text,
    pid integer NOT NULL,
    source_id text,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb
);
SELECT create_hypertable('service_events', 'recorded_at', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS service_events_severity_time_idx
    ON service_events (severity, recorded_at DESC);
CREATE INDEX IF NOT EXISTS service_events_feature_time_idx
    ON service_events (feature_id, recorded_at DESC);

CREATE TABLE IF NOT EXISTS host_samples (
    recorded_at timestamptz NOT NULL,
    cpu_percent double precision,
    memory_used_bytes bigint,
    memory_total_bytes bigint,
    swap_used_bytes bigint,
    swap_total_bytes bigint,
    storage_used_bytes bigint,
    storage_total_bytes bigint,
    uptime_seconds double precision,
    load_average jsonb,
    statuses jsonb NOT NULL DEFAULT '{}'::jsonb
);
SELECT create_hypertable('host_samples', 'recorded_at', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS process_samples (
    recorded_at timestamptz NOT NULL,
    service text NOT NULL,
    feature_id text,
    role text NOT NULL,
    source_id text,
    pid integer NOT NULL,
    lifecycle_state text NOT NULL,
    cpu_percent double precision,
    rss_bytes bigint,
    memory_percent double precision,
    thread_count integer,
    started_at timestamptz,
    statuses jsonb NOT NULL DEFAULT '{}'::jsonb
);
SELECT create_hypertable('process_samples', 'recorded_at', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS process_samples_owner_time_idx
    ON process_samples (feature_id, role, source_id, pid, recorded_at DESC);

CREATE TABLE IF NOT EXISTS writer_health (
    recorded_at timestamptz NOT NULL,
    queue_depth integer NOT NULL,
    dropped_count bigint NOT NULL,
    write_failures bigint NOT NULL,
    last_error text
);
SELECT create_hypertable('writer_health', 'recorded_at', if_not_exists => TRUE);

CREATE OR REPLACE FUNCTION ensure_observability_retention(retention_days integer DEFAULT 30)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE table_name text;
BEGIN
    IF retention_days < 1 THEN
        RAISE EXCEPTION 'retention_days must be positive';
    END IF;
    FOREACH table_name IN ARRAY ARRAY['operation_measurements','service_events','host_samples','process_samples','writer_health'] LOOP
        PERFORM add_retention_policy(table_name::regclass, make_interval(days => retention_days), if_not_exists => TRUE);
    END LOOP;
END;
$$;

SELECT ensure_observability_retention(COALESCE(NULLIF(current_setting('app.telemetry_retention_days', true), '')::integer, 30));

GRANT INSERT ON operation_measurements, service_events, host_samples, process_samples, writer_health TO telemetry_writer;
GRANT SELECT ON operation_measurements, service_events, host_samples, process_samples, writer_health TO telemetry_grafana;
GRANT USAGE ON SCHEMA public TO telemetry_writer, telemetry_grafana;
INSERT INTO telemetry_schema_migrations(version) VALUES ('001_initial') ON CONFLICT DO NOTHING;
