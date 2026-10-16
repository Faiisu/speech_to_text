# 03. Record IPCAM RTSP audio for model tuning

Status: wontfix

Legacy outcome: Implemented IPCAM RTSP capture through the existing recording workflow and Model tuning page. Source validation, credential references, bounded WAV capture, persisted source identity, startup cleanup, partial audio after disconnect, playback/download, and replay through tuning trials are covered. Python suite: 317 passed, 9 optional hardware/MQTT skips. Frontend build and Model tuning E2E passed; the full browser suite passed with 26 passed and 1 optional MQTT skip. Actual camera authentication/codecs, recognition quality, and UBX-330M throughput remain unverified without camera/target hardware access.

Extend the existing recording workflow and Model tuning page according to the [IPCAM recording contract](../spec.md#ipcam-recording). Reuse the production RTSP capture adapter and the existing saved WAV trial flow.

## Acceptance criteria

Implement the source selection, validation, safe credential handling, bounded recording lifecycle, metadata persistence, and browser workflow described in the specification. Preserve microphone/upload behavior and validate startup failure cleanup and partial WAV retention on disconnect. Keep real camera and target hardware proof distinct from deterministic software checks.

## Comments

- Requested on 2026-10-16. No camera access was supplied.
- Software validation: `.venv/bin/python -m pytest -q --disable-warnings` completed with 317 passed, 9 skipped in 29.85 seconds. Browser checks from `speech_to_text/frontend/`: `npm run build` passed; focused Model tuning E2E passed all 3 scenarios after the final control-label and microphone-notice changes; the full E2E suite passed 26 scenarios with 1 optional broker skip before those presentation changes.
- Real transport smoke: a temporary local RTSP server sent interleaved RTP/TCP PCMU at 8 kHz. `POST /recordings` with a secret reference captured a mono PCM16 WAV at 16 kHz (3808 frames), stopped successfully, and restored source metadata through a new `RecordingService`. Capture loaded no model. Downloaded audio completed a clip tuning trial and keyword matching using an injected deterministic inference runtime. Neither snapshot nor manifest contained the resolved password.
- Disconnect smoke: terminating the real RTSP TCP connection failed the recording with safe `RTSP audio stream ended` text and preserved a readable partial WAV (3488 frames), with failed/RTSP metadata retained after reload. These transport checks do not prove real camera authentication or real-model recognition quality.
