# UBX-330M installer

Status: ready-for-agent

## Purpose

Provide a repeatable, one-command installation path for deploying Speech-to-Text from a source checkout to the UBX-330M Linux host. The installer builds its local images, prepares the model and SQLite storage, and starts the service. `sudo` is optional when Docker and Compose are already accessible; privileged host setup such as Docker package installation and UFW changes requires root.

Operator-facing deployment behavior remains authoritative in the [deployment plan](../../docs/deployment.md). This spec records installer acceptance only.

## Confirmed host and deployment choices

- Target: UBX-330M running Ubuntu 24.04 on x86_64.
- Runtime: Docker Compose on the host, one backend process, one service, published on port 8000.
- Images: deterministic local `linux/amd64` tags built from the checked-out repository. A dependency image built from the locked `python-deps` Dockerfile stage exports the pinned `turbo` OpenVINO model into the checkout's `./models` before startup; the application image is then built locally without model weights and Compose uses that local tag. The model is mounted read-only at `/opt/models`.
- Network: Docker needs outbound access for missing base images and build packages; the exporter container needs Hugging Face access if no complete local export exists. If Docker must be installed, Ubuntu package mirrors are needed. The installer does not install application/model Python dependencies on the host.
- Accelerator and audio: Intel iGPU exposed through `/dev/dri`; host microphone devices through `/dev/snd`; detect and pass the host `render` and `audio` group IDs.
- Data: installer-owned Compose configuration and profile database live under the ignored checkout path `./data/ubx330m`. The checkout user's group is added to the container so UID 10001 can write the profile database without privileged host ownership changes.
- Installation readiness does not signal normal user go-live. Outbound HTTP forwarding is still a separate go-live gate.
- Do not alter or stop unrelated services or existing containers. Do not auto-remove Docker packages, enable UFW, or rewrite firewall policy.

## Acceptance criteria

- [x] A repeatable Bash installer is placed under `scripts/` and linked from the root README.
- [x] The installer validates Ubuntu 24.04, x86_64, `/dev/dri`, `/dev/snd`, LAN access, and port availability before host changes. `sudo` is optional when Docker and Compose are installed, running, and accessible to the current user.
- [x] A non-root run continues with accessible Docker and skips privileged host setup, including UFW changes with a clear firewall reminder. A root run can install a supported Ubuntu Docker package without removing conflicting packages or start the existing Engine; if a safe install is not possible, it stops with a clear message.
- [x] It detects the LAN IPv4 address/subnet plus the numeric GIDs for the GPU render and audio devices without prompting for values.
- [x] It creates its Compose/data paths under ignored checkout `data/ubx330m`; the invoking checkout owner owns those paths, and the app's UID 10001 receives that GID for profile database writes.
- [x] It checks port 8000 before starting, and aborts without stopping or changing any existing service if the port is occupied.
- [x] Its seven ordered stages are: validate host; prepare Docker; prepare/load export dependencies; prepare/reuse the pinned model; build the application image from the current checkout; configure/start the service; check readiness.
- [x] The model export is written into checkout `./models` with the invoking user's ownership, including when the installer is run through `sudo`.
- [x] It runs export through the dependency image without installing application/model Python dependencies on the host, and preserves an existing complete model if export fails.
- [x] Export reuse requires provenance metadata matching the pinned Hugging Face repository and revision; exports without matching metadata are staged and regenerated.
- [x] It builds and runs a deterministic version-tagged local application image with Compose, one backend process, `/dev/dri`, `/dev/snd`, detected device groups, persistent profile SQLite storage, restart policy, and the existing model-free healthcheck; it does not pull an app image from GHCR.
- [x] It binds port 8000 to all host interfaces. A root run adds only a rule for the detected LAN subnet to TCP port 8000 when UFW is active; a non-root run skips UFW and explains that the operator must allow LAN traffic. It never enables UFW or changes other firewall rules.
- [x] An update script fast-forwards the deployed checkout from its configured Git upstream, then reuses the installer path to rebuild/reuse dependencies and model, rebuild the application image, and replace the managed service. It refuses dirty checkouts and refuses to run without this installer's managed deployment files.
- [x] It waits for the container healthcheck, verifies the local HTTP endpoint and model/microphone readiness, then prints the LAN URL and states that forwarding is still required for normal go-live.
- [x] Install and update behavior are documented in `docs/deployment.md`; no duplicate deployment source of truth is added.
- [x] Static validation includes `bash -n` and ShellCheck when available. Do not execute the installer end-to-end on the development workstation.

## Non-goals

- Do not implement outbound forwarding, backups, authentication, TLS, a reverse proxy, or persistent run history.
- Do not change existing host services or install a GPU driver.
- Do not run the installer against the developer workstation or UBX-330M as part of code verification.
