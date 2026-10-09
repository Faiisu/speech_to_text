# Feature Test Frontend and System Control Center

## Purpose

Build the browser frontend as the shared control center for the speech-to-text system. Developers and operators use it to configure, run, observe, and test each implemented feature through that feature's public API.

The control center grows with the system. Every feature is delivered with its own page contribution and feature-specific test structure as part of the same development slice. Feature specs and automated contract tests remain the source of truth for behavior.

## Scope and growth model

The first vertical slice is Feature 01, model deployment and audio-to-text. The current system spec defines no later feature contracts, so the first release must not invent their settings or workflows.

Organize frontend and Python service adapters by feature. Each feature contributes a typed page module, API adapter, and test entry points to an explicit feature registry. Adding a feature means adding its module and registry entry; shared navigation and the system dashboard discover it from the registry without edits to a monolithic page. Keep registration explicit and reviewable rather than loading arbitrary plugins at runtime.

For each future feature, develop its backend callable API and frontend page together against the feature spec. Include the feature page, its interactive test flow, and its UI/API integration checks in that feature's implementation tickets. Do not defer all frontend work until backend feature development is complete.

## Control center experience

The application grows from feature-focused test pages into one control center. Its shared shell provides:

- **System overview:** service readiness, loaded model/runtime, active inputs and sessions, and recent errors.
- **Feature navigation:** one page per implemented feature, with implementation/test status and links to that feature's contract.
- **Feature workspace:** feature-specific configuration, real system actions, test actions, live outputs, lifecycle state, errors, and measurements.
- **Shared run output:** structured, timestamped events tagged by feature and source/session so users can follow concurrent work.

Each feature page supports the operations that its own spec defines. It must make the feature testable through the same public API used by the system, and it may add operator controls as the system grows. Do not add generic controls whose behavior has no feature contract.

## System observation

There is no separate system-observation dashboard or historical telemetry store. Feature 01 exposes local per-chunk RTF in clip results, microphone events, and process-group results; its measurement contract is in the [Feature 01 spec](../new-speech-to-text/spec.md#per-chunk-performance-measurement).

## Feature 01 first vertical slice

### Model and runtime

- Show the catalog returned by `list_available_models()`, including installation/readiness, supported runtime/precision, and why an option is unavailable.
- Let the user select a compatible model, runtime, and precision, then load or close it.
- Show handle lifecycle state and reuse the loaded handle until explicitly closed or the service exits.

### Finite audio

- Accept a WAV file and the Feature 01 flow settings: language, chunk length, silence threshold, and supported decoding controls.
- Call `transcribe_clip` with the selected loaded handle.
- Show transcript, elapsed time, errors, and selected configuration as distinct data.
- Show CER only when a verified reference transcript is supplied and the user runs that comparison. A transcript alone is not an accuracy proof.

### Microphone and process flows

- Show host input device names and allow the OS default or a named device. The service captures directly on its host. When the service cannot access a Mac microphone directly, an optional loopback-only host bridge captures audio and streams bounded PCM batches into the existing Feature 01 session; the browser does not request microphone access.
- Start/stop `start_microphone_flow` and stream ordered transcript, error, and completion events with source ID and sequence.
- Support shared-model and per-input-model process topologies, device lists, and per-source flow settings.
- Show per-source lifecycle, queue timeout/errors, and measured capacity output. Distinguish pass, fail, unavailable, and inconclusive; never show unavailable values as zero or passing.

## Feature contribution contract

Every new feature's implementation plan must identify its frontend page and test structure alongside its callable backend contract. A feature contribution includes:

1. A stable feature ID, display name, summary, and pointer to the authoritative feature spec.
2. A feature-owned page template, controller module (`mount(root, context)`), stylesheet, and API adapter, registered through one explicit feature contribution. The shared shell loads the registered local assets and owns only navigation, system overview, and shared events; it does not contain feature page markup or event handlers.
3. Configuration controls derived from that feature's documented options and defaults.
4. Test actions that exercise the real feature API and display outputs, errors, and lifecycle state.
5. Automated UI/API integration checks using boundary injection only where hardware or an external runtime is required.
6. A clear label for simulated/injected checks and a separate record of any physical-hardware proof still required.

The feature's acceptance criteria are incomplete until the page is reachable through the registry and its test flow covers the feature's documented contracts. Features that have no meaningful UI action must document why and provide a visible test/status view instead.

## Architecture and safety

- The browser calls a Python HTTP adapter which invokes callable feature APIs in-process. The browser never imports or duplicates Python feature logic.
- Bind the directly run service to loopback (`127.0.0.1`) by default. The shared Control Center CORS middleware allows all origins, methods, and headers with credentials disabled; the Control Center has no authentication. Use an explicit `0.0.0.0` bind only on a trusted network and apply host firewall rules as needed.
- Use request/response for catalog, configuration, lifecycle, and finite actions. Use an event stream for long-running feature sessions; choose the transport while defining the API contract.
- Upload WAV bytes to the service, validate them through Feature 01, and avoid retaining uploaded clips after the request.
- Keep shared shell/navigation/event handling in a common control-center module. Keep each feature page, API adapter, and UI tests in its feature-named module.
- Show real readiness and API results. Automated tests may inject external boundaries, but identify those runs as simulated and never present them as hardware proof.
- Feature implementations own input validation. The frontend renders validation and lifecycle errors returned by the public feature service.
- Keep interactive test-run history in the active browser session. Per-chunk inference measurements are returned by Feature 01 and are not persisted as historical telemetry.

## Implementation sequence

1. Define the extension contract and build the local API/shell foundations: loopback-by-default direct service, shared API client, event stream, feature registry, navigation, and system overview.
2. Deliver the Feature 01 page as a vertical slice: model lifecycle, clip transcription, microphone capture, process topologies, and observable output.
3. For every later feature, add its page, API adapter, test controls, and UI/API integration checks in parallel with that feature's backend implementation.
4. Expand the shared overview as features contribute system-level lifecycle and health information. Preserve each feature's own page as the place to configure and test its behavior.
5. Run end-to-end browser checks with injected boundaries, then record real-runtime and hardware proofs separately on the relevant target machines.
6. Return Feature 01 per-chunk measurements with its clip and process-group results and publish them on its microphone event stream. See the [Feature 01 spec](../new-speech-to-text/spec.md#per-chunk-performance-measurement).

Use feature-based modules as required by the repository's [agent rules](../../AGENTS.md#modular-programming-and-feature-based-folders). Track control-center foundations here; track each feature's page and tests in that feature's own issue directory.

## Acceptance criteria

- The shared shell lists only registered, documented features and routes each to its own page module.
- Feature 01 can be configured, loaded, tested with a WAV, tested with a physical microphone, stopped cleanly, and tested with both process topologies through the real feature API.
- The system overview shows service/model/session readiness and recent errors using actual service state.
- Per-chunk measurements are available with the Feature 01 operation that produced them; no telemetry database or Grafana deployment is required.
- A future feature can add its page and test structure by adding a feature-owned module and registry contribution without rewriting existing pages.
- Each feature is developed with its frontend contribution and UI/API tests alongside its backend API.
- Simulated, real-runtime, and hardware proof statuses are distinguishable.
- The directly run local service binds to loopback by default; an explicit `0.0.0.0` bind is available for trusted networks.
- Existing feature contract suites run separately and continue to validate backend contracts independently of the browser.

## Decisions to make during implementation

- Use FastAPI for the local Python service and native HTML/CSS/JavaScript modules for the first frontend. Serve the UI from the same local service; avoid a separate Node build tool until a feature requires it. The service can serve static files and the API, while `TestClient` supports direct integration testing of the app lifecycle and routes ([FastAPI static files](https://fastapi.tiangolo.com/tutorial/static-files/), [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/)).
- Define exact API routes and registry types before implementing the first page. The API contract derives from Feature 01's public functions and must support feature-owned adapters.
- Keep the feature registry explicit and typed; avoid runtime loading of arbitrary modules.

## Feature 01 visual direction

Audience: developers and operators checking live speech capture and model behavior. The page's primary job is to start a real test run and make its state and result easy to inspect.

| Token | Value | Use |
| --- | --- | --- |
| Slate chassis | `#17242C` | App background and navigation rail |
| Control surface | `#24343C` | Work areas and cards |
| Signal mint | `#57D6AE` | Ready/running signal and selected navigation |
| Recorder amber | `#E7A55D` | Primary run/stop actions and attention state |
| Paper | `#E9F0E9` | Main text and transcript surface |
| Fault coral | `#E36F68` | Failed/error state |

Use Barlow Condensed for restrained section titles, IBM Plex Sans for interface copy, and IBM Plex Mono for model IDs, timestamps, sequences, and measured values. Provide system sans/monospace fallbacks so the console remains usable offline.

Choose a three-zone workbench: feature navigation and system status on the left; model/input configuration and run controls in the center; a persistent event log on the right. A single-column page is simpler but hides the live event stream while the user adjusts or checks run settings. On narrow screens, stack these zones in navigation, controls, then events order.

```text
Option A — live workbench (chosen)
┌ feature/status ┬ model + input + actions ┬ live events ┐
│ feature pages  │ signal spine            │ 00:00.12    │
│ system state   │ file / microphone       │ transcript  │
│                │ run configuration       │ errors      │
└────────────────┴─────────────────────────┴─────────────┘

Option B — one-column sequence
┌ feature selector ──────────────────────────────────────┐
│ model + input settings                                  │
│ signal spine and run actions                            │
│ events appear below the controls                        │
└────────────────────────────────────────────────────────┘
```

Choose A because live transcription needs the operator to see the active event stream beside the configuration and lifecycle controls. Collapse A into the order shown in the responsive rule rather than letting the event log fall below a long form.

The signature is a compact **signal spine** beside the active test: input → model → transcript, with each node reflecting its actual readiness or session state. Use one restrained pulse on live input; keep other motion static, honor reduced-motion preference, and do not decorate empty/failure states.

Design critique: an all-black surface with neon green would resemble a generic developer dashboard and would make every status compete for attention. The selected slate/paper palette gives transcripts and settings readable contrast; mint and amber appear only where audio flow or operator action needs emphasis. The signal spine supplies the memorable visual without adding unrelated charts or animation.

## Local service API and run command

The service is `speech_to_text.control_center`, built with FastAPI and native browser modules. Install the `control-center` optional dependency group and run `python -m speech_to_text.control_center` at the repository root; it binds to `127.0.0.1:8765` by default and accepts an explicit `0.0.0.0` bind. Shared CORS middleware allows all origins, methods, and headers with credentials disabled; the Control Center has no authentication. `/` serves the page from the same origin as `/api` and `/assets`.

The explicit registry provides `GET /api/features` and `GET /api/system`. A feature contribution supplies its own router beneath `/api/features/<feature-id>`. Feature 01 exposes:

| Method and path | Operation |
| --- | --- |
| `GET /catalog` | Rescan available model/runtime readiness |
| `GET /devices` | List host input devices when capture support is installed |
| `POST /models` | Load a model and return an owned handle ID |
| `DELETE /models/<handle-id>` | Stop owned flows and close a model handle |
| `POST /clips` | Validate and transcribe an uploaded WAV without retaining it |
| `POST /microphones` | Start a Feature 01 microphone session |
| `GET /capture-capabilities` | Report configured microphone capture adapters |
| `POST /sessions/<source-id>/audio` | Append one authenticated, bounded PCM batch in the Mac profile |
| `POST /sessions/<source-id>/capture-error` | End a Mac host-bridge session with a typed capture error |
| `POST /process-groups` | Start shared-model or per-input-model capture processes |
| `POST /capacity` | Run the Feature 01 microphone-paced capacity measurement and return its real verdict/measurements |
| `GET /events/<source-id>` | Stream typed source events with transport sequence numbers |
| `POST /sessions/<source-id>/stop` | Stop a microphone flow and flush accepted audio |
| `POST /process-groups/<group-id>/stop` | Stop all group flows and model workers |

The adapter invokes `speech_to_text.features.model_deployment`; it does not duplicate feature validation or inference. `create_app()` accepts boundary factories for deterministic API integration checks. Process and capacity operations accept per-device flow settings aligned with their device list. The capacity route calls the Feature 01 measurement path, and its report preserves pass, fail, unavailable, and inconclusive states with missing measurements represented as unavailable. Injected runtime and capture checks remain separate from production runtime and physical hardware evidence. Control-center browser event history is held in memory for the current page session only. Reference CER is calculated by the browser only after the operator checks the independently verified reference declaration; the app does not independently validate reference provenance.
