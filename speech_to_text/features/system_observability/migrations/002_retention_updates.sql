CREATE INDEX IF NOT EXISTS host_samples_time_idx ON host_samples (recorded_at DESC);
CREATE INDEX IF NOT EXISTS writer_health_time_idx ON writer_health (recorded_at DESC);

CREATE OR REPLACE FUNCTION ensure_observability_retention(retention_days integer DEFAULT 30)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE table_name text;
BEGIN
    IF retention_days < 1 THEN
        RAISE EXCEPTION 'retention_days must be positive';
    END IF;
    FOREACH table_name IN ARRAY ARRAY['operation_measurements','service_events','host_samples','process_samples','writer_health'] LOOP
        PERFORM remove_retention_policy(table_name::regclass, if_exists => true);
        PERFORM add_retention_policy(table_name::regclass, make_interval(days => retention_days));
    END LOOP;
END;
$$;

INSERT INTO telemetry_schema_migrations(version) VALUES ('002_retention_updates') ON CONFLICT DO NOTHING;
