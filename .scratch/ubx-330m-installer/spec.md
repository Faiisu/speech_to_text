# UBX-330M installer

Status: ready-for-agent

## Purpose

Provide a repeatable, one-command installation path for deploying a versioned Speech-to-Text image to the UBX-330M Linux host. The installer prepares the host and starts the service without asking the operator to manually configure Docker, device group IDs, the SQLite volume, or firewall rules.

Operator-facing deployment behavior remains authoritative in the [deployment plan](../../docs/deployment.md). This spec records installer acceptance only.

## Confirmed host and deployment choices

- Target: UBX-330M running Ubuntu 24.04 on x86_64.
- Runtime: Docker Compose on the host, one backend process, one service, published on port 8000.
- Image: a versioned public GHCR image that already contains the React frontend and `turbo` OpenVINO model.
- Accelerator and audio: Intel iGPU exposed through `/dev/dri`; host microphone devices through `/dev/snd`; detect and pass the host `render` and `audio` group IDs.
- Data: persistent host directory under `/opt/speech_to_text/data`, owned by container UID/GID 10001; only the SQLite profile database persists.
- Installation readiness does not signal normal user go-live. Outbound HTTP forwarding is still a separate go-live gate.
- Do not alter or stop unrelated services or existing containers. Do not auto-remove Docker packages, enable UFW, or rewrite firewall policy.

## Acceptance criteria

- [x] A repeatable Bash installer is placed under `scripts/` and linked from the root README.
- [x] The installer validates Ubuntu 24.04, x86_64, root privileges, `/dev/dri`, and `/dev/snd` before making host changes.
- [x] It reuses a working Docker Engine and Compose plugin. If Docker is absent, it installs a supported Ubuntu package without removing conflicting packages; if a safe install is not possible, it stops with a clear message and leaves the host unchanged.
- [x] It detects the LAN IPv4 address/subnet plus the numeric GIDs for the GPU render and audio devices without prompting for values.
- [x] It creates an installation directory and `/data` bind mount with owner 10001:10001, without changing ownership recursively outside its own data directory.
- [x] It checks port 8000 before starting, and aborts without stopping or changing any existing service if the port is occupied.
- [x] It pulls and runs a version-pinned public GHCR image with Compose, one backend process, `/dev/dri`, `/dev/snd`, detected device groups, persistent profile SQLite storage, restart policy, and the existing model-free healthcheck.
- [x] It binds access to the detected LAN interface. If UFW is already active, it adds only a rule for the detected LAN subnet to TCP port 8000; it does not enable UFW or change other firewall rules.
- [x] It waits for the container healthcheck, verifies the local HTTP endpoint and model/microphone readiness, then prints the LAN URL and states that forwarding is still required for normal go-live.
- [x] Installer behavior and update guidance are documented in `docs/deployment.md`; no duplicate deployment source of truth is added.
- [x] Static validation includes `bash -n` and ShellCheck when available. Do not execute the installer end-to-end on the development workstation.

## Non-goals

- Do not implement outbound forwarding, backups, authentication, TLS, a reverse proxy, or persistent run history.
- Do not change existing host services or install a GPU driver.
- Do not run the installer against the developer workstation or UBX-330M as part of code verification.
