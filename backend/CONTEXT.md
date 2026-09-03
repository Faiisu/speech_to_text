# Backend

Owns the TimescaleDB-backed event log of keyword detections reported by the Speech-to-Text service, and exposes the HTTP API to ingest and query them.

## Language

**Detection event**:
One row recording that a target keyword/phrase was spotted: the word, when it happened, which model detected it, and which recording session it came from. The only unit of storage — a "count" is never stored directly, only derived by querying how many detection events match a filter.
_Avoid_: Hit, match (ambiguous outside this context)

**Session**:
One continuous recording run of the Speech-to-Text service, identified by a session ID generated when recording starts. Detection events are tagged with the session they came from, so a session's events can be queried together.
