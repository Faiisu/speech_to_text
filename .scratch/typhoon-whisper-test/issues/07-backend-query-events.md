# 07: Backend can be queried for detections

**What to build:** `GET /counts?word=&from=&to=` returns how many matching detection events exist in that time range, and `GET /events?word=&from=&to=&session_id=` returns the matching raw events — both computed from the same event log ingested in ticket 06, per ADR 0003 (counts are derived by querying the event log, never stored separately).

**Blocked by:** 06

**Status:** done

- [x] `GET /counts?word=<word>` returns the total number of matching events for that word
- [x] `GET /counts` also accepts optional `from`/`to` time-range filters, narrowing the count to events within that window
- [x] `GET /events` returns the matching raw events (word, detected_at, model, session_id) as a list, filterable by `word`, `from`, `to`, and `session_id`, any of which may be omitted
- [x] Querying for a word with no matching events returns a count of 0 / an empty list, not an error
- [x] Events ingested via ticket 06 are correctly returned by both endpoints
