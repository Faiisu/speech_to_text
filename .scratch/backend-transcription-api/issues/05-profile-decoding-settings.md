# 05. Configure decoding settings through microphone profiles

Status: wontfix

Legacy outcome: Profile decoding settings are implemented through the editor, persistent store, run snapshots, and recovery. Software and browser verification pass; real-model quality/performance checks on UBX-330M remain unverified.

## Scope

Implement the [profile decoding settings contract](../spec.md#profile-decoding-settings) through the existing Profile editor, API, SQLite store, and workflow start/recovery path. Reuse the Feature 01 defaults and decoder validation. No runtime hot updates, additional model settings, new dependencies, or forced shared-model reloads.

## Acceptance checklist

- [x] The editor can set, save, reopen, and duplicate all five decoding options with their documented defaults and ranges.
- [x] Invalid values are rejected by frontend and API; partial or omitted options normalize to complete defaults.
- [x] Existing SQLite profiles migrate additively without losing data and remain readable after reopen.
- [x] Both execution modes receive saved options; running and recovering workflows retain their start-time snapshot after profile edits.
- [x] Stop/Start applies newly saved settings and run status reports the effective snapshot.
- [x] Backend/workflow tests, browser lifecycle regression, frontend build, and desktop/mobile visual verification pass.

## Comments

2026-10-16: Implementation assigned to a dedicated feature-development sub-agent. The parent owns specification updates, review, and integration verification.

Final parent verification: `MQTT_TEST_BROKER_URL=mqtt://127.0.0.1:18884 .venv/bin/python -m pytest -q --tb=short` — 269 passed, 2 hardware tests skipped. `npm run build` and `MQTT_TEST_BROKER_URL=mqtt://127.0.0.1:18884 npm run test:e2e` from `speech_to_text/frontend/` — build passed and all 22 browser tests passed, including real MQTT publish/subscribe. The parent also saved/reopened custom values through the editor and visually inspected desktop and mobile layouts using a separate temporary backend. Test-only servers and broker are removed after verification.

Visual review corrected a hidden checkbox inherited from radio-option styling; the new native checkbox shows its checked state and help text separately. A pre-existing input-overflow regression test now joins its callback thread before checking frame delivery, removing a scheduling race revealed by the combined test run without changing capture behavior.
