#!/usr/bin/env bash

set -euo pipefail

TASK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASK_PLATFORM="$(uname -s)"
TASK_BROKER_PROJECT="speech-mqtt-tests-$$"
TASK_BROKER_STARTED=0

case "$TASK_PLATFORM" in
  Darwin) TASK_RUNTIME_EXTRA="${FULL_TEST_RUNTIME_EXTRA:-ctranslate2}" ;;
  Linux) TASK_RUNTIME_EXTRA="${FULL_TEST_RUNTIME_EXTRA:-openvino}" ;;
  *) printf 'Supported hosts: macOS and Linux (UBX-330M).\n' >&2; exit 1 ;;
esac

case "$TASK_RUNTIME_EXTRA" in
  ctranslate2|openvino) ;;
  *) printf 'FULL_TEST_RUNTIME_EXTRA must be ctranslate2 or openvino.\n' >&2; exit 1 ;;
esac

for task_command in uv npm docker; do
  if ! command -v "$task_command" >/dev/null 2>&1; then
    printf 'Required command not found: %s\n' "$task_command" >&2
    exit 1
  fi
done

cleanup() {
  task_exit_code=$?
  trap - EXIT
  if [[ "$TASK_BROKER_STARTED" == 1 ]]; then
    MQTT_BIND_ADDRESS=127.0.0.1 MQTT_PORT="$TASK_BROKER_PORT" \
      docker compose -p "$TASK_BROKER_PROJECT" -f "$TASK_ROOT/compose.mqtt.yaml" \
      down --volumes >/dev/null 2>&1 || true
  fi
  exit "$task_exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$TASK_ROOT"
printf 'Full tests on %s; runtime extra: %s\n' "$TASK_PLATFORM" "$TASK_RUNTIME_EXTRA"
docker info >/dev/null
docker compose version
uv sync --locked --extra backend --extra microphone --extra mqtt --extra test --extra "$TASK_RUNTIME_EXTRA"

TASK_BROKER_PORT="$(uv run --no-sync python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
TASK_BROKER_STARTED=1
MQTT_BIND_ADDRESS=127.0.0.1 MQTT_PORT="$TASK_BROKER_PORT" \
  docker compose -p "$TASK_BROKER_PROJECT" -f "$TASK_ROOT/compose.mqtt.yaml" \
  up -d --wait --wait-timeout 90
export MQTT_TEST_BROKER_URL="mqtt://127.0.0.1:$TASK_BROKER_PORT"

printf 'Running Python tests, including real MQTT publish/subscribe...\n'
uv run --no-sync python -m pytest -q

cd "$TASK_ROOT/speech_to_text/frontend"
npm ci
if [[ "$TASK_PLATFORM" == Linux && "${FULL_TEST_INSTALL_BROWSER_DEPS:-0}" == 1 ]]; then
  npx playwright install --with-deps chromium
else
  npx playwright install chromium
fi
npm run build
npm run test:e2e

printf '\nFull software test suite passed.\n'
if [[ "${FEATURE01_RUN_HARDWARE:-0}" != 1 ]]; then
  printf 'Physical microphone/model tests remain opt-in (FEATURE01_RUN_HARDWARE=1 and configured reference inputs).\n'
fi
