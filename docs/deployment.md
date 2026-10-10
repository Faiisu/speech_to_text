[← Back to README](../README.md)

# Deployment plan: UBX-330M

## Goal

Deploy the current Speech-to-Text application on the UBX-330M Linux host as a simple LAN service. This document is the operator guide for the Docker image and Compose deployment.

## Shape

- Build one `linux/amd64` Docker image on Ubuntu 24.04 containing the Python backend, the built React frontend, the converted `turbo` OpenVINO source-precision model, and the Intel OpenCL runtime. The model is exported in a CPU-only build stage from a pinned Hugging Face revision; runtime uses OpenVINO GPU and the host's Intel kernel driver through `/dev/dri`. FastAPI serves the frontend assets and `/api/v1` from the same origin, with `/healthz` as a model-free health endpoint.
- Run one backend process in one Docker Compose service. Publish port `8000` to the LAN and open `http://<UBX-330M-IP>:8000` in a browser.
- Use HTTP without login for LAN users. Restrict access to the LAN through the host firewall.
- Pass the Intel GPU device (`/dev/dri`) and the host microphone/audio devices into the container. Use the existing OpenVINO GPU model setup.
- Keep only the SQLite profile database on a host-mounted path. The default model ships in each versioned image, so rollback also restores the matching model.

## Image release and update

GitHub Actions builds and tags a `linux/amd64` image for each `v*` release tag, then pushes it to the public package `ghcr.io/faiisu/speech_to_text` using the same `vX.Y.Z` tag plus `latest`. UBX-330M can pull the image without registry credentials. The model-export stage runs without GPU access; its first build downloads the pinned checkpoint and converts it to OpenVINO IR, which can use substantial memory and disk space. A host operator selects the image version with `SPEECH_TO_TEXT_IMAGE` in the Compose environment, pulls it, and recreates the service. Keep the previous image tag available for rollback. Deployments may briefly interrupt active workflows; run status and events are process-local and are lost when the service restarts.

The current UBX-330M trial uses `ghcr.io/faiisu/speech_to_text:v0.1.3` on host port `18000`. Its Compose checkout is `~/apps/speech_to_text-trial`; the `.env` file pins the image and sets the host device group IDs. The tested entry point is `http://<UBX-330M-IP>:18000/`. This deployment is ready for technical evaluation, but user go-live remains gated on connecting and verifying outbound forwarding.

## Host data and configuration

The image contains `openvino-turbo-source` under `/opt/models`; do not mount a models directory over it. Compose mounts `./data` at `/data` and sets `SPEECH_TO_TEXT_PROFILE_DB=/data/profiles.sqlite3`. The container runs as UID/GID `10001`; give that user ownership of the host bind-mount directory before the first start:

```bash
mkdir -p data
sudo chown -R 10001:10001 data
```

The application creates the SQLite file on first use. Keep only this database in that directory. Profiles are shared by all LAN users and survive container replacement. The project does not create an additional backup. Run records, transcripts, matches, and events remain in memory and are lost on restart.

Set the service defaults to `SPEECH_TO_TEXT_MODEL=turbo`, `SPEECH_TO_TEXT_RUNTIME=openvino-gpu`, and `SPEECH_TO_TEXT_PRECISION=source`. Pass only the host device permissions needed for Intel GPU and audio access.

Compose publishes `${HOST_PORT:-8000}:8000`, so the trial uses host port `18000` and leaves the default port `8000` available. Set `RENDER_GID` and `AUDIO_GID` to the numeric host groups that own `/dev/dri/renderD*` and `/dev/snd` when the defaults differ. On the UBX-330M trial they are `992` and `29`. One backend worker is started; the container's `/healthz` check does not load the model.

## Build, trial, rollback

For a local image build, run `docker compose build`. This builds the frontend, exports the pinned turbo model in the build stage, and assembles the runtime image. The UBX-330M trial is deployed from a published image; no model download or image build is needed on that host. To update that trial to a released version, run these commands on UBX-330M:

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

First clone or copy a repository checkout onto the UBX-330M. From the checkout root on that host, run the installer as root:

```bash
sudo ./scripts/install_ubx330m.sh
```

It defaults to the pinned public image `ghcr.io/faiisu/speech_to_text:v0.1.2`. To install the currently trialed release, pass `v0.1.3` explicitly; pass another published release tag to select a different version:

```bash
sudo ./scripts/install_ubx330m.sh v0.1.3
```

The installer accepts only Ubuntu 24.04 on x86_64 and checks root access, `/dev/dri`, `/dev/snd`, the LAN route, device group IDs, and TCP port 8000 before it changes the host. It detects the LAN IPv4 address and subnet, render GID, and audio GID automatically, and rejects Tailscale/CGNAT routes. It reuses a working Docker Engine and Compose plugin. If either is missing, it refreshes Ubuntu package metadata, simulates the package installation, and stops if apt would remove any installed package; it does not purge conflicting packages. It never installs a GPU driver, enables UFW, or stops unrelated containers or services.

The installer pulls the selected image, writes its Compose configuration under `/opt/speech_to_text/`, and stores only the profile SQLite database under `/opt/speech_to_text/data/`, owned by UID/GID `10001:10001`. It binds port 8000 to the detected LAN address. When UFW is already active, it adds an allow rule for TCP port 8000 from only the detected LAN subnet. Re-running the installer updates the installer-owned Compose service to the selected pinned image and preserves the profile database. To roll back, rerun it with the previous release tag. The installer waits for the image healthcheck, checks `/healthz`, confirms that `turbo` is installed and `openvino-gpu` is ready with source precision available, and lists discovered microphone devices. It stops with an actionable error if no microphone devices are found.

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
