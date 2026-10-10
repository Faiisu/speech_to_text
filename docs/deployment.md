[← Back to README](../README.md)

# Deployment

## Supported platforms

| Platform | Status | Deployment target |
| --- | --- | --- |
| Linux | Supported on the validated target below | UBX-330M running Ubuntu 24.04 on x86_64 with an Intel GPU |
| macOS | No supported deployment path yet | Not currently supported |
| Windows | No supported deployment path yet | Not currently supported |

The UBX-330M installer is currently the only supported host deployment. It requires the Intel GPU and audio devices provided by that host. Building the Docker image from macOS or another non-`amd64` machine does not make that machine a supported deployment target.

## Goal

Deploy the current Speech-to-Text application on the UBX-330M Linux host as a simple LAN service. The supported installer path starts from a source checkout without model weights, prepares the model, builds the local Docker images, and starts the Compose service. This document is the operator guide for that path and for the separately recorded GHCR trial.

## Shape

- Build two local `linux/amd64` images from the source checkout: a dependency image used only to export the model, and an application image containing the Python backend, built React frontend, and Intel OpenCL runtime. The application image does not contain model weights. The pinned `turbo` OpenVINO source-precision export is stored under checkout `./models` and mounted read-only at `/opt/models`. Runtime uses OpenVINO GPU and the host's Intel kernel driver through `/dev/dri`. FastAPI serves the frontend assets and `/api/v1` from the same origin, with `/healthz` as a model-free health endpoint.
- Run one backend process in one Docker Compose service. Publish port `8000` to the LAN and open `http://<UBX-330M-IP>:8000` in a browser.
- Use HTTP without login for LAN users. Restrict access to the LAN through the host firewall.
- Pass the Intel GPU device (`/dev/dri`) and the host microphone/audio devices into the container. Use the existing OpenVINO GPU model setup.
- Keep the SQLite profile database under `./data` and model artifacts under `./models`, both on host-mounted paths. The model mount is read-only inside the app container; its files remain available when the app image is rolled back or replaced.

## Image release and update

GitHub Actions builds and tags a `linux/amd64` image when a `v*` Git tag is pushed, then pushes it to `ghcr.io/faiisu/speech_to_text` with a semantic-version tag and `latest`. The existing GHCR trial below is a separate, previously published-image deployment. The installer does not pull an application image from GHCR: it builds both images from the checked-out source. During those builds Docker may download base images and install OS and application packages. Separately, the model-export step downloads the pinned checkpoint from Hugging Face when `./models` does not already contain a complete export for the pinned repository and revision. Thus a source-only checkout can be deployed, but the first install needs outbound access to the relevant registries, package sources, and Hugging Face. Conversion can use substantial memory and disk space. Deployments may briefly interrupt active workflows; run status and events are process-local and are lost when the service restarts.

The existing GHCR trial is pinned to image tag `v0.1.3`, which predates the image change in this checkout and still contains the bundled model. A future GHCR image built from the current source will omit model weights. The installer defaults to the local tag `speech-to-text-ubx330m:v0.1.3`; this tag labels the image built from the current checkout and does not make the installer pull `v0.1.3` from GHCR. Pass a version argument or set `SPEECH_TO_TEXT_VERSION` to choose another local image tag.

The separately recorded GHCR trial uses `ghcr.io/faiisu/speech_to_text:v0.1.3` on host port `18000`. Its Compose checkout is `~/apps/speech_to_text-trial`; the `.env` file pins the image and sets the host device group IDs. Its tested entry point is `http://<UBX-330M-IP>:18000/`. This is not the source-checkout installer path described below. The trial was ready for technical evaluation, but normal user go-live remains gated on connecting and verifying outbound forwarding.

## Host data and configuration

Compose mounts `./data` at `/data` and `./models` at `/opt/models` read-only, and sets `SPEECH_TO_TEXT_PROFILE_DB=/data/profiles.sqlite3`. The default `openvino-turbo-source` model must exist under the host `./models` directory before the app starts. The container runs as UID/GID `10001`; give that user ownership of the profile bind-mount directory before the first start. Model files need to be readable by UID `10001`:

```bash
mkdir -p data
sudo chown -R 10001:10001 data
```

Generate the pinned export into the checkout's `models/` directory before starting Compose. The exporter reuses an export only when its required OpenVINO weights and provenance manifest match the pinned Hugging Face repository and revision. It writes a staged export and replaces the destination only after all required files are present; exports without matching metadata are regenerated, while the prior directory remains intact if that export fails. For a manual source-checkout deployment, build and run the same dependency image used by the installer:

```bash
mkdir -p models
EXPORT_DEPS_IMAGE=speech-to-text-ubx330m-export-deps:v0.1.3
docker build --platform linux/amd64 --target python-deps \
  --tag "$EXPORT_DEPS_IMAGE" --file Dockerfile .
docker run --rm --platform linux/amd64 --user "$(id -u):$(id -g)" \
  --volume "$PWD/scripts/export_openvino_model.py:/tmp/export_openvino_model.py:ro" \
  --volume "$PWD/models:/models" \
  --env HF_HUB_DISABLE_TELEMETRY=1 \
  --entrypoint /build/.venv/bin/python "$EXPORT_DEPS_IMAGE" \
  /tmp/export_openvino_model.py --destination /models/openvino-turbo-source
```

The dependency image's tag is only a local label; the command builds it from this checkout. Missing or partial exports fail before Compose starts; an existing complete export is retained if a refresh fails. Keep the `models/` directory with the deployment checkout during upgrades and rollback.

The application creates the SQLite file on first use. Keep only this database in that directory. Profiles are shared by all LAN users and survive container replacement. The project does not create an additional backup. Run records, transcripts, matches, and events remain in memory and are lost on restart.

Set the service defaults to `SPEECH_TO_TEXT_MODEL=turbo`, `SPEECH_TO_TEXT_RUNTIME=openvino-gpu`, and `SPEECH_TO_TEXT_PRECISION=source`. Pass only the host device permissions needed for Intel GPU and audio access.

Compose publishes `${HOST_PORT:-8000}:8000`, so the trial uses host port `18000` and leaves the default port `8000` available. Set `RENDER_GID` and `AUDIO_GID` to the numeric host groups that own `/dev/dri/renderD*` and `/dev/snd` when the defaults differ. On the UBX-330M trial they are `992` and `29`. One backend worker is started; the container's `/healthz` check does not load the model.

## Build, trial, rollback

For a local image build on an `amd64` host, run `docker compose build`. On Apple Silicon or another non-`amd64` host, set the target platform because the runtime image requires the Intel OpenCL package:

```bash
DOCKER_DEFAULT_PLATFORM=linux/amd64 docker compose build
```

This builds the frontend and assembles the runtime image without model weights. The existing GHCR `v0.1.3` trial image still contains its legacy bundled model; the UBX-330M installer instead builds from the source checkout and exports weights into that checkout's `./models` directory. A later GHCR image built from this checkout will omit weights. To update the separate GHCR trial to a published version, run these commands on UBX-330M:

```bash
cd ~/apps/speech_to_text-trial

# Keep the trial's existing settings; change only the image version when updating.
cat > .env <<'EOF'
SPEECH_TO_TEXT_IMAGE=ghcr.io/faiisu/speech_to_text:v0.1.3
HOST_PORT=18000
RENDER_GID=992
AUDIO_GID=29
EOF
chmod 600 .env

sudo docker compose pull
sudo docker compose up -d --no-build
```

For a later release, replace `v0.1.3` with the desired published tag, then repeat the pull and recreate commands. To roll back, set the previous working image tag in `.env` and repeat them. Compose replaces only this service; the profile SQLite file under `data/` remains mounted. The single backend process has a short interruption during replacement, and active runs/history are lost on restart.

Verify the deployment on UBX-330M:

```bash
curl -fsS http://127.0.0.1:18000/healthz
curl -fsS http://127.0.0.1:18000/api/v1/models
curl -fsS http://127.0.0.1:18000/api/v1/microphones
```

The health response should be `{"status":"ok"}`; the models response should show `turbo` installed and `openvino-gpu` ready with `source` precision available. The microphone response should list host input devices. Open `http://<UBX-330M-IP>:18000/` from a LAN client. GPU readiness requires an Intel GPU host; CPU-only local systems can verify model artifact discovery but cannot pass the GPU-ready check.

To trial on a different Compose checkout, set `SPEECH_TO_TEXT_IMAGE` to a version tag and `HOST_PORT=18000` for both `docker compose pull` and `docker compose up -d --no-build`. Stop only that trial service with `HOST_PORT=18000 docker compose down`; this does not remove its `data/` directory. The previously running service on port 8000 remains in place.

## UBX-330M installer

Clone the GitHub repository onto the UBX-330M; model weights are not stored in Git. For example:

```bash
git clone https://github.com/Faiisu/speech_to_text.git
cd speech_to_text
```

From the checkout root, run the installer as the normal user when Docker Engine and Compose are already installed, running, and accessible to that user:

```bash
./scripts/install_ubx330m.sh
```

This is the only supported deployment installer at present. Use it on the Linux target listed in [Supported platforms](#supported-platforms).

Use `sudo` when Docker or Compose needs installation, Docker needs to be started with host privileges, or an active UFW firewall needs the installer's LAN allow rule. The installer uses `SUDO_UID` and `SUDO_GID` to keep the checkout's generated model and installer files owned by the invoking user. It defaults to the local image tag `speech-to-text-ubx330m:v0.1.3`; pass another version tag to select a different local image:

```bash
sudo ./scripts/install_ubx330m.sh v0.1.3
```

The installer runs seven stages in this order: validate host; prepare Docker; prepare/load export dependencies; prepare model; build application image; configure/start service; check readiness. It accepts only Ubuntu 24.04 on x86_64 and checks `/dev/dri`, `/dev/snd`, the LAN route, device group IDs, and TCP port 8000 before host changes. It detects the LAN IPv4 address and subnet, render GID, and audio GID automatically, and rejects Tailscale/CGNAT routes. `sudo` is optional when Docker Engine and Compose are already running and accessible to the invoking user. Without that access, a non-root run stops with host setup guidance. A root run reuses a working Docker Engine and Compose plugin; if either is missing, it refreshes Ubuntu package metadata, simulates the package installation, and stops if apt would remove any installed package. It does not purge conflicting packages, install Python application/model dependencies on the host, install a GPU driver, enable UFW, or stop unrelated containers or services.

The dependency and application images use deterministic local tags based on the selected version. Compose references the locally built application image and starts with `--no-build`; the installer does not pull an app image from GHCR. The exporter runs inside the local dependency image and writes the pinned export into the checkout's `./models/openvino-turbo-source`. It records the Hugging Face repository and revision in a provenance manifest, reuses only a matching export, and keeps the prior model directory intact if refresh fails. Compose mounts checkout `./models` read-only at `/opt/models`. The application image is built from the current checkout without model weights. Installer Compose configuration and profile data live under the ignored `./data/ubx330m/` directory; container UID `10001` receives the checkout owner's group so it can write the profile database without changing ownership outside the checkout. Docker needs outbound access for missing base images and build dependencies, and the exporter container needs Hugging Face access when no matching export exists. If Docker is missing, Ubuntu package mirrors are also needed to install Docker safely.

The installer binds port 8000 to the detected LAN address. When run as root and UFW is already active, it adds an allow rule for TCP port 8000 from only the detected LAN subnet. A non-root run skips firewall changes and prints a reminder to allow that LAN traffic; clients may be unable to connect until the host firewall permits it. Re-running the installer rebuilds the selected local image tag from the current checkout and updates only the installer-owned Compose service; the profile database and complete model export remain in place. To roll back to an earlier application build, run the installer from the corresponding earlier repository checkout with its previous version tag. The installer waits for the image healthcheck, checks `/healthz`, confirms that `turbo` is installed and `openvino-gpu` is ready with source precision available, and lists discovered microphone devices. It stops with an actionable error if no microphone devices are found.

An installer success confirms service readiness only. Outbound HTTP forwarding still must be connected and its delivery verified before normal user go-live, as described in [Go-live gate](#go-live-gate).

## Service operation

- Use a lightweight health endpoint that does not load the model for the container healthcheck.
- Restart the service with Docker Compose's `unless-stopped` policy and inspect output with Docker logs on the host.
- Keep a single backend process. Do not add a lower deployment-specific microphone limit; operators can use the existing capacity workflow to measure the host. The backend's current API still has its own limit of 16 active microphone workflows per process.
- Do not add a persistent queue for outbound delivery. The existing forwarding contract retries transient failures up to three attempts and reports failure after retries are exhausted.

## Go-live gate

The current backend does not connect its HTTP API workflows to `HttpForwarder`. Complete that integration and verify delivery to the destination before opening the service for normal LAN use. Once connected, configure the destination and any outbound credential through the deployment environment, not in the image. Forwarding failure behavior remains bounded in-memory retry with no delivery queue across restarts.

## See also

- [Getting Started](getting-started.md) for the current local development setup.
- [Architecture](architecture.md) for process-local run state and workflow ownership.
- [Transcript matching and forwarding specification](../.scratch/transcript-matching-forwarding/spec.md) for the HTTP payload and retry contract.
