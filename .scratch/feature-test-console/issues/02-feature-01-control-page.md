# 02. Feature 01 control and test page

Status: ready-for-human
Blocked by: 01
Execution: implementation-complete; browser-passed; hardware-proof-pending

## Scope

Build the first feature-owned page for model deployment and audio-to-text using the shared registry, API client, and system overview from ticket 01.

## Visual design

- Palette: chassis `#17242C`, surface `#24343C`, signal mint `#57D6AE`, recorder amber `#E7A55D`, paper `#E9F0E9`, fault coral `#E36F68`.
- Typography: Barlow Condensed for section titles, IBM Plex Sans for interface copy, IBM Plex Mono for telemetry, with offline system fallbacks.
- Layout: feature/status rail, central model/input workbench, persistent event log. Stack in that order on narrow screens.
- Signature: a signal spine showing actual input → model → transcript readiness/state. Use motion only to indicate live input; respect reduced motion.
- Keep copy operational, specific, and in sentence case. Do not add decorative gradients, placeholder results, or generic dashboard charts.

## Acceptance checklist

- [x] The UI reports service connection and API errors clearly.
- [x] The Feature 01 page presents model/runtime readiness and load/close lifecycle, defaulting to `turbo` when available.
- [x] The page supports WAV transcription, physical microphone start/stop, and both process topologies through the actual Feature 01 API.
- [x] Per-source language, chunk length, and silence threshold are configurable for process groups.
- [x] Transcript, ordered events, errors, session state, and measured/unavailable capacity values are distinct and clear.
- [x] The page shows user-verified reference CER only when a reference is supplied and the user confirms independent verification.
- [x] Headless Chrome browser tests cover catalog/load errors, loaded-handle WAV output, simulated microphone transcript/error/completion order and stop behavior, and service-disconnected rendering.
- [ ] At least one manual real-runtime/microphone run is recorded separately from injected test runs.

## Browser verification

All five browser scenarios passed in the local Chrome environment. After correcting the harness to wait for catalog initialization before clicking, five consecutive complete runs passed 25 checks with no failures or skips. The scenarios use injected service responses and event streams; physical hardware proof remains separate.

## Comments

Select the browser framework in ticket 01. Future pages are delivered within each future feature's vertical-slice tickets, not accumulated into a later frontend-only phase.

Corrective plan for review finding 4: expand the existing headless Chrome integration test using injected service and capture boundaries. Cover rendered catalog and load errors, transcript/error/completion events and stop behavior for a simulated microphone session, and a disconnected service state. Assert visible lifecycle and event output after user actions; do not rely on module or implementation snapshots. Discover a local Chrome/Chromium executable through environment and platform paths so the test can run across developer machines. Keep injected runs labeled as simulated, record manual hardware proof as pending, and update the browser checklist only after the browser tests execute successfully. Preserve the existing WAV and loaded-handle coverage. Completed: all five browser cases execute and pass in local headless Chrome; tests label their boundaries as simulated and assert the shared shell's rendered disconnected state. Manual physical microphone and production-runtime proof remains pending.

Implementation: the registry routes to a Feature 01-owned template, controller, and stylesheet with model/runtime readiness, finite WAV, host microphone, per-source process settings, process topology, Feature 01 capacity reports, transcript/configuration output, user-verified reference CER (operator-declared provenance), and signal-spine state. The shared shell loads feature assets from registered URLs and retains navigation, system overview, and shared events. Layout stacks by navigation → controls → event log on small screens; keyboard focus and reduced motion are supported. Headless Chrome coverage passes for an existing loaded-model handle and WAV transcript, catalog and model-load errors, simulated microphone transcript/error/completion ordering and stop behavior, and disconnected-service state. Service responses and the microphone event stream are injected; these checks are simulated browser integration proof, not physical hardware evidence. No new real OpenVINO/GPU or production recognition result is claimed. Physical-target acceptance remains recorded separately in the [system Feature 01 spec](../../new-speech-to-text/spec.md#acceptance-evidence-to-establish-before-treating-deployment-as-complete).
