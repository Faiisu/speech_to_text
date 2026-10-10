[← Back to README](../README.md)

# Deployment

## Supported platforms

| Platform | Status | Supported target |
| --- | --- | --- |
| Linux | Supported | UBX-330M running Ubuntu 24.04 on x86_64 with an Intel GPU |
| macOS | Not supported | No deployment target is currently validated |
| Windows | Not supported | No deployment target is currently validated |

The UBX-330M is the only supported deployment target. Use [`scripts/install_ubx330m.sh`](../scripts/install_ubx330m.sh) for a first deployment and [`scripts/update_ubx330m.sh`](../scripts/update_ubx330m.sh) for later updates. Each script runs the complete corresponding operation from the repository checkout.

## Deploy on UBX-330M

Clone the repository onto the UBX-330M and run the installer from the checkout root:

```bash
git clone https://github.com/Faiisu/speech_to_text.git
cd speech_to_text
./scripts/install_ubx330m.sh
```

Run it as the normal user when Docker Engine and Compose are installed, running, and accessible to that user. Use `sudo` with the same installer only when Docker host setup or an active UFW firewall change is needed. The installer uses `SUDO_UID` and `SUDO_GID` to keep generated files owned by the invoking user. It defaults to the local image tag `speech-to-text-ubx330m:v0.1.3`; an optional version argument selects another local tag.

## Update an existing deployment

From the deployed checkout on the UBX-330M, run:

```bash
./scripts/update_ubx330m.sh
```

The update script requires a clean checkout with a configured Git upstream, fast-forwards the current branch, and then runs the installer to reuse cached dependencies/model files, rebuild the application image, and update the managed service. It stops if the checkout cannot be fast-forwarded or the installer's managed Compose file is missing. The installer keeps the existing profile database and model/cache directories. An optional image version can be passed through, for example `./scripts/update_ubx330m.sh v0.1.4`.

The installer completes these stages in order: validate the host, prepare Docker, prepare export dependencies, prepare the model, build the application image, configure and start the service, and check readiness. It accepts Ubuntu 24.04 on x86_64 and checks `/dev/dri`, `/dev/snd`, the LAN route, device group IDs, and TCP port 8000 before making host changes. It detects the LAN IPv4 address and subnet, GPU render device GID, and host `audio` group GID automatically, and rejects Tailscale/CGNAT routes.

The service bind-mounts `/dev/snd` and allows read/write access to ALSA character devices, so a USB microphone connected after container startup can be discovered without recreating the container. The host must recognize the microphone as an ALSA capture device and assign its `/dev/snd` nodes to the `audio` group. Request the microphone list again after connecting a device to refresh discovery.

If Docker Engine or its Compose plugin is missing, a root run can install the required Docker packages. Before installing, the script simulates the apt transaction and stops if apt would remove an installed package. The installer does not purge conflicting packages, install a GPU driver, enable UFW, or stop unrelated containers or services.

During the same run, the installer builds local Docker images from the checkout and exports the pinned model into `./models/openvino-turbo-source`. Export metadata records the Hugging Face repository and revision. A matching complete export is reused. Hugging Face downloads, the export process's home/config/cache files, and temporary files are kept under `./data/ubx330m/cache`; an interrupted or incomplete export can reuse cached files on the next run. A failed refresh leaves the prior complete export intact. Python dependency downloads are cached by Docker's builder and reused when the cache is available. The model files stay in the checkout and are mounted read-only in the application container. Docker needs outbound access for downloads that are missing from the caches or changed dependencies; Hugging Face access is needed when required model files are not cached. Ubuntu package mirrors are also needed if Docker must be installed.

The installer creates its Compose configuration and profile data under the ignored `./data/ubx330m/` directory. The profile database persists across installer runs and container replacement. Run records, transcripts, matches, and events are kept in memory and are lost when the service restarts. The service binds port 8000 to `0.0.0.0`, so it listens on all host network interfaces. When run as root with UFW already active, the installer allows TCP port 8000 only from the detected LAN subnet; otherwise, configure the host firewall to limit access as needed.

An installer success means the service health check passed, `/healthz` responded, `turbo` was installed, `openvino-gpu` was ready with source precision, and microphone devices were discovered. If no microphone devices are found, the installer stops with an actionable error.

## Service behavior

- The service runs one backend process in Docker Compose and uses the `unless-stopped` restart policy.
- `/healthz` is a model-free health endpoint.
- The API currently allows up to 16 active microphone workflows per process.
- Run status and events are process-local and are lost on restart.

## Go-live gate

The current backend does not connect its HTTP API workflows to `HttpForwarder`. Complete that integration and verify delivery before opening the service for normal LAN use. Once connected, configure the destination and any outbound credential through the deployment environment, not in the image. Forwarding retries are bounded in memory and are not retained across restarts.

## See also

- [Getting Started](getting-started.md) for local development.
- [Architecture](architecture.md) for process-local run state and workflow ownership.
- [Transcript matching and forwarding specification](../.scratch/transcript-matching-forwarding/spec.md) for the HTTP payload and retry contract.
