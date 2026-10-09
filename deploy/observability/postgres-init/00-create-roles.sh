#!/usr/bin/env bash
set -euo pipefail
: "${TELEMETRY_WRITER_PASSWORD:?Set TELEMETRY_WRITER_PASSWORD in .env}"
: "${TELEMETRY_GRAFANA_PASSWORD:?Set TELEMETRY_GRAFANA_PASSWORD in .env}"
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=writer_password="$TELEMETRY_WRITER_PASSWORD" \
  --set=grafana_password="$TELEMETRY_GRAFANA_PASSWORD" <<'SQL'
CREATE ROLE telemetry_writer LOGIN PASSWORD :'writer_password';
CREATE ROLE telemetry_grafana LOGIN PASSWORD :'grafana_password';
SQL
