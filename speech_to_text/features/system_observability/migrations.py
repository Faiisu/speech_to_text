"""Apply checked-in idempotent TimescaleDB migrations."""

from pathlib import Path
import os

MIGRATIONS = Path(__file__).with_name("migrations")


def apply_migrations(database_url: str, *, connect=None, retention_days: int | None = None):
    if connect is None:
        from .writer import _connect
        connect = _connect
    retention_days = int(retention_days or os.getenv("SPEECH_TO_TEXT_TELEMETRY_RETENTION_DAYS", "30"))
    if retention_days < 1:
        raise ValueError("retention_days must be positive")
    connection = connect(database_url)
    try:
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE IF NOT EXISTS telemetry_schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
            for path in sorted(MIGRATIONS.glob("*.sql")):
                version = path.stem
                cursor.execute("SELECT 1 FROM telemetry_schema_migrations WHERE version = %s", (version,))
                if cursor.fetchone():
                    continue
                cursor.execute(path.read_text(encoding="utf-8"))
                cursor.execute("INSERT INTO telemetry_schema_migrations(version) VALUES (%s) ON CONFLICT DO NOTHING", (version,))
            cursor.execute("SELECT ensure_observability_retention(%s)", (retention_days,))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    import sys
    database_url = os.getenv("SPEECH_TO_TEXT_TELEMETRY_MIGRATION_DATABASE_URL", "").strip()
    if not database_url:
        sys.exit("Set SPEECH_TO_TEXT_TELEMETRY_MIGRATION_DATABASE_URL to a privileged PostgreSQL URL.")
    apply_migrations(database_url)
