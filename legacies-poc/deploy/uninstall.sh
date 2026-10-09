#!/usr/bin/env bash
# Take the station service back down. The counterpart to install.sh.
#
# Graded by what it destroys. Stopping the service and removing the unit is
# reversible — install.sh puts it all back. Deleting the TimescaleDB volume is
# not: those detection events are the record this whole system exists to
# produce, and no amount of reinstalling brings them back. So the service goes
# down by default and the data only goes when it is asked for by name.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="stt-stations"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

REMOVE_DATA=0
REMOVE_MODELS=0
REMOVE_CONFIG=0
REMOVE_VENV=0
ASSUME_YES=0
DRY_RUN=0

usage() {
  cat <<EOF
Usage: sudo $0 [options]

Stops the station service, removes its systemd unit, and stops the database
containers. Keeps everything else unless told otherwise.

  --remove-data      Also delete the TimescaleDB volume. DESTROYS every stored
                     keyword detection, permanently and unrecoverably.
  --remove-models    Also delete models/ (the converted weights, several GB).
                     Recoverable: convert_model.py rebuilds them.
  --remove-config    Also delete stations.json (labels, devices, keywords,
                     measured thresholds).
  --remove-venv      Also delete .venv/.
  --all              All of the above. Read what that means first.
  --yes              Do not ask. Intended for scripts, not for people.
  --dry-run          Print what would happen and change nothing.
  -h, --help         This.

Nothing here touches the source tree or git history.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --remove-data)   REMOVE_DATA=1 ;;
    --remove-models) REMOVE_MODELS=1 ;;
    --remove-config) REMOVE_CONFIG=1 ;;
    --remove-venv)   REMOVE_VENV=1 ;;
    --all)           REMOVE_DATA=1; REMOVE_MODELS=1; REMOVE_CONFIG=1; REMOVE_VENV=1 ;;
    --yes)           ASSUME_YES=1 ;;
    --dry-run)       DRY_RUN=1 ;;
    -h|--help)       usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

cd "$PROJECT_DIR"

# Never `systemctl list-unit-files | grep -q`: grep exits at the first match,
# systemctl dies of SIGPIPE partway through its several hundred lines, and
# `set -o pipefail` turns that into a failed test. The script then reports no
# unit installed and leaves the service running — which is exactly what it
# did on the UBX-330M.
unit_installed() {
  [ -f "$UNIT_PATH" ] && return 0
  listed="$(systemctl list-unit-files --no-legend "${SERVICE_NAME}.service" 2>/dev/null || true)"
  [ -n "$listed" ]
}

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
note() { printf '    %s\n' "$1"; }
run()  {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '    \033[2mwould run:\033[0m %s\n' "$*"
  else
    "$@"
  fi
}

# -- what is actually here -------------------------------------------------

step "Plan"
if unit_installed; then
  note "stop and remove the ${SERVICE_NAME} systemd unit ($(systemctl is-active "$SERVICE_NAME" 2>/dev/null || echo unknown))"
else
  note "systemd unit not installed — nothing to remove"
fi
note "stop the timescaledb and backend containers"

if [ "$REMOVE_DATA" -eq 1 ]; then
  # Say how much is about to be lost, in rows rather than in the abstract.
  ROWS="$(docker compose exec -T timescaledb psql -U postgres -d sttdemo -tAc \
          'SELECT COUNT(*) FROM detection_events' 2>/dev/null || echo "?")"
  printf '    \033[31mDELETE the database volume — %s stored detection(s), unrecoverable\033[0m\n' "$ROWS"
fi
[ "$REMOVE_MODELS" -eq 1 ] && note "delete models/ ($(du -sh models 2>/dev/null | cut -f1 || echo 'absent')) — convert_model.py can rebuild"
[ "$REMOVE_CONFIG" -eq 1 ] && note "delete stations.json (labels, devices, keywords, thresholds)"
[ "$REMOVE_VENV" -eq 1 ] && note "delete .venv/"

if [ "$REMOVE_DATA" -eq 0 ]; then
  note "keeping the database volume (pass --remove-data to delete it)"
fi

# -- confirm ---------------------------------------------------------------

if [ "$DRY_RUN" -eq 0 ] && [ "$ASSUME_YES" -eq 0 ]; then
  echo
  if [ "$REMOVE_DATA" -eq 1 ]; then
    # A y/n prompt is too easy to answer on autopilot for something that
    # cannot be undone.
    printf 'Type \033[1mdelete the data\033[0m to confirm: '
    read -r reply
    echo
    [ "$reply" = "delete the data" ] || { echo "Not confirmed — nothing changed."; exit 1; }
  else
    printf 'Continue? [y/N] '
    read -r reply
    echo
    case "$reply" in [yY]*) ;; *) echo "Nothing changed."; exit 1 ;; esac
  fi
fi

# -- service ---------------------------------------------------------------

step "Service"
if [ "$(id -u)" -ne 0 ] && [ "$DRY_RUN" -eq 0 ]; then
  note "not root: skipping systemd. Re-run with sudo to remove the unit."
else
  if unit_installed; then
    run systemctl stop "$SERVICE_NAME" || true
    run systemctl disable "$SERVICE_NAME" || true
    run rm -f "$UNIT_PATH"
    run systemctl daemon-reload
    run systemctl reset-failed "$SERVICE_NAME" || true
    note "unit removed"
  else
    note "no unit installed"
  fi
fi

# -- containers ------------------------------------------------------------

step "Containers"
if [ "$REMOVE_DATA" -eq 1 ]; then
  run docker compose down -v
  note "containers and volume removed"
else
  run docker compose down
  note "containers stopped; volume kept"
fi

# -- optional extras -------------------------------------------------------

if [ "$REMOVE_MODELS" -eq 1 ]; then
  step "Models"
  run rm -rf "$PROJECT_DIR/models"
  note "rebuild with: uv run python convert_model.py --runtime openvino --model turbo"
fi

if [ "$REMOVE_CONFIG" -eq 1 ]; then
  step "Configuration"
  run rm -f "$PROJECT_DIR/stations.json" "$PROJECT_DIR/stations.json.tmp"
  note "start again from deploy/stations.example.json"
fi

if [ "$REMOVE_VENV" -eq 1 ]; then
  step "Virtualenv"
  run rm -rf "$PROJECT_DIR/.venv"
  note "recreate with: uv sync --extra openvino"
fi

# -- what is left ----------------------------------------------------------

step "Remaining"
[ -d "$PROJECT_DIR/models" ]        && note "models/          $(du -sh "$PROJECT_DIR/models" | cut -f1)"
[ -f "$PROJECT_DIR/stations.json" ] && note "stations.json    kept"
[ -d "$PROJECT_DIR/.venv" ]         && note ".venv/           kept"
if [ "$REMOVE_DATA" -eq 0 ]; then
  note "database volume kept — 'docker compose up -d' brings the detections back"
fi
note "source tree and git history untouched"

if [ "$DRY_RUN" -eq 1 ]; then
  printf '\n\033[2mDry run — nothing was changed.\033[0m\n\n'
else
  printf '\n\033[32mService is down.\033[0m Reinstall with: sudo ./deploy/install.sh\n\n'
fi
