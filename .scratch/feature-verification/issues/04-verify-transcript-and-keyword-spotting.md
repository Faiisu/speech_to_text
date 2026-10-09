# 04: Verify transcript and keyword spotting

**What to build:** Tests proving that transcripts from the verified pipeline generate the expected keyword events and station-local detection totals. Follow the existing keyword-spotting and debounce definitions and ADR; this ticket verifies their behavior rather than changing the matching algorithm.

**Blocked by:** 03: Verify microphone-to-model pipeline.

**Status:** ready-for-agent

**Audit protocol:** [Feature verification audit](../spec.md)

- [ ] Controlled transcripts containing Thai keywords, multiple keywords, and phrases emit the expected detections; English matching is case-insensitive.
- [ ] Empty transcripts, transcripts without a configured keyword, and an empty keyword configuration emit no detections.
- [ ] A repeat detection inside the debounce window, including a repeat caused by chunk overlap, is suppressed. Detections at the boundary and after the window are accepted.
- [ ] Repeating a keyword multiple times in one transcript produces one detection for that configured keyword, consistent with spotting rather than precise occurrence counting.
- [ ] Debounce state is isolated by station and keyword; one station cannot suppress another station's detection.
- [ ] Emitted events and backend reports carry the expected keyword, station, model, and session metadata where each interface supports them; station detection totals advance only for emitted detections.
- [ ] Deterministic tests control transcript content and time, verifying exact event sequences and report calls without a real model.
- [ ] A fixed clip with declared target keywords produces the expected detections through the real pipeline. Retain the transcript and detection timeline as evidence, using acceptance criteria based on keywords rather than exact full-transcript equality.
- [ ] Record PASS, FAIL, or SKIP per case with reasons, separating mocked spotting results from real-model pipeline evidence.

## Comments

### 2026-10-09 current-state audit

- **PASS, deterministic:** controlled Thai single/multiple-keyword and phrase matching, case-insensitive English, empty/non-matching transcripts, one alert for repeated occurrences in a transcript, overlap debounce, exact-boundary acceptance, after-window acceptance, and station-local debounce state all passed. Local event metadata, backend request metadata, and station totals matched expected emitted detections.
- **PASS, real pipeline execution:** production replay completed with a loaded runtime and drained queue, as recorded in ticket 03.
- **FAIL, target phrase:** the local clip transcript lacked `สวัสดี`, so the real pipeline emitted no target detection. Clip provenance is unverified; this is an observed target-check failure for the recorded file.
- **FAIL, HTTP rejection visibility:** `tests/keyword_spotting/test_spotting.py::test_non_success_backend_response_is_reported_as_not_persisted` is a strict expected failure. A mocked HTTP 503 returns silently from `report_event`; the local keyword event still appears, while the caller gets no indication that persistence was rejected. The separate isolated DB proof in ticket 05 verifies successful HTTP 201 ingestion only.

This ticket remains open until real-model target evidence and observable backend rejection behavior are established.
