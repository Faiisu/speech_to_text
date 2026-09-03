# 08: Speech-to-Text reports live detections to the backend

**What to build:** Running `transcribe.py --keywords ...` and speaking a target keyword results in a real detection event reaching the backend (ticket 06's ingest endpoint) and being queryable via ticket 07's API. Each recording session generates its own `session_id` at start, tagging every event from that run; the currently-selected `--model` is sent with each event. Per ADR 0003, this is a plain synchronous HTTP POST with a short timeout — if the backend is unreachable, the recording session logs a warning and keeps running rather than crashing.

**Blocked by:** 04, 06

**Status:** code complete, awaiting live end-to-end verification (sandbox has no mic access; needs a human to run it interactively)

- [x] A `session_id` (UUID, printed at recording start) is generated once per recording session and attached to every detection event from that session
- [x] Each detected keyword (from ticket 04's `spot_keywords`) triggers a `POST /events` to the backend with `word`, `detected_at`, `model`, and `session_id` — verified with a standalone test hitting the real backend from tickets 05-07
- [x] The POST uses a 2s timeout, so a hung backend delays at most one chunk cycle rather than blocking indefinitely
- [x] If the backend is unreachable, the script logs a `[backend] warning: ...` and continues — verified by pointing `--backend-url` at a closed port and confirming no crash
- [x] Debounce (ticket 04) correctly prevents a second `POST` for the same overlap-duplicated keyword — verified only one event was stored despite two detections 4s apart
- [ ] End-to-end verified live: speaking a keyword during a real mic recording produces an event queryable via `GET /events` and reflected in `GET /counts`
