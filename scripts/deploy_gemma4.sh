#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
OPENVINO_PROJECT="speech_to_text_ubx330m"
GEMMA_PROJECT="speech_to_text_gemma4"
GEMMA_COMPOSE="$PROJECT_DIR/compose.gemma4.yaml"
GEMMA_UBX_OVERRIDE="$PROJECT_DIR/compose.gemma4.ubx330m.yaml"
GEMMA_MICROPHONE_OVERRIDE="$PROJECT_DIR/compose.gemma4.microphone.yaml"

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

(( EUID != 0 )) || fail "Run this script as the normal host user, not root."
command -v docker >/dev/null 2>&1 || fail "Docker is required."
docker info >/dev/null 2>&1 || fail "Docker is unavailable to this user. Start Docker and grant this user access, then retry."

if [[ -n "$(docker ps --quiet --filter "label=com.docker.compose.project=$OPENVINO_PROJECT" --filter status=running)" ]]; then
  fail "The OpenVINO service is running. Switch flavors explicitly with scripts/stop_service.sh, then rerun scripts/deploy_gemma4.sh."
fi

[[ -f "$GEMMA_COMPOSE" && -f "$GEMMA_UBX_OVERRIDE" && -f "$GEMMA_MICROPHONE_OVERRIDE" ]] \
  || fail "The Gemma Compose deployment files are incomplete."
docker network inspect shared-network >/dev/null 2>&1 \
  || fail "Required external network 'shared-network' is missing. Create it with 'docker network create shared-network', then retry."

command -v getent >/dev/null 2>&1 || fail "getent is required to detect the host audio group."
audio_group="$(getent group audio || true)"
audio_gid="${audio_group##*:}"
[[ "$audio_gid" =~ ^[0-9]+$ ]] || fail "The host 'audio' group is unavailable; microphone access cannot be configured."
[[ -d /dev/snd ]] || fail "Host microphone devices at /dev/snd are unavailable. Audio-file deployment can use compose.gemma4.yaml directly."

mkdir -p "$PROJECT_DIR/models" "$PROJECT_DIR/data/ubx330m"
[[ -w "$PROJECT_DIR/models" && -w "$PROJECT_DIR/data/ubx330m" ]] \
  || fail "The models and UBX data directories must be writable by the current host user."

export APP_UID="$(id -u)"
export APP_GID="$(id -g)"
export AUDIO_GID="$audio_gid"
docker compose -p "$GEMMA_PROJECT" \
  -f "$GEMMA_COMPOSE" \
  -f "$GEMMA_UBX_OVERRIDE" \
  -f "$GEMMA_MICROPHONE_OVERRIDE" \
  up --build -d

printf '\nGemma is deployed on port 8000. Model, profile, and recording data persist under models/ and data/ubx330m/.\n'
printf 'To switch back to OpenVINO, run scripts/stop_service.sh, then scripts/deploy_openvino.sh.\n'
