# 05: Verify detection event counting

**What to build:** Integration tests proving that accepted detections travel from the verified keyword pipeline through backend ingestion to the event log and can be queried as correct counts. Use an isolated test database. Follow the event-log ADR: these totals count persisted, debounced detection events, not every spoken occurrence.

**Blocked by:** 04: Verify transcript and keyword spotting.

Status: ready-for-agent

**Audit protocol:** [Feature verification audit](../spec.md)

- [ ] An empty matching event set returns a count of zero; ingesting N distinct test events returns N and exposes those events through the query API.
- [ ] Counts correctly apply supported word, station, and inclusive time-range filters, individually and together, including events exactly at each time boundary.
- [ ] Event queries correctly isolate session IDs. Do not require a session filter on the counts endpoint, which does not currently support it.
- [ ] A controlled keyword sequence creates exactly the expected persisted rows and count delta; suppressed detections create no rows or count increments.
- [ ] Stored event metadata identifies the expected model, station, and session, including events produced by the real pipeline proof.
- [ ] Clearing the live feed leaves persisted events and derived counts unchanged.
- [ ] Network failures and non-success ingestion responses must not be reported as successful persistence. Tests distinguish local keyword events from committed database rows and expose any current behavior gap as a failure.
- [ ] Database tests use an isolated database with the production schema and migrations. Setup and cleanup leave existing application data untouched and repeated runs produce the same expectations.
- [ ] Record PASS, FAIL, or SKIP per case with evidence and reasons, including input sequence, API responses, and persisted count deltas. Missing database or real pipeline prerequisites are SKIP, never PASS.

## Comments

### 2026-10-09 current-state audit

- **PASS, isolated TimescaleDB/API:** ran `tests/proof/database_event_log.py` against `stt_feature_test`, a dedicated `_test` database with the production init schema and migration. Seven uniquely tagged HTTP ingests returned 201. Adversarial decoys verified exact count results: absent word 0; target word 5; target word plus station 4; inclusive time range 3 and 2; all combined filters 2. `/events` isolated 2 rows in the requested session and 1 in the other session.
- **PASS, controlled spotting→persistence:** target sequence at seconds `0`, `4`, `4.5`, `10` emitted 3 local detections and 3 HTTP reports. The 4-second repeat was suppressed; persisted count delta was exactly 3. Stored model/station/session metadata matched. Runner cleanup removed only its generated session rows and passed.
- **PASS, feed clearing behavior:** existing `tests/test_stations.py::test_clearing_the_feed_leaves_stored_detections_alone` confirms clearing recent UI data performs no backend request. Persisted rows and derived counts are separate from that in-memory feed.
- **FAIL, non-success ingestion visibility:** see ticket 04 strict expected failure. A 503 response is ignored by `report_event`; the isolated proof used successful 201 responses.
- **SKIP, real-model rows:** the real replay target check in ticket 03 emitted no keyword event, so this run could not verify metadata from a real-model detection persisted through the database.

The database proof was repeated after the runner changes; each execution used unique inputs and cleaned its own rows. This ticket remains open for non-success response behavior and a real-pipeline event persisted from a declared target clip.
