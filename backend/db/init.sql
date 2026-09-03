CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS detection_events (
    id BIGSERIAL,
    word TEXT NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL,
    model TEXT NOT NULL,
    session_id TEXT NOT NULL,
    PRIMARY KEY (id, detected_at)
);

SELECT create_hypertable('detection_events', 'detected_at', if_not_exists => TRUE);
