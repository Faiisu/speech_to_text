# 01. Add IPCAM audio through RTSP

Status: wontfix

Legacy outcome: Implemented backend RTSP capture, workflow integration, persistent IPCAM profiles, direct API, recovery, and frontend source selection/lifecycle. The full Python suite passed (313 passed, 9 opt-in skips), frontend production build passed, and the full browser suite passed (25 passed, 1 optional MQTT broker skip). Local real RTSP/PyAV transport and the FastAPI-to-matching stop/flush pipeline were also exercised with deterministic inference. Physical camera authentication/codecs, real speech recognition quality, and UBX-330M throughput remain unverified without target devices.

Implement the [RTSP source contract](../spec.md), starting with backend capture, workflow, persistence, and HTTP integration, then the existing frontend Profile lifecycle.

## Acceptance criteria

Complete the acceptance and validation requirements in the specification. Preserve existing microphone behavior, keep resolved credentials private, and record software validation separately from physical camera/hardware proof.

## Comments

- Implementation requested on 2026-10-16. No real camera URL was supplied.
- Backend checkpoint: source configuration and safe PyAV capture, both execution modes, profile migration, direct API, run snapshots, and recovery are implemented. RTSP factories must be forwarded or the start fails safely; an unsupported adapter cannot fall back to a host microphone.
- Integration validation on 2026-10-16: `.venv/bin/python -m pytest -q --disable-warnings` passed with 313 passed and 9 skipped in 28.87 seconds. Skipped tests require opt-in hardware/model inputs or a real MQTT broker. The direct pytest executable encountered the existing repository import-path issue; invoking pytest as a Python module collected and ran the full suite successfully.
- A temporary local RTSP server sent actual interleaved RTP/TCP PCMU at 8 kHz. The real PyAV adapter produced mono float32 PCM at 16 kHz and stopped its capture thread. A second smoke exercised the real FastAPI RTSP endpoint, secret-reference resolution, capture, the session pipeline with an injected deterministic inference runtime, keyword matching, partial-chunk stop/flush, and terminal stopped status. Run snapshots/events contained neither the resolved URL nor its password. This proves software transport/integration, not camera authentication, recognition quality, or target hardware capacity.
- Frontend validation: `npm run build` passed. `npm run test:e2e` from `speech_to_text/frontend/` passed with 25 passed and 1 optional MQTT broker skip. Coverage includes RTSP create/edit/duplicate/start/monitor/stop with unavailable microphone discovery, factory delivery to the workflow fixture, and secret-reference create/edit/reload without resolved credentials in the UI/API. Existing default-microphone behavior remains covered by the shared fixture and microphone scenarios.
