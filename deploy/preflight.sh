#!/usr/bin/env bash
# Refuse to deploy onto a machine where the iGPU won't actually work.
#
# Every check here corresponds to a way an OpenVINO GPU deploy fails *after*
# it looks like it succeeded: the service starts, the panel says "running",
# and the first inference throws — or worse, silently falls back to CPU and
# quietly misses half the audio. Finding out here, with the reason, is the
# whole point of this script.
set -uo pipefail

FAIL=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=1; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }

cd "$(dirname "$0")/.." || exit 1
# shellcheck source=deploy/find-uv.sh
. "$(dirname "$0")/find-uv.sh"

echo
echo "Render node"
if compgen -G "/dev/dri/render*" > /dev/null; then
  ok "found $(echo /dev/dri/render*)"
else
  bad "no /dev/dri/render* — the kernel isn't exposing an iGPU. Check that i915 is loaded (lsmod | grep i915)."
fi

echo
echo "Permissions"
if [ -n "${SUDO_USER:-}" ]; then TARGET_USER="$SUDO_USER"; else TARGET_USER="$(id -un)"; fi
if id -nG "$TARGET_USER" 2>/dev/null | tr ' ' '\n' | grep -qx "render"; then
  ok "$TARGET_USER is in the 'render' group"
elif id -nG "$TARGET_USER" 2>/dev/null | tr ' ' '\n' | grep -qx "video"; then
  warn "$TARGET_USER is in 'video' but not 'render' — on most distros the render node needs 'render'"
else
  bad "$TARGET_USER is in neither 'render' nor 'video'. Fix: sudo usermod -aG render $TARGET_USER (then log out and back in)"
fi

echo
echo "Intel compute runtime"
if command -v clinfo > /dev/null 2>&1 && clinfo 2>/dev/null | grep -qi "Intel"; then
  ok "Intel OpenCL runtime present"
else
  warn "clinfo shows no Intel runtime — install intel-opencl-icd if OpenVINO can't see the GPU below"
fi

echo
echo "OpenVINO"
if ! "$UV" run python -c "import openvino" > /dev/null 2>&1; then
  bad "openvino not importable. Fix: uv sync --extra openvino"
else
  DEVICES="$("$UV" run python -c "import openvino; print(','.join(openvino.Core().available_devices))" 2>/dev/null)"
  if echo "$DEVICES" | tr ',' '\n' | grep -q "^GPU"; then
    ok "OpenVINO devices: $DEVICES"
    NAME="$("$UV" run python -c "import openvino; c=openvino.Core(); print(c.get_property('GPU','FULL_DEVICE_NAME'))" 2>/dev/null)"
    [ -n "$NAME" ] && ok "GPU is: $NAME"
  else
    bad "OpenVINO sees no GPU (devices: ${DEVICES:-none}). The runtime is installed but the driver isn't usable."
  fi
fi

echo
echo "Model"
if [ -d "models/openvino-turbo" ]; then
  ok "typhoon-whisper-turbo converted at models/openvino-turbo ($(du -sh models/openvino-turbo | cut -f1))"
else
  bad "models/openvino-turbo missing. Fix: "$UV" run python convert_model.py --runtime openvino --model turbo"
fi

echo
echo "Microphones"
MICS="$("$UV" run python -c "
from stations.capture import input_devices
for d in input_devices(): print(f\"    [{d['index']}] {d['name']}\")
" 2>/dev/null)"
if [ -n "$MICS" ]; then
  ok "input devices:"; echo "$MICS"
else
  bad "no audio input devices found"
fi

echo
echo "Network streams (CCTV)"
if command -v ffmpeg > /dev/null 2>&1; then
  ok "ffmpeg present — stations can read RTSP sources"
else
  warn "ffmpeg missing: microphone stations work, RTSP/CCTV stations do not. Fix: sudo apt-get install -y ffmpeg"
fi

if [ -f stations.json ]; then
  echo
  echo "Configured stations"
  "$UV" run python -c "
import sys
from stations.config import load, ConfigError
from stations.capture import check_device, DeviceError
try:
    settings = load()
except ConfigError as exc:
    print(f'    config error: {exc}'); sys.exit(1)
for s in settings.stations:
    if not s.enabled:
        print(f'    - {s.label}: disabled'); continue
    try:
        check_device(s.device); print(f'    ok {s.label} -> {s.device}')
    except DeviceError as exc:
        print(f'    MISSING {s.label}: {exc}'); sys.exit(1)
" || bad "a configured station's microphone is missing or ambiguous"
fi

echo
echo "Database"
if docker compose ps timescaledb 2>/dev/null | grep -q "Up\|running"; then
  ok "TimescaleDB container is up"
else
  warn "TimescaleDB is not running — deploy/install.sh will start it"
fi

echo
if [ "$FAIL" -eq 0 ]; then
  printf '\033[32mPreflight passed.\033[0m\n\n'
else
  printf '\033[31mPreflight failed — fix the ✗ items above before deploying.\033[0m\n\n'
fi
exit "$FAIL"
