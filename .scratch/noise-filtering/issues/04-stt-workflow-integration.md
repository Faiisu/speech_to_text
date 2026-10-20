# Apply noise filtering before speech recognition

Status: wontfix

Legacy outcome: Implemented optional run-scoped spectral denoising and bandpass filtering before silence gating and inference. Saved microphone/RTSP profiles persist the settings with disabled defaults; direct microphone/RTSP and WAV clip APIs accept the same configuration. Noise-only recordings are copied, downmixed/resampled and analyzed once, and the frozen profile is reused during capture recovery. Existing standalone file comparison remains available.

The [noise-filtering contract](../spec.md#speech-to-text-workflow-integration) owns processing/defaults; the [backend API contract](../../backend-transcription-api/spec.md#live-noise-filtering-settings) owns transport/persistence.

## Acceptance and verification

- Existing profiles migrate additively with both stages off; settings round-trip and copy through the profile editor.
- Prepared noise spectra survive deletion of source recordings and capture recovery; edits to a saved profile apply to later starts.
- Shared and spawned inference receive frame-aligned filtered audio, including partial stop chunks; invalid processing fails instead of bypassing filtering.
- Real workflow and HTTP pipeline tests replace only capture/decoder external boundaries and verify filtered model input plus keyword forwarding. Live monitoring remains captured input.
- `uv run --no-sync python -m pytest -q --import-mode=importlib`: 462 passed, 9 optional hardware/MQTT skips. The import mode avoids existing same-basename test collection collisions.
- Frontend production build passes; 24 focused profile, live-audio and standalone noise-filter browser checks pass.
- Real microphone acoustics, recognition accuracy, and UBX throughput remain hardware acceptance checks.
