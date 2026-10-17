#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PID=""

cleanup() {
  if [[ -n "$BACKEND_PID" ]]; then
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  printf 'This script is for macOS.\n' >&2
  exit 1
fi

for command_name in uv npm; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'Required command not found: %s\n' "$command_name" >&2
    exit 1
  fi
done

cd "$ROOT_DIR"

printf 'Installing Python dependencies...\n'
uv sync --locked --extra backend --extra microphone --extra ctranslate2 --extra model-management --extra mqtt

if [[ ! -x "$ROOT_DIR/speech_to_text/frontend/node_modules/.bin/vite" ]]; then
  printf 'Installing frontend dependencies...\n'
  (cd "$ROOT_DIR/speech_to_text/frontend" && npm ci)
fi

: "${SPEECH_TO_TEXT_RUNTIME:=ctranslate2}"
if [[ "$SPEECH_TO_TEXT_RUNTIME" == "ctranslate2" ]]; then
  : "${SPEECH_TO_TEXT_PRECISION:=int8}"
fi
export SPEECH_TO_TEXT_RUNTIME SPEECH_TO_TEXT_PRECISION

printf 'Starting backend at http://127.0.0.1:8000\n'
uv run --no-sync python -m uvicorn speech_to_text.backend.app:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf 'Starting frontend at http://127.0.0.1:5173 (press Ctrl-C to stop)\n'
cd "$ROOT_DIR/speech_to_text/frontend"
npm run dev -- --host 127.0.0.1
