# 06: Backend can ingest a detection event

**What to build:** `POST /events` accepts a detection event (`word`, `detected_at`, `model`, `session_id`, per `backend/CONTEXT.md`'s "Detection event") and stores it as a row in a TimescaleDB hypertable. Verifiable by posting a test event with `curl` and confirming it landed (via a direct DB query or a temporary read-back response).

**Blocked by:** 05

**Status:** done

- [x] `POST /events` accepts `word`, `detected_at`, `model`, `session_id` and returns success (201, `{"status": "stored"}`)
- [x] The event is persisted in TimescaleDB as a row in an append-only event log (per ADR 0003 — not a mutable counter)
- [x] The events table is set up as a TimescaleDB hypertable on `detected_at` (confirmed via `timescaledb_information.hypertables`)
- [x] Posting a malformed event (missing required field) returns a clear error (422 with field-level detail), not a silent failure or crash
- [x] A posted event can be confirmed to exist afterward (verified via direct `psql` query)
