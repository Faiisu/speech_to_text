# UBX deployment flavor switching

## Goal

Provide explicit operator commands to deploy the existing OpenVINO service, deploy the optional Gemma Transformers service, and stop either service without affecting unrelated containers or persistent data.

## Ownership and compatibility

- `scripts/deploy_openvino.sh` delegates to the existing UBX installer and preserves its defaults and checks.
- `scripts/deploy_gemma4.sh` composes the existing Gemma base, UBX network/data, and microphone Compose files.
- `scripts/stop_service.sh` owns stopping the two named application Compose projects.
- `docs/deployment.md` is the operator-facing source of truth.
- Do not edit `legacies-poc/`.

## Requirements

1. The OpenVINO and Gemma application services are mutually exclusive on host TCP port 8000. Each deploy script must clearly refuse when the other project's container is running; it must not stop the other flavor automatically.
2. Redeploying the same flavor is idempotent: OpenVINO continues through its existing installer, and Gemma uses Compose `up --build -d` in its fixed project.
3. OpenVINO deployment reuses `scripts/install_ubx330m.sh` and verifies its generated `data/ubx330m/compose.yaml` exists after a successful install.
4. Gemma deployment requires the external `shared-network`. It sets host UID/GID and audio GID, and uses the existing `/dev/snd` microphone override. It can deploy on a fresh UBX checkout without first generating the OpenVINO Compose file.
5. The UBX Gemma override joins `shared-network`, sets `SPEECH_TO_TEXT_DESTINATION_ALLOWED_NETWORKS` to `0.0.0.0/0,::/0`, and mounts `models/` at `/opt/models` plus `data/ubx330m/` at `/data`. This preserves the existing profile database and model-tuning records across flavor switches.
6. Deploy scripts refuse root execution and do not use `sudo` to perform deployment actions. The stop script may run as root, including through `sudo`; it must preserve the invoking user's UID/GID for Compose interpolation when those values are available.
7. The stop script targets only Compose projects `speech_to_text_ubx330m` and `speech_to_text_gemma4`. It uses Compose `down` without `--volumes`; it never deletes bind-mounted profile/model data or stops unrelated containers.
8. Deployment docs describe switching commands, prerequisites, port ownership, data persistence, and the stop behavior. Project structure lists the new Compose and script files.

## Acceptance

- Starting an inactive flavor while the other is running fails with an actionable message that names `scripts/stop_service.sh`.
- The operator can stop and then deploy either flavor using the documented commands.
- The OpenVINO installer retains the existing default configuration and remains the OpenVINO deployment implementation.
- Gemma uses the in-process Transformers adapter, retains the UBX profile data mount, and joins the same external network policy as OpenVINO.
- The stop script runs only the two explicitly named Compose projects and does not invoke volume deletion or broad Docker stop commands.

## Out of scope

- Automatic flavor switching or automatic stopping of an active service.
- Deleting/resetting persistent model, profile, recording, cache, or broker data.
- Managing unrelated Docker containers or external networks.
- Changing the OpenVINO installer, its default model, or Gemma inference behavior.
