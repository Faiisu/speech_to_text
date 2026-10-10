# 01: Verify microphone connection

**What to build:** Repeatable tests and a live verification procedure proving that a station connects to its named microphone, receives audio, stops cleanly, and recovers when the microphone returns. Extend existing coverage instead of duplicating tests. Follow the station and device-name definitions in the domain glossary.

**Blocked by:** None (can start immediately).

Status: ready-for-agent

**Audit protocol:** [Feature verification audit](../spec.md)

- [ ] A valid, unambiguous device name opens the intended input and delivers non-empty samples to the station capture path.
- [ ] Missing and ambiguous names produce actionable errors without binding to another microphone.
- [ ] Permission denial and an occupied microphone report failure without leaving capture threads or resources running.
- [ ] Stopping a station releases its input; starting it again succeeds.
- [ ] Disconnecting a microphone is visible in station health, and reconnecting it resumes capture through the existing watchdog without changing its station identity.
- [ ] Deterministic tests simulate device enumeration, input callbacks, failures, and reconnects without requiring a physical microphone.
- [ ] A live smoke test records the exact selected device, station, received sample count, lifecycle observations, and outcome; silence alone is not a connection failure.
- [ ] Record PASS, FAIL, or SKIP for each case, with evidence and reasons. Unavailable hardware or permissions are SKIP, never PASS; report simulated and live results separately.

## Comments

### 2026-10-09 current-state audit

- **PASS, deterministic:** exact device-name resolution selected the intended enumerated input; missing and ambiguous names failed without fallback. Permission-denied and device-busy simulations reached the station error callback and left the capture thread stopped.
- **PASS, live capture/lifecycle:** `uv run --no-sync python tests/proof/microphone_smoke.py --device 'MacBook Air Microphone' --duration 1` opened exact name `MacBook Air Microphone` (PortAudio index 0 for this run) for station `hardware-proof`, delivered 14,505 callback samples and 3 chunks in the first run, then stopped its capture and worker threads and reopened the same name with 5,535 callback samples and 1 chunk. Silence did not count as failure.
- **FAIL, deterministic active disconnect:** `tests/microphone/test_capture.py::test_active_input_stream_disconnect_is_detected_and_watchdog_reopens_same_station` is a strict expected failure. Marking an open stream inactive produced no error or watchdog restart; `StationCapture._source_alive` does not check PortAudio stream state.
- **SKIP, live reconnect:** the physical unplug/replug interaction was not performed. Run the smoke runner with `--manual-reconnect` when a person can perform that action.

Permission-denied and busy behavior is verified only by controlled exceptions. Their live permission/resource conditions remain unobserved. This ticket remains open because active disconnect recovery is not demonstrated and currently fails its deterministic regression check.
