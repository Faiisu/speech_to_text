# 01. Host recording and model comparison

Status: wontfix

Legacy outcome: Implemented and verified with injected host audio/runtime boundaries. Physical microphone and real-model acceptance on UBX-330M remain unverified.

## Scope

Implement short backend-host microphone capture and a browser workflow to save, replay, and compare samples against configurable speech-to-text settings.

The authoritative recording, persistence, endpoint, and tuning behavior is in the [model-tuning specification](../spec.md).

## Acceptance checklist

- [x] Record at most 120 seconds from a selected host microphone, retain silence, and save a mono PCM16 WAV.
- [x] Capture streams to persistent data storage with bounded memory and no model loading.
- [x] Stop, overflow, fatal errors, maximum duration, and backend shutdown produce usable lifecycle states and retain captured audio where possible.
- [x] Generated recording IDs safely address recordings; finalized recordings survive backend restart and can be deleted.
- [x] Browser flow can listen to/download a saved recording and rerun it with model/runtime/decoding options and keywords.
- [x] Show transcript, matches, and RTF for each rerun.
- [x] Validate capture/storage behavior with injected audio sources and exercise API/UI behavior without real microphone hardware.

## Validation

- The complete Python suite passed: 286 tests passed, 2 hardware tests skipped.
- Frontend production build passed.
- All 23 browser E2E tests passed, including recording, valid WAV download, and rerunning the same audio with separate configuration snapshots. Existing MQTT delivery tests used an isolated local Mosquitto broker.
- Manual browser checks confirmed WAV playback and the model-tuning desktop/mobile layout without horizontal overflow; all five mobile navigation links remain visible.
- Recording tests cover stereo downmix, silence retention, input overflow, fatal capture errors, storage failure, time limits, shutdown, persistence, and deletion.
- No production device recording or model-quality claim is based on these injected-boundary tests.
