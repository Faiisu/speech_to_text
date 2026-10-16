"""SQLite persistence for reusable microphone workflow profiles."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from speech_to_text.workflows.transcribe_match_forward import (
    normalize_profile_decoding_options,
)

_DEFAULT_DECODING_OPTIONS_JSON = (
    # Frozen additive migration default for old profiles and rollback-safe databases.
    "'{\"beam_size\":1,\"temperature\":0.0,\"condition_on_previous_text\":false,"
    "\"no_repeat_ngram_size\":3,\"repetition_penalty\":1.0}'"
)


def _default_database_path() -> Path:
    configured = os.environ.get("SPEECH_TO_TEXT_PROFILE_DB")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "share" / "speech_to_text" / "profiles.sqlite3"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ProfileNameConflictError(ValueError):
    """Raised when a new or updated profile name matches another profile."""


class UnknownDestinationError(ValueError):
    """Raised when a profile selects a destination that is no longer saved."""


def _normalized_name(name: str) -> str:
    return unicodedata.normalize("NFC", name.strip()).casefold()


class SQLiteProfileStore:
    """Persist profile configuration while keeping database access thread-safe."""

    def __init__(self, database_path: str | Path | None = None):
        self.database_path = (
            Path(database_path).expanduser()
            if database_path is not None
            else _default_database_path()
        )
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            if str(self.database_path) != ":memory:":
                self.database_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                str(self.database_path), timeout=10, check_same_thread=False
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            if str(self.database_path) != ":memory:":
                try:
                    connection.execute("PRAGMA journal_mode = WAL")
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower():
                        raise
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS workflow_profiles (
                    profile_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    device TEXT,
                    execution_mode TEXT NOT NULL
                        CHECK (execution_mode IN ('shared', 'per_workflow_process')),
                    keywords_json TEXT NOT NULL,
                    silence_threshold REAL NOT NULL DEFAULT 0.00,
                    model TEXT NOT NULL DEFAULT 'turbo',
                    runtime TEXT NOT NULL DEFAULT 'openvino-gpu',
                    decoding_options_json TEXT NOT NULL DEFAULT {_DEFAULT_DECODING_OPTIONS_JSON},
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS workflow_destinations (
                    destination_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(workflow_profiles)"
                ).fetchall()
            }
            if "silence_threshold" not in columns:
                connection.execute(
                    "ALTER TABLE workflow_profiles ADD COLUMN "
                    "silence_threshold REAL NOT NULL DEFAULT 0.00"
                )
            if "model" not in columns:
                connection.execute(
                    "ALTER TABLE workflow_profiles ADD COLUMN "
                    "model TEXT NOT NULL DEFAULT 'turbo'"
                )
                # Freeze the effective model used by legacy profiles.
                legacy_model = os.environ.get("SPEECH_TO_TEXT_MODEL", "turbo")
                connection.execute(
                    "UPDATE workflow_profiles SET model = ?", (legacy_model,)
                )
            if "runtime" not in columns:
                connection.execute(
                    "ALTER TABLE workflow_profiles ADD COLUMN "
                    "runtime TEXT NOT NULL DEFAULT 'openvino-gpu'"
                )
                # Freeze the runtime that legacy profiles would have inherited.
                legacy_runtime = os.environ.get(
                    "SPEECH_TO_TEXT_RUNTIME", "openvino-gpu"
                )
                connection.execute(
                    "UPDATE workflow_profiles SET runtime = ?", (legacy_runtime,)
                )
            if "destination_id" not in columns:
                try:
                    connection.execute(
                        "ALTER TABLE workflow_profiles ADD COLUMN destination_id TEXT"
                    )
                except sqlite3.OperationalError as exc:
                    # A second process may finish this additive migration after our
                    # PRAGMA read and before this ALTER runs.
                    if "duplicate column name" not in str(exc).lower():
                        raise
            if "decoding_options_json" not in columns:
                try:
                    connection.execute(
                        "ALTER TABLE workflow_profiles ADD COLUMN "
                        "decoding_options_json TEXT NOT NULL DEFAULT "
                        f"{_DEFAULT_DECODING_OPTIONS_JSON}"
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            connection.commit()
            self._connection = connection
        return self._connection

    @staticmethod
    def _serialize(row: sqlite3.Row | None):
        if row is None:
            return None
        return {
            "profile_id": row["profile_id"],
            "name": row["name"],
            "device": row["device"],
            "execution_mode": row["execution_mode"],
            "keywords": json.loads(row["keywords_json"]),
            "silence_threshold": row["silence_threshold"],
            "model": row["model"],
            "runtime": row["runtime"],
            "destination_id": row["destination_id"],
            "decoding_options": json.loads(row["decoding_options_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_profiles(self):
        with self._lock:
            rows = self._db().execute(
                "SELECT * FROM workflow_profiles ORDER BY name, profile_id"
            ).fetchall()
            return [self._serialize(row) for row in rows]

    def get_profile(self, profile_id: str):
        with self._lock:
            row = self._db().execute(
                "SELECT * FROM workflow_profiles WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
            return self._serialize(row)

    def create_profile(self, profile: dict):
        now = _utc_now()
        decoding_options = normalize_profile_decoding_options(profile.get("decoding_options"))
        with self._lock:
            connection = self._db()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._ensure_name_available(connection, profile["name"])
                self._ensure_destination_exists(connection, profile.get("destination_id"))
                connection.execute(
                    """
                    INSERT INTO workflow_profiles
                        (profile_id, name, device, execution_mode, keywords_json,
                         silence_threshold, model, runtime, destination_id, decoding_options_json,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile["profile_id"],
                        profile["name"],
                        profile["device"],
                        profile["execution_mode"],
                        json.dumps(profile["keywords"], ensure_ascii=False),
                        profile["silence_threshold"],
                        profile["model"],
                        profile["runtime"],
                        profile.get("destination_id"),
                        json.dumps(decoding_options),
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM workflow_profiles WHERE profile_id = ?",
                    (profile["profile_id"],),
                ).fetchone()
                connection.commit()
                return self._serialize(row)
            except Exception:
                connection.rollback()
                raise

    def update_profile(self, profile_id: str, profile: dict):
        decoding_options = normalize_profile_decoding_options(profile.get("decoding_options"))
        with self._lock:
            connection = self._db()
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute(
                    "SELECT profile_id FROM workflow_profiles WHERE profile_id = ?",
                    (profile_id,),
                ).fetchone()
                if current is None:
                    connection.commit()
                    return None
                self._ensure_name_available(connection, profile["name"], profile_id)
                self._ensure_destination_exists(connection, profile.get("destination_id"))
                connection.execute(
                    """
                    UPDATE workflow_profiles
                    SET name = ?, device = ?, execution_mode = ?, keywords_json = ?,
                        silence_threshold = ?, model = ?, runtime = ?, destination_id = ?,
                        decoding_options_json = ?,
                        updated_at = ?
                    WHERE profile_id = ?
                    """,
                    (
                        profile["name"],
                        profile["device"],
                        profile["execution_mode"],
                        json.dumps(profile["keywords"], ensure_ascii=False),
                        profile["silence_threshold"],
                        profile["model"],
                        profile["runtime"],
                        profile.get("destination_id"),
                        json.dumps(decoding_options),
                        _utc_now(),
                        profile_id,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM workflow_profiles WHERE profile_id = ?",
                    (profile_id,),
                ).fetchone()
                connection.commit()
                return self._serialize(row)
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _ensure_name_available(
        connection: sqlite3.Connection, name: str, excluding_profile_id: str | None = None
    ) -> None:
        rows = connection.execute(
            "SELECT profile_id, name FROM workflow_profiles"
        ).fetchall()
        normalized = _normalized_name(name)
        if any(
            row["profile_id"] != excluding_profile_id
            and _normalized_name(row["name"]) == normalized
            for row in rows
        ):
            raise ProfileNameConflictError("A profile with this name already exists.")

    @staticmethod
    def _ensure_destination_exists(connection, destination_id):
        if destination_id is not None and connection.execute(
            "SELECT 1 FROM workflow_destinations WHERE destination_id = ?",
            (destination_id,),
        ).fetchone() is None:
            raise UnknownDestinationError("The selected destination no longer exists.")

    def delete_profile(self, profile_id: str) -> bool:
        with self._lock:
            cursor = self._db().execute(
                "DELETE FROM workflow_profiles WHERE profile_id = ?", (profile_id,)
            )
            self._db().commit()
            return cursor.rowcount > 0

    @staticmethod
    def _serialize_destination(row):
        if row is None:
            return None
        return {
            "destination_id": row["destination_id"],
            "name": row["name"],
            "config": json.loads(row["config_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_destinations(self):
        with self._lock:
            rows = self._db().execute(
                "SELECT * FROM workflow_destinations ORDER BY name, destination_id"
            ).fetchall()
            return [self._serialize_destination(row) for row in rows]

    def get_destination(self, destination_id: str):
        with self._lock:
            row = self._db().execute(
                "SELECT * FROM workflow_destinations WHERE destination_id = ?",
                (destination_id,),
            ).fetchone()
            return self._serialize_destination(row)

    def save_destination(self, destination_id: str, name: str, config: dict):
        now = _utc_now()
        with self._lock:
            connection = self._db()
            previous = connection.execute(
                "SELECT created_at FROM workflow_destinations WHERE destination_id = ?",
                (destination_id,),
            ).fetchone()
            created_at = previous["created_at"] if previous else now
            connection.execute(
                """INSERT INTO workflow_destinations
                   (destination_id, name, config_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(destination_id) DO UPDATE SET
                     name = excluded.name, config_json = excluded.config_json,
                     updated_at = excluded.updated_at""",
                (
                    destination_id,
                    name,
                    json.dumps(config, ensure_ascii=False),
                    created_at,
                    now,
                ),
            )
            connection.commit()
            return self.get_destination(destination_id)

    def destination_is_referenced(self, destination_id: str) -> bool:
        with self._lock:
            return self._db().execute(
                "SELECT 1 FROM workflow_profiles WHERE destination_id = ? LIMIT 1",
                (destination_id,),
            ).fetchone() is not None

    def delete_destination(self, destination_id: str) -> bool:
        with self._lock:
            connection = self._db()
            try:
                connection.execute("BEGIN IMMEDIATE")
                if self.destination_is_referenced(destination_id):
                    connection.rollback()
                    return False
                cursor = connection.execute(
                    "DELETE FROM workflow_destinations WHERE destination_id = ?",
                    (destination_id,),
                )
                connection.commit()
                return cursor.rowcount > 0
            except Exception:
                connection.rollback()
                raise

    def close(self):
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
