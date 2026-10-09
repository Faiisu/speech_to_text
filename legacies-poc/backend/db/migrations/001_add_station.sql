-- init.sql only runs on a *fresh* volume, so a machine that already has a
-- timescaledb_data volume (the UBX-330M does) never sees the station column
-- added there. This migration is what actually updates it, and is safe to
-- re-run: deploy/install.sh applies it on every deploy.
ALTER TABLE detection_events
    ADD COLUMN IF NOT EXISTS station TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS detection_events_station_time
    ON detection_events (station, detected_at DESC);
