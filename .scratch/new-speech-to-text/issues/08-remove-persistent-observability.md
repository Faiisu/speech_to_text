# 08. Remove persistent observability deployment

Status: ready-for-human
Execution: completed
Blocked by: none

## Scope

Remove the current application's TimescaleDB/Grafana deployment and persistence path while keeping Feature 01 per-chunk performance measurements available at the point of use. Retain the Linux `speech-feature-01` project and port `8765`, the Mac `speech-mac-test` project and port `18766`, model/cache mounts, Linux UID/audio/GPU and PipeWire access, and the Mac host microphone bridge.

The deployment owns Compose files, Dockerfiles, environment examples, deployment scripts/assets, operator documentation, and this ticket. Runtime/API implementation and tests are owned by the pipeline work. Preserve historical audit tickets, evidence, and the archived PoC; historical statements are not current deployment instructions.

## Acceptance checklist

- [x] `compose.speech-service.yml` and `compose.mac-test.yml` each define only the speech service, with no database dependency, Grafana service, telemetry DSN, or observability credential interpolation.
- [x] Existing Linux and Mac project names and API ports remain unchanged. Model/cache mounts, Linux UID/GID/audio/GPU/PipeWire bindings, Mac profile platform, and host microphone bridge remain configured.
- [x] Image installs no telemetry/database extra. Production health checks remain lightweight and do not load a model.
- [x] Remove the observability Compose file, deployment assets, smoke script, and observability-only environment example. Replace `.env.example` with speech deployment settings only; leave a user's existing `.env` untouched and unused old keys harmless.
- [x] Feature 01 returns per-chunk measurements with clip results and sends the same record shape through microphone and process-group SSE streams, with timestamp, PID, source ID, sequence, audio duration, inference duration, RTF, and status. Silent chunks skipped before inference produce no record.
- [x] The Feature 01 measurement spec is the authoritative local RTF contract. The former System Observability spec is marked retired and links to it; historical issues/evidence are retained without changing what their past verification claimed.
- [x] README, API, architecture, configuration, getting-started, deployment, and control-center documentation describe local RTF with no current database/Grafana service or credential requirement, and their active links resolve.
- [x] Compose config validation succeeds for both projects with an existing `.env` without revealing its values. Both Dockerfiles pass `docker build --check`.
- [x] Deployment documentation records the Linux Control Center's default wildcard CORS origins/methods/headers with credentials disabled and its unchanged loopback-only bind.
- [x] Deploy both standalone speech services, verify real per-chunk RTF output and wildcard CORS, and retire their old database/Grafana containers while preserving named volumes.

## Current measurement contract

See [Feature 01 per-chunk performance measurement](../spec.md#per-chunk-performance-measurement). A clip result includes a `measurements` array. Microphone SSE emits one `type: "measurement"` event per inferred chunk before completion. Process-group results include the same per-chunk record shape. Records retain UTC timestamp, PID, source ID, sequence, audio duration, inference duration, numeric RTF, and status. RTF is `inference_seconds / audio_seconds`; values at or below `1.0` met real-time pace for that chunk.

## Deployment transition

After the standalone speech service reports healthy, use the same old project name with `docker compose -p speech-feature-01 -f compose.speech-service.yml up -d --remove-orphans` on Linux or `docker compose -p speech-mac-test -f compose.mac-test.yml up -d --remove-orphans` on Mac to stop old database/Grafana containers from that project. These commands do not remove named volumes. Do not use `down -v` as part of this transition.

## Verification

Both standalone Compose configurations passed `docker compose -p <project> -f <compose-file> config -q` with the existing local `.env`; each resolves to the single `speech-service` service. The quiet check did not print environment values. `docker build --check -f Dockerfile .` and the Mac Dockerfile check both passed without warnings. Relative links in the current README, guides, and active specifications resolve, and `git diff --check` is clean. The full software suite passed 103 tests and skipped the 2 opt-in hardware proofs.

Mac deployment completed on 2026-10-09. The real CTranslate2 int8 model transcribed the legacy 21.129375-second WAV and returned three local RTF records for 10, 10, and 1.129375 seconds of audio. Physical Mac microphone capture also produced a measurement event before completion. The final image has no database driver or observability package, accepts wildcard CORS GET/preflight requests, and matches the current runtime source. Only the speech service remains running in `speech-mac-test`; both old observability volumes remain intact. Evidence: [Mac local RTF proof](../evidence/mac-local-rtf-20261009.json).

Linux deployment completed on the UBX-330M on 2026-10-09. The real `turbo/openvino-gpu/source` model transcribed the same WAV into five ordered measurements, including its final partial chunk. Wildcard CORS passed for localhost and external-origin GET requests and the model POST preflight. The deployed app source hash matches the current source. The service has no telemetry environment, database driver, or observability module; `speech-feature-01` contains only its healthy speech service. Both old observability volumes were preserved, and the unrelated legacy backend/database containers were left running. One model remains ready with no active sessions or errors. Evidence: [Linux local RTF proof](../evidence/linux-local-rtf-20261009.json). These runs verify local measurements and deployment, not independent transcript accuracy or an every-chunk real-time capacity verdict.
