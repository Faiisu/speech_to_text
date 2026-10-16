# 02. Configure camera credentials through the frontend

Status: wontfix

Legacy outcome: Implemented frontend camera credentials for Profiles and Model tuning, safe URL normalization, write-only API credentials, encrypted saved passwords, and profile edit/duplicate behavior. No further issue work remains.

Implement the [frontend camera credential contract](../spec.md#frontend-camera-credentials) for Profile and Model tuning. Users can enter username/password or paste an authenticated URL without configuring environment variables. Keep passwords write-only in the API and encrypted for saved profiles.

## Acceptance criteria

Meet the normalization, safe metadata, encryption/key lifecycle, profile edit/duplicate, recording, and validation acceptance criteria in the specification. Preserve existing direct URL and environment-reference inputs.

## Comments

- Requested on 2026-10-16 after an authenticated camera URL was rejected by the existing direct-URL validation. Use synthetic credentials in code/tests; never copy the user's actual camera password into the repository.
- Validation: full Python suite passed (327 passed, 9 skipped); frontend production build passed; browser suite passed (28 passed, 1 skipped). Focused RTSP API and Model tuning credential browser checks passed after final integration fixes.
- A temporary local RTSP server verified actual PyAV/TCP authentication, reserved characters in synthetic credentials, saved-profile reopen/start, and finite recording capture. Actual Hikvision hardware and recognition quality were not verified in this environment.
