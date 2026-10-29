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

## Switch the UBX-330M service between OpenVINO and Gemma

The deploy scripts keep the two application flavors mutually exclusive on host port 8000. They allow redeploying the currently active flavor, but refuse to start one flavor while the other is running. Stop the active flavor explicitly before switching; neither deploy script stops it automatically. Run `deploy_openvino.sh` as the normal Docker-enabled host user. `deploy_gemma4.sh` also supports root: it keeps the container on a non-root UID/GID, taking them from `SUDO_UID`/`SUDO_GID` or the checkout directory owner, and assigns the top-level model/data directories to that identity. Avoid setting `APP_UID=0`.

Deploy or update the standard OpenVINO service with:

```bash
./scripts/deploy_openvino.sh
```

This reuses `scripts/install_ubx330m.sh` and its managed Compose project. Deploy Gemma on a fresh UBX checkout, or switch from OpenVINO by stopping the active service first:

```bash
./scripts/stop_service.sh
./scripts/deploy_gemma4.sh
```

The UBX Gemma deploy script requires the external `shared-network` used by OpenVINO and an available host `audio` group plus `/dev/snd`, because it enables microphone access. The service joins that network with the same destination-network allowance as OpenVINO. The [`compose.gemma4.ubx330m.yaml`](../compose.gemma4.ubx330m.yaml) override mounts `models/` at `/opt/models` and the existing `data/ubx330m/` directory at `/data`, preserving profiles, recordings, and the Hugging Face cache across flavor switches. `deploy_gemma4.sh` sets the container UID/GID and audio GID from the host. Audio-file uploads themselves do not require microphone devices; use the base Gemma Compose instructions above when deploying without `/dev/snd`.

To switch back, stop both managed flavor projects and rerun the OpenVINO installer:

```bash
./scripts/stop_service.sh
./scripts/deploy_openvino.sh
```

The stop script targets the installer Compose project `speech_to_text_ubx330m` and the Gemma Compose project `speech_to_text_gemma4`. It also recognizes the legacy Gemma container named `speech-to-text-gemma4` under Compose project `speech_to_text`, which can be left by a checkout launched without an explicit project name. For that legacy case, it stops and removes only the matching container so its fixed name is available for the managed Gemma project. `docker compose down` runs without `--volumes`, and `docker rm` runs without `-v`; bind-mounted model and application data remain in place. The script does not stop unrelated containers. Run it as the normal Docker-enabled user, or use `sudo ./scripts/stop_service.sh` when the system Docker daemon requires root access. With `sudo`, it keeps the invoking user's UID/GID for Compose configuration. Rootless Docker setups should run it as the Docker-owning user so it reaches the same Docker context. The script reports when no managed containers were found. See the [deployment-switching contract](../.scratch/deployment-switching/spec.md) for the lifecycle rules.

The installer completes these stages in order: validate the host, prepare Docker, prepare export dependencies, prepare the model, build the application image, configure and start the service, and check readiness. It accepts Ubuntu 24.04 on x86_64 and checks `/dev/dri`, `/dev/snd`, the LAN route, device group IDs, and TCP port 8000 before making host changes. It detects the LAN IPv4 address and subnet, GPU render device GID, and host `audio` group GID automatically, and rejects Tailscale/CGNAT routes.

Before the installer builds images, create the external Docker network `shared-network` if it does not already exist: `docker network create shared-network`. The managed `speech-to-text` service joins this network through the Compose network key `shared`; the installer checks that the named network exists and stops with this command if it is missing. The network remains operator-managed, and the standalone MQTT Compose service is not attached to it automatically.

The generated Compose file sets `SPEECH_TO_TEXT_DESTINATION_ALLOWED_NETWORKS` to `"0.0.0.0/0,::/0"`, allowing configured destinations on any IPv4 or IPv6 network. Link-local and known metadata addresses remain blocked by the destination egress policy, including IPv4-mapped IPv6 equivalents.

The service bind-mounts `/dev/snd` and allows read/write access to ALSA character devices, so a USB microphone connected after container startup can be discovered without recreating the container. The host must recognize the microphone as an ALSA capture device and assign its `/dev/snd` nodes to the `audio` group. Request the microphone list again after connecting a device to refresh discovery.

If Docker Engine or its Compose plugin is missing, a root run can install the required Docker packages. Before installing, the script simulates the apt transaction and stops if apt would remove an installed package. The installer does not purge conflicting packages, install a GPU driver, enable UFW, or stop unrelated containers or services.

During the same run, the installer builds local Docker images from the checkout and exports the pinned model into `./models/openvino-turbo-source`. Export metadata records the Hugging Face repository and revision. A matching complete export is reused. Hugging Face downloads, the export process's home/config/cache files, and temporary files are kept under `./data/ubx330m/cache`; an interrupted or incomplete export can reuse cached files on the next run. A failed refresh leaves the prior complete export intact. Python dependency downloads are cached by Docker's builder and reused when the cache is available. The model files stay in the checkout and are mounted writable in the application container for frontend model downloads/conversion. The installer grants the checkout group write access to model assets and the container joins that group; the default Hugging Face cache is `/data/huggingface`. See the [model-management contract](../.scratch/model-management/spec.md) for storage and preparation behavior. Docker needs outbound access for downloads that are missing from the caches or changed dependencies; Hugging Face access is needed when required model files are not cached. Ubuntu package mirrors are also needed if Docker must be installed.

The installer creates its Compose configuration and profile data under the ignored `./data/ubx330m/` directory. The profile database and [model tuning recordings](../.scratch/model-tuning/spec.md) persist across installer runs and container replacement through the existing `/data` mount. Run records, transcripts, matches, and events are kept in memory and are lost when the service restarts. The service binds port 8000 to `0.0.0.0`, so it listens on all host network interfaces. When run as root with UFW already active, the installer allows TCP port 8000 only from the detected LAN subnet; otherwise, configure the host firewall to limit access as needed.

An installer success means the service health check passed, `/healthz` responded, `turbo` was installed, `openvino-gpu` was ready with source precision, and microphone devices were discovered. If no microphone devices are found, the installer stops with an actionable error.

### Optional Gemma 4 E4B Docker flavor

The standard production image and UBX installer retain the existing OpenVINO Whisper defaults. The optional Gemma flavor uses a separate Compose file and contains Transformers/PyTorch plus CTranslate2, but omits Optimum Intel and the OpenVINO runtime because their dependency set conflicts with the current Transformers requirement. Gemma itself runs only through its in-process Transformers adapter; CTranslate2 remains available for compatible Whisper models. The flavor uses the same `/models` page, Profile selection, and Model tuning flow.

From a Linux host with Docker Compose installed, start the Gemma flavor with audio-file uploads and persistent model/application storage:

```bash
mkdir -p models data/gemma4
export APP_UID="$(id -u)"
export APP_GID="$(id -g)"
docker compose -f compose.gemma4.yaml up --build -d
```

The host directories must be writable by `APP_UID:APP_GID`. `/opt/models` stores prepared model files, while `/data` retains the profile database, model-tuning recordings, and Hugging Face cache. Open **Models** at `http://127.0.0.1:8000/models`, inspect/download `google/gemma-4-E4B-it`, and choose the Transformers runtime to verify it. A request-scoped Hugging Face token must have access to the repository. The backend stores the source under `/opt/models/sources/gemma-4-e4b` and loads it in-process when the first transcription starts; weights are not copied into the image.

To enable host microphone capture on Linux, install `getent` if needed and apply the optional device override. Audio-file uploads work without this override:

```bash
export AUDIO_GID="$(getent group audio | cut -d: -f3)"
docker compose -f compose.gemma4.yaml -f compose.gemma4.microphone.yaml up --build -d
```

Stop the service with `docker compose -f compose.gemma4.yaml down`. Google's [audio guide](https://ai.google.dev/gemma/docs/capabilities/audio?hl=en) shows the E4B checkpoint at about 10.2 GB, so leave additional disk for download and staging copies.

Including PyTorch, Transformers, and audio/vision dependencies makes this image substantially larger and multi-gigabyte; exact size depends on platform wheels and Docker layers. The target host also needs sufficient system RAM or accelerator memory. [Google's overview](https://ai.google.dev/gemma/docs/core.md) estimates 17.9 GB GPU/TPU memory for Gemma 4 E4B BF16 including overhead, and the separate [vLLM recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) lists a 24 GB+ NVIDIA GPU as its BF16 minimum. Those values are references for their documented stacks, not guarantees for this app's Transformers runtime or the UBX Intel iGPU. Gemma inference, memory use, speed, and recognition quality have not been verified on the UBX-330M.

This Compose flavor does not configure GPU passthrough or an accelerator-specific PyTorch build; it uses the devices available to PyTorch inside the container and therefore normally runs inference on CPU. Treat accelerator use and target-hardware performance as unverified until checked on the deployment host.

## Service behavior

- The service runs one backend process in Docker Compose and uses the `unless-stopped` restart policy.
- `/healthz` is a model-free health endpoint.
- The API currently allows up to 16 active microphone workflows per process.
- Run status and events are process-local and are lost on restart.

Accepted microphone runs recover after unexpected workflow endings while the backend remains running; explicit stop cancels recovery. See the [microphone recovery contract](../.scratch/backend-transcription-api/spec.md#microphone-recovery) for timing and limits. This does not restore runs after a backend/container restart. Verify physical microphone disconnect/reconnect and continuous OpenVINO capture on the UBX-330M before treating hardware recovery as proven.

IPCAM RTSP runs share that lifecycle and active-run capacity limit. Configure camera reachability and optional credential references through [RTSP configuration](configuration.md#ipcam-audio-through-rtsp). The current UBX installer still requires host microphone discovery during readiness checks; an installation with only network cameras requires adapting that deployment check. Camera connectivity, decoder support, reconnection, and continuous inference must be checked with the actual camera on the target deployment.

Camera usernames/passwords can be entered directly in Profile or Model tuning. The backend/test extras include the credential encryption dependency, so rebuild the application image when updating to this feature. Saved Profile passwords use an automatically generated `/data/profiles.sqlite3.key` under the existing persistent mount; preserve it with the profile database when backing up or moving a container. See [credential setup and key handling](configuration.md#ipcam-audio-through-rtsp).

## MQTT broker

The standalone [`compose.mqtt.yaml`](../compose.mqtt.yaml) runs Eclipse Mosquitto on either Linux or macOS with Docker Compose 2.23.1 or newer. From the checkout root:

```bash
docker compose -f compose.mqtt.yaml up -d --wait
docker compose -f compose.mqtt.yaml ps
docker compose -f compose.mqtt.yaml logs --tail 50 mqtt
```

The default endpoint is `mqtt://127.0.0.1:1883`. The broker has a QoS 1 publish health check, `restart: always`, persistent broker state in a named volume, and bounded container logs. A process exit restarts the broker; Docker must itself be running and configured to start at boot. On UBX-330M with Docker Engine, enable its boot service with `sudo systemctl enable --now docker`. On macOS, start the installed Docker runtime and configure its login startup. A health check reports availability; Docker's restart policy restarts exited containers, not a process that remains running but unhealthy. An explicit `docker compose stop` is an operator stop.

The bundled configuration permits anonymous clients and binds only to host loopback. Keep this local default for development. A LAN or Internet broker needs a deliberately configured listener, TLS and authentication before exposing it; use `mqtts://` and deployment-approved references in destination settings. `MQTT_PORT` changes the published host port; `MQTT_BIND_ADDRESS` changes the bind address. For containerized application deployments, arrange a reachable broker address/network as described in [MQTT configuration](configuration.md#mqtt-destination-setup).

`docker compose -f compose.mqtt.yaml down` removes the service while preserving its volume. Adding `--volumes` deletes broker state. The full test script uses a separate project and volume, so its cleanup does not remove this broker's data.

## Go-live gate

The backend connects transcription workflows to the selected saved destination. Before normal LAN use, configure a destination through the API, select it in a profile, and verify both synthetic delivery and a terminal workflow result against the actual receiver. Approve required LAN CIDRs and credential/TLS resource references through the deployment environment, not in the image; see [output configuration](configuration.md#transcript-matching-and-forwarding). Forwarding retries remain bounded in memory and are not retained across restarts. Destination API access rules are defined in the [destination endpoints specification](../.scratch/backend-transcription-api/spec.md#destination-endpoints).

## See also

- [Getting Started](getting-started.md) for local development.
- [Architecture](architecture.md) for process-local run state and workflow ownership.
- [Transcript matching and forwarding specification](../.scratch/transcript-matching-forwarding/spec.md) for the JSON payload and transport retry contract.
