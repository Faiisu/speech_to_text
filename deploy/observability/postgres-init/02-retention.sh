#!/usr/bin/env bash
set -euo pipefail
retention_days="${SPEECH_TO_TEXT_TELEMETRY_RETENTION_DAYS:-30}"
[[ "$retention_days" =~ ^[1-9][0-9]*$ ]] || { echo "Retention days must be a positive integer" >&2; exit 1; }
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --command="SELECT ensure_observability_retention(${retention_days});"
