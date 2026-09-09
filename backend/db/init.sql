CREATE EXTENSION IF NOT EXISTS timescaledb;

-- `station` is the labelled microphone the detection came from ("Line 1").
-- Defaulted rather than required so the PoC panel and the CLI, which have no
-- station, keep inserting without change.
CREATE TABLE IF NOT EXISTS detection_events (
    id BIGSERIAL,
    word TEXT NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL,
    model TEXT NOT NULL,
    session_id TEXT NOT NULL,
    station TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (id, detected_at)
);

SELECT create_hypertable('detection_events', 'detected_at', if_not_exists => TRUE);

-- The operator UI's main query is "recent hits for this station", which
-- otherwise scans every chunk of the hypertable.
CREATE INDEX IF NOT EXISTS detection_events_station_time
    ON detection_events (station, detected_at DESC);
