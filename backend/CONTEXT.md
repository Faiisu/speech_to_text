# Backend

Owns the TimescaleDB-backed event log of keyword detections reported by the Speech-to-Text service, and exposes the HTTP API to ingest and query them.

## Language

**Detection event**:
One row recording that a target keyword/phrase was spotted: the word, when it happened, which model detected it, which **station** heard it, and which session it came from. The only unit of storage — a "count" is never stored directly, only derived by querying how many detection events match a filter.
_Avoid_: Hit, match (ambiguous outside this context)

**Session**:
One run of the Speech-to-Text service that produced transcripts, identified by a session ID generated when it starts. Detection events are tagged with the session they came from, so a session's events can be queried together.

**Station**:
The labelled microphone a detection came from ("Line 1"), stored as the label rather than an id so a row still reads correctly after the configuration changes. Empty for events from the CLI and the testing panel, which have no station — the column is defaulted rather than required so those keep working unchanged.

Note that a station's label is the *only* thing tying a row to a physical microphone. Two stations sharing a label would make the log ambiguous, which is why the configuration refuses to save one.

Note this covers **replay** sessions as well as live microphone ones — replaying a clip reports detections exactly like a live recording does, so events from a repeated benchmark of the same clip accumulate in this log. Nothing stored here distinguishes the two, so a `session_id` is the only way to tell one run's events from another's.
