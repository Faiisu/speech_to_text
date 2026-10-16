# 02. Import audio files for model tuning

Status: wontfix

Legacy outcome: Implemented and verified. WAV imports preserve original bytes; audio-only MP3 and M4A imports are converted to bounded mono PCM16 WAV. Browser uploads use the same retained audio library and trial flow as host recordings.

## Scope

Allow browser users to import local audio into the existing recording library, then replay and compare it through model tuning. The authoritative format, size, validation, API, metadata, and storage contract is in the [model-tuning specification](../spec.md).

## Acceptance checklist

- [x] Upload WAV, MP3, and M4A through the recording API with a 20 MiB input limit.
- [x] Preserve valid PCM16 WAV bytes and convert MP3/M4A to mono 16 kHz PCM16 WAV with a 120-second decoded-duration limit.
- [x] Keep original filenames as sanitized display metadata and derive all storage names from generated IDs.
- [x] Restore imported recordings after service restart, return them from list/download, and delete their audio and metadata.
- [x] Reject invalid, empty, unsupported, truncated, and over-duration uploads without leaving recording files or manifests.
- [x] Import while microphone capture is active without changing the active capture.
- [x] Keep import independent of microphone access and model loading.

## Validation

- Complete Python suite: 305 passed, 2 hardware tests skipped. The recording API suite includes 11 passing tests.
- All 24 browser E2E tests passed, including invalid upload feedback, byte-preserving WAV downloads, duplicate filenames with separate IDs, retained imports after page reload, and imported-audio transcription with selected settings.
- Actual PyAV MP3/AAC encoders generated stereo 44.1 kHz test inputs; conversion produced mono 16 kHz PCM16 WAV. Over-duration compressed audio, invalid media, missing codec support, and storage errors left no retained files.
- A concurrency check blocked upload validation and proved an active microphone could still be stopped independently.
- Frontend production build and dependency lock validation passed. Existing MQTT tests used an isolated local Mosquitto broker.
- No deployment or real-model recognition-quality test was performed on UBX-330M.
