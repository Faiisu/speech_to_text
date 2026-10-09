#!/usr/bin/env bash
# Install or update the station service on the UBX-330M. Safe to re-run.
#
# The STT service runs on the host rather than in a container: the OpenVINO
# GPU plugin has to match the host's i915 driver, and a container image that
# drifts from the host is the usual way an iGPU deploy breaks. The database
# has no such constraint, so it stays in Docker.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="stt-stations"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
RUN_USER="${SUDO_USER:-$(id -un)}"
SKIP_CONVERT="${SKIP_CONVERT:-0}"

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

cd "$PROJECT_DIR"

step "Dependencies"
# shellcheck source=deploy/find-uv.sh
. "$PROJECT_DIR/deploy/find-uv.sh"
echo "using uv at $UV"
# Run as the invoking user, not root: uv writes .venv/ and its cache, and
# doing that as root leaves a tree the operator can no longer update.
as_user() {
  if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
    sudo -u "$SUDO_USER" -H "$@"
  else
    "$@"
  fi
}
as_user "$UV" sync --extra openvino

step "Model: typhoon-whisper-turbo -> OpenVINO IR"
if [ -d "models/openvino-turbo" ] && [ "$SKIP_CONVERT" != "1" ]; then
  echo "already converted (SKIP_CONVERT=1 to skip this check entirely)"
elif [ "$SKIP_CONVERT" = "1" ]; then
  echo "skipped"
else
  # One IR serves every openvino-* runtime; the device is chosen at load time.
  as_user "$UV" run python convert_model.py --runtime openvino --model turbo
fi

step "Database"
docker compose up -d timescaledb
for _ in $(seq 1 30); do
  docker compose exec -T timescaledb pg_isready -U postgres > /dev/null 2>&1 && break
  sleep 2
done
# init.sql only runs on a *fresh* volume, so an existing deployment never sees
# schema changes from it. The migrations are what actually update it, and each
# one is written to be safe to re-apply.
for migration in backend/db/migrations/*.sql; do
  echo "applying $(basename "$migration")"
  docker compose exec -T timescaledb psql -U postgres -d sttdemo -v ON_ERROR_STOP=1 < "$migration" > /dev/null
done
docker compose up -d --build backend

step "Configuration"
if [ ! -f stations.json ]; then
  cp deploy/stations.example.json stations.json
  echo "wrote stations.json from the example — edit it, or use the web UI, before starting"
else
  echo "stations.json already exists, left alone"
fi

step "Service"
if [ "$(id -u)" -ne 0 ]; then
  echo "not root: skipping systemd install."
  echo "Re-run with sudo to install the unit, or start manually:"
  echo "  $UV run uvicorn production_server:app --host 0.0.0.0 --port 8080"
else
  # The unit gets uv's absolute path: systemd starts with a bare PATH and
  # would not find it either.
  sed -e "s|@PROJECT_DIR@|${PROJECT_DIR}|g" -e "s|@USER@|${RUN_USER}|g" \
      -e "s|@UV@|${UV}|g" \
    deploy/stt-stations.service > "$UNIT_PATH"
  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME"
  systemctl restart "$SERVICE_NAME"
  sleep 2
  systemctl --no-pager --lines=10 status "$SERVICE_NAME" || true
fi

step "Preflight"
as_user env UV="$UV" ./deploy/preflight.sh || echo "(preflight reported problems — see above)"

cat <<EOF

Done. The operator UI is on http://$(hostname -I 2>/dev/null | awk '{print $1}'):8080

  logs      journalctl -u ${SERVICE_NAME} -f
  restart   systemctl restart ${SERVICE_NAME}
  db        docker compose logs -f timescaledb

EOF
