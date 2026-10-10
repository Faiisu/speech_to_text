# 09. Publish the Linux Docker service on host interfaces

Status: wontfix
Execution: completed
Blocked by: none

Legacy outcome: the earlier Control Center container and ports were retired. The current versioned image uses port 8000 as documented in [`docs/deployment.md`](../../../docs/deployment.md).

## Scope

Use bridge networking for the Linux `speech-feature-01` service and publish its Control Center as `0.0.0.0:18765` to container port `8765` by default. The Mac Docker Desktop test profile also publishes its Control Center on `0.0.0.0:18766`; its host microphone bridge remains loopback-only. Preserve the Linux model/cache mounts, UID/GID, Intel GPU and audio devices, PipeWire runtime mount, and Compose project name.

## Acceptance checklist

- [x] Linux Compose uses bridge networking and explicitly maps `0.0.0.0:${SPEECH_TO_TEXT_HOST_PORT:-18765}:${CONTROL_CENTER_PORT:-8765}`.
- [x] The service listens on the container bridge interface; its health check uses the internal container port.
- [x] Host PID namespace sharing is removed because no active OS sampler requires it; process measurements retain the service process PID.
- [x] Linux host access is `http://127.0.0.1:18765/` locally or through its host IPv4 address on the network. The Mac profile uses port `18766`, and its microphone bridge remains bound to `127.0.0.1:18767`.
- [x] Environment, deployment, API, getting-started, README, Feature 01, and Control Center documentation reflect the current host-interface publication and point to the deployment source of truth.
- [x] Compose resolves Linux to `0.0.0.0:18765 -> 8765/tcp` and Mac to `0.0.0.0:18766 -> 8765/tcp`, with bridge networking and no host PID namespace.
- [ ] Deployment health and host access are verified on Linux.

## Verification

Compose configuration validation passed for both projects. Deployment health and remote reachability depend on applying the current Compose configuration and on host firewall/network routing rules.
