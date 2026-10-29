#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
OPENVINO_PROJECT="speech_to_text_ubx330m"
GEMMA_PROJECT="speech_to_text_gemma4"
LEGACY_GEMMA_PROJECT="speech_to_text"
LEGACY_GEMMA_CONTAINER="speech-to-text-gemma4"
GEMMA_COMPOSE="$PROJECT_DIR/compose.gemma4.yaml"
GEMMA_UBX_OVERRIDE="$PROJECT_DIR/compose.gemma4.ubx330m.yaml"
GEMMA_MICROPHONE_OVERRIDE="$PROJECT_DIR/compose.gemma4.microphone.yaml"

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "Docker is required."
docker info >/dev/null 2>&1 || fail "Docker is unavailable to this user. Start Docker and grant this user access, then retry."

if (( EUID == 0 )); then
  if [[ "${SUDO_UID:-}" =~ ^[0-9]+$ && "${SUDO_GID:-}" =~ ^[0-9]+$ && "$SUDO_UID" -ne 0 ]]; then
    APP_UID="${APP_UID:-$SUDO_UID}"
    APP_GID="${APP_GID:-$SUDO_GID}"
  else
    APP_UID="${APP_UID:-$(stat -c '%u' "$PROJECT_DIR")}"
    APP_GID="${APP_GID:-$(stat -c '%g' "$PROJECT_DIR")}"
  fi
  [[ "$APP_UID" =~ ^[0-9]+$ && "$APP_GID" =~ ^[0-9]+$ && "$APP_UID" -ne 0 ]] \
    || fail "Root deployment requires a non-root APP_UID/APP_GID or a checkout owned by a non-root user."
else
  APP_UID="${APP_UID:-$(id -u)}"
  APP_GID="${APP_GID:-$(id -g)}"
fi

if [[ -n "$(docker ps --quiet --filter "label=com.docker.compose.project=$OPENVINO_PROJECT" --filter status=running)" ]]; then
  fail "The OpenVINO service is running. Switch flavors explicitly with scripts/stop_service.sh, then rerun scripts/deploy_gemma4.sh."
fi
if [[ -n "$(docker ps --quiet \
  --filter "name=^/$LEGACY_GEMMA_CONTAINER$" \
  --filter "label=com.docker.compose.project=$LEGACY_GEMMA_PROJECT" \
  --filter "label=com.docker.compose.service=$LEGACY_GEMMA_CONTAINER")" ]]; then
  fail "A legacy Gemma service is already running under Compose project $LEGACY_GEMMA_PROJECT. Stop it with scripts/stop_service.sh before deploying this project."
fi

[[ -f "$GEMMA_COMPOSE" && -f "$GEMMA_UBX_OVERRIDE" && -f "$GEMMA_MICROPHONE_OVERRIDE" ]] \
  || fail "The Gemma Compose deployment files are incomplete."
docker network inspect shared-network >/dev/null 2>&1 \
  || fail "Required external network 'shared-network' is missing. Create it with 'docker network create shared-network', then retry."

command -v getent >/dev/null 2>&1 || fail "getent is required to detect the host audio group."
audio_group="$(getent group audio || true)"
audio_gid="$(awk -F: 'NR == 1 { print $3 }' <<< "$audio_group")"
[[ "$audio_gid" =~ ^[0-9]+$ ]] || fail "The host 'audio' group is unavailable; microphone access cannot be configured."
[[ -d /dev/snd ]] || fail "Host microphone devices at /dev/snd are unavailable. Audio-file deployment can use compose.gemma4.yaml directly."

mkdir -p "$PROJECT_DIR/models" "$PROJECT_DIR/data/ubx330m"
if (( EUID == 0 )); then
  chown "$APP_UID:$APP_GID" "$PROJECT_DIR/models" "$PROJECT_DIR/data/ubx330m" \
    || fail "Could not assign the model and UBX data directories to APP_UID/APP_GID $APP_UID:$APP_GID."
else
  [[ -w "$PROJECT_DIR/models" && -w "$PROJECT_DIR/data/ubx330m" ]] \
    || fail "The models and UBX data directories must be writable by the current host user."
fi

export APP_UID APP_GID
export AUDIO_GID="$audio_gid"
docker compose -p "$GEMMA_PROJECT" \
  -f "$GEMMA_COMPOSE" \
  -f "$GEMMA_UBX_OVERRIDE" \
  -f "$GEMMA_MICROPHONE_OVERRIDE" \
  up --build -d

printf '\nGemma is deployed on port 8000. Model, profile, and recording data persist under models/ and data/ubx330m/.\n'
printf 'To switch back to OpenVINO, run scripts/stop_service.sh, then scripts/deploy_openvino.sh.\n'
