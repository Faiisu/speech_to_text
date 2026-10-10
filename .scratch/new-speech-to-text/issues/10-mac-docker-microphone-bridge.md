# 10. Connect Mac microphones to the Docker Desktop session

Status: wontfix
Execution: completed
Blocked by: 03

Legacy outcome: the Mac Docker Desktop host bridge belonged to the retired Control Center architecture. The current application uses the React/FastAPI flow documented in [`docs/architecture.md`](../../../docs/architecture.md); this bridge is not part of the current interface.

## Scope

Provide microphone capture for the Mac Docker Desktop profile without changing Linux native capture. The local Control Center starts and stops one Feature 01 session through a loopback-only Mac host bridge. The bridge forwards bounded PCM batches into that session so Feature 01 retains buffering, chunking, partial flush, inference events, and telemetry. Process-group and capacity capture remain unavailable in this profile.

## Acceptance checklist

- [x] Mac bridge binds only to `127.0.0.1`, supports wildcard CORS for Control Center hostname aliases, and documents foreground lifecycle commands.
- [x] Feature 01 enables remote PCM ingress only when the Mac Docker profile turns it on; each session uses a random bearer token.
- [x] Audio ingress limits each request to one second, validates float32 alignment and finite samples, and rejects unauthenticated or oversized batches.
- [x] Host capture uses a bounded callback queue, preserves callback ordering, drains accepted frames before stopping Feature 01, and fails visibly on overflow or transport errors.
- [x] Repeated start requests cannot replace an active source or leak a second Mac capture stream.
- [x] Browser control uses wildcard CORS for the loopback bridge by default; optional strict mode accepts equivalent `localhost`, `127.0.0.1`, and `[::1]` origins on the configured HTTP port while rejecting other hosts, schemes, and ports.
- [x] Linux native capture continues to use the existing sounddevice adapter; Mac Docker process-group and capacity controls are disabled in the page.
- [x] Deterministic API coverage proves token enforcement, sample forwarding, chunk ordering, and session completion through the existing Feature 01 pipeline.

## Comments

The bridge captures the selected Mac device with the host `.venv`'s `sounddevice` installation and posts PCM batches to the existing container API. The host service remains a separate foreground process that operators restart after login or reboot.

Deterministic verification: `.venv/bin/python -m pytest tests/control_center/test_api.py tests/control_center/test_mac_microphone_bridge.py tests/control_center/test_feature_01_browser.py -q` covers authenticated audio and failure reporting, callback-size-independent sample ordering, duplicate source rejection, and both native and Mac-bridge page lifecycles. Parent-run physical capture in the Docker profile established actual input, final-chunk flush, database RTF records, and Grafana visibility; transcript accuracy remains unverified.

Final deployment verification on 2026-10-09: the rebuilt Mac Docker service and host bridge captured 6.293375 seconds from `MacBook Air Microphone` using the real `turbo` CTranslate2 int8 model. Both the 5-second chunk and final 1.293375-second chunk completed, with RTF values of 1.6120 and 5.2532 respectively. The read-only database role and Grafana datasource returned both rows with timestamps, container PID, source ID, and sequence. These ambient-audio runs used a zero silence threshold to exercise inference; they do not prove transcript accuracy or real-time performance. Actual Chrome rendering populated Mac device options and disabled unsupported process controls. Evidence: [Mac Docker microphone proof](../evidence/mac-docker-microphone-20261009.json).

Full software suite: `.venv/bin/python -m pytest tests -q` passed 112 tests and skipped the 2 opt-in hardware proofs. The deployment remains running with one loaded model and no active capture sessions. The bridge was launched as a detached host process for this session; it is not installed as a login service.

Origin regression: default wildcard CORS allows the browser page at `http://localhost:18766` to control the bridge listening at `127.0.0.1:18767`. PCM writes still require the per-session bearer token. Live HTTP handler regressions cover wildcard and strict-origin modes, device enumeration, preflight, session start/stop, and strict-mode rejection of non-loopback hosts, unexpected schemes, paths, credentials, query strings, and port mismatches.

Live wildcard deployment verification: restarting the host bridge with `SPEECH_TO_TEXT_BRIDGE_CONTROL_ORIGIN='*'` fixed the reported `localhost:18766` failure. The same HTTP reproduction changed from 403 to a successful Mac device catalog and physical microphone start/stop, with `Access-Control-Allow-Origin: *`; preflight returned 204. Actual Chrome rendering on `http://localhost:18766` populated `MacBook Air Microphone`. Evidence is recorded in the existing microphone proof above. The Docker inference service did not require a rebuild for this host-handler change.
