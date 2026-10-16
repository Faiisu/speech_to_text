# Add configurable recognition language to workflows

Status: wontfix

Legacy outcome: Implemented the language configuration contract in the [backend API specification](../spec.md) and the [matching workflow specification](../../transcript-matching-forwarding/spec.md#matching-contract).

## Problem

Profiles, direct microphone requests, and finite clips currently select Thai implicitly. Callers need to choose Thai, English, or automatic recognition for mixed speech, and the selected setting must remain stable for active runs and recovery.

## Acceptance criteria

- Profile CRUD persists `language` with SQLite `TEXT NOT NULL DEFAULT 'th'`; additive migration is safe on existing databases, idempotent across reopen, and preserves existing row data.
- Profile JSON, direct microphone JSON, and clip multipart accept only `th`, `en`, or `auto`; omission defaults to `th`. Invalid values return HTTP 422 before model loading or source startup.
- Profile starts, direct microphones, clips, shared and process-isolated execution, and recovery pass the selected language into workflow configuration and matching.
- Run snapshots, matching lifecycle events, and outgoing records reflect the configured selection. `auto` selects automatic language detection, not a detected-language result; code-switched speech behavior depends on the selected model.
- `WordMatchingConfig` supports `th`, `en`, and `auto` with the existing NFC/casefold literal substring algorithm; no detection or matching algorithm change is introduced.
- Existing profiles and API callers that omit language retain Thai behavior.

## Validation

- Complete Python suite: 297 passed, 2 hardware tests skipped.
- Frontend production build passed.
- All 23 browser E2E tests passed, including selected-language persistence, duplication, active-run snapshots, Stop/Start application, and clip settings reaching the injected runtime.
- Adapter tests confirm CTranslate2 receives `None` for automatic detection and OpenVINO clears checkpoint language restrictions per call while explicitly requesting transcription without mutating shared generation configuration.
- Existing MQTT publish/subscribe tests passed against an isolated local Mosquitto broker.
- Real microphone/model behavior and recognition quality for code-switched speech on UBX-330M remain unverified; this change does not deploy to the device.

## Comments
