"""Persist and manage bounded host microphone recordings."""

from __future__ import annotations

import json
import os
import re
import threading
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from speech_to_text.features.model_deployment import (
    AudioCodecUnavailableError,
    AudioInputError,
    ConfigurationError,
    convert_audio_to_wav,
    create_rtsp_secret_source_factory,
    normalize_rtsp_source,
    start_audio_recording,
    validate_wav,
)


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class RecordingConflictError(RuntimeError):
    """A requested operation conflicts with the active recording."""


class RecordingConfigurationError(ValueError):
    """A live recording source definition is invalid or not approved."""


class RecordingUnavailableError(RuntimeError):
    """Microphone capture or recording storage is unavailable."""


class RecordingInvalidAudioError(ValueError):
    """An uploaded file is not a supported, complete audio recording."""


class RecordingTooLargeError(ValueError):
    """An uploaded file exceeds the configured byte limit."""


class RecordingService:
    def __init__(self, *, output_directory=None, audio_source_factory=None, max_seconds=120):
        self.output_directory = Path(
            output_directory
            or os.environ.get("SPEECH_TO_TEXT_RECORDINGS_DIR", "data/tuning-recordings")
        ).expanduser().resolve()
        self.audio_source_factory = audio_source_factory
        self.max_seconds = max_seconds
        self._lock = threading.RLock()
        self._records = {}
        self._active_id = None
        self._load_saved()

    def _paths(self, recording_id):
        key = uuid.UUID(recording_id).hex
        if key != recording_id:
            raise ValueError("Invalid recording ID")
        return self.output_directory / f"{key}.wav", self.output_directory / f"{key}.json"

    def _load_saved(self):
        if not self.output_directory.is_dir():
            return
        for manifest_path in self.output_directory.glob("*.json"):
            try:
                recording_id = manifest_path.stem
                wav_path, safe_manifest = self._paths(recording_id)
                if safe_manifest != manifest_path:
                    continue
                record = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(record, dict):
                    continue
                if not isinstance(record.get("created_at"), str):
                    continue
                if not isinstance(record.get("duration_seconds"), (int, float)):
                    continue
                if not isinstance(record.get("warnings", []), list):
                    continue
                if record.get("recording_id") != recording_id or record.get("status") not in {"completed", "failed"}:
                    continue
                if not wav_path.is_file():
                    continue
                record.setdefault("source", "microphone")
                record.setdefault("filename", None)
                record["_session"] = None
                self._records[recording_id] = record
            except (OSError, ValueError, json.JSONDecodeError):
                continue

    def _save_manifest(self, record):
        _, manifest_path = self._paths(record["recording_id"])
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: value for key, value in record.items() if not key.startswith("_")}
        temp_path = manifest_path.with_suffix(".json.tmp")
        try:
            temp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temp_path.replace(manifest_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def start(
        self,
        device=None,
        *,
        source_type="microphone",
        rtsp_url=None,
        rtsp_url_secret_ref=None,
        rtsp_username=None,
        rtsp_password=None,
    ):
        if not isinstance(source_type, str) or source_type not in {"microphone", "rtsp"}:
            raise RecordingConfigurationError("source_type must be microphone or rtsp")
        if device is not None and (not isinstance(device, str) or not device.strip()):
            raise RecordingConfigurationError("device must be a non-empty string or null")
        if rtsp_url is not None and not isinstance(rtsp_url, str):
            raise RecordingConfigurationError("rtsp_url must be a string or null")
        if rtsp_url_secret_ref is not None and not isinstance(rtsp_url_secret_ref, str):
            raise RecordingConfigurationError("rtsp_url_secret_ref must be a string or null")
        if source_type == "microphone":
            if rtsp_url is not None or rtsp_url_secret_ref is not None or rtsp_username is not None or rtsp_password is not None:
                raise RecordingConfigurationError("Microphone recordings cannot include RTSP fields")
            source_factory = self.audio_source_factory
            source_definition = {"source": "microphone", "device": device}
        else:
            if device is not None:
                raise RecordingConfigurationError("RTSP recordings cannot include a microphone device")
            if (rtsp_url is None) == (rtsp_url_secret_ref is None):
                raise RecordingConfigurationError("RTSP recordings require exactly one URL or secret reference")
            username = None
            has_password = False
            safe_url = rtsp_url
            try:
                if rtsp_url is not None:
                    config = normalize_rtsp_source(rtsp_url, rtsp_username, rtsp_password)
                    source_factory = config.audio_source_factory
                    safe_url = config.rtsp_url
                    username = config.rtsp_username
                    has_password = config.rtsp_has_password
                else:
                    if rtsp_username is not None or rtsp_password not in (None, ""):
                        raise ConfigurationError("Credential fields cannot be combined with an RTSP secret reference")
                    source_factory = create_rtsp_secret_source_factory(rtsp_url_secret_ref)
            except ConfigurationError as exc:
                raise RecordingConfigurationError(str(exc)) from None
            source_definition = {
                "source": "rtsp",
                "device": None,
                "rtsp_url": safe_url,
                "rtsp_url_secret_ref": rtsp_url_secret_ref,
                "rtsp_username": username,
                "rtsp_has_password": has_password,
            }
            # Tests and embedders can inject a source through the same production capture seam.
            if self.audio_source_factory is not None:
                source_factory = self.audio_source_factory

        with self._lock:
            if self._active_id is not None:
                raise RecordingConflictError("A recording is already active")
            recording_id = uuid.uuid4().hex
            audio_path, _ = self._paths(recording_id)
            record = {
                "recording_id": recording_id,
                "status": "recording",
                "duration_seconds": 0.0,
                **source_definition,
                "filename": None,
                "error": None,
                "warnings": [],
                "created_at": _utc_now(),
            }
            # Ensure storage errors fail the request before opening a host device.
            try:
                self.output_directory.mkdir(parents=True, exist_ok=True)
                if source_type == "rtsp" and hasattr(source_factory, "preflight"):
                    source_factory.preflight()
                session = start_audio_recording(
                    device,
                    audio_path,
                    max_seconds=self.max_seconds,
                    audio_source_factory=source_factory,
                )
            except (AudioInputError, OSError, ValueError) as exc:
                try:
                    audio_path.unlink(missing_ok=True)
                except OSError:
                    pass
                if source_type == "rtsp":
                    raise RecordingUnavailableError("Unable to start RTSP recording") from None
                raise RecordingUnavailableError("Unable to start microphone recording") from None
            record["_session"] = session
            self._records[recording_id] = record
            self._active_id = recording_id
            try:
                self._save_manifest(record)
            except Exception:
                self._records.pop(recording_id, None)
                self._active_id = None
                session.stop()
                try:
                    audio_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise RecordingUnavailableError("Unable to save recording metadata") from None
            threading.Thread(target=self._watch, args=(recording_id,), daemon=True).start()
            return self.snapshot(recording_id)

    @staticmethod
    def _display_filename(filename):
        # Treat both POSIX and Windows separators as path separators, then
        # remove control characters because this value is exposed to clients.
        name = str(filename or "audio.wav").replace("\\", "/").split("/")[-1]
        name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
        return (name or "audio.wav")[:255]

    def import_audio(self, fileobj, filename, *, max_bytes=20 * 1024 * 1024):
        """Persist a WAV or converted MP3/M4A without blocking host capture."""
        temporary_path = None
        converted_path = None
        created_audio_path = None
        created_manifest_path = None
        committed = False
        try:
            self.output_directory.mkdir(parents=True, exist_ok=True)
            extension = Path(self._display_filename(filename)).suffix.lower()
            if extension not in {".wav", ".mp3", ".m4a"}:
                raise RecordingInvalidAudioError("Audio file must be WAV, MP3, or M4A")
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=".upload-", suffix=extension,
                dir=self.output_directory, delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                total = 0
                while True:
                    chunk = fileobj.read(min(64 * 1024, max_bytes - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise RecordingTooLargeError("Uploaded audio exceeds the 20 MiB limit")
                    temporary.write(chunk)
            source_path = temporary_path
            if extension in {".mp3", ".m4a"}:
                with tempfile.NamedTemporaryFile(
                    mode="wb", prefix=".converted-", suffix=".wav",
                    dir=self.output_directory, delete=False
                ) as converted:
                    converted_path = Path(converted.name)
                try:
                    convert_audio_to_wav(
                        temporary_path, converted_path, max_seconds=self.max_seconds
                    )
                except AudioCodecUnavailableError as exc:
                    raise RecordingUnavailableError(
                        "Compressed audio decoding is unavailable"
                    ) from exc
                except AudioInputError as exc:
                    raise RecordingInvalidAudioError(str(exc)) from exc
                source_path = converted_path
            try:
                duration = validate_wav(source_path, max_seconds=self.max_seconds)
            except AudioInputError as exc:
                raise RecordingInvalidAudioError(str(exc)) from exc

            with self._lock:
                recording_id = uuid.uuid4().hex
                audio_path, manifest_path = self._paths(recording_id)
                # A hard link creates the final name atomically without ever
                # replacing an existing recording if an ID collision occurs.
                os.link(source_path, audio_path)
                created_audio_path = audio_path
                record = {
                    "recording_id": recording_id,
                    "status": "completed",
                    "duration_seconds": duration,
                    "device": None,
                    "error": None,
                    "warnings": [],
                    "created_at": _utc_now(),
                    "source": "upload",
                    "filename": self._display_filename(filename),
                    "_session": None,
                }
                created_manifest_path = manifest_path
                self._save_manifest(record)
                self._records[recording_id] = record
                committed = True
                return self.snapshot(recording_id)
        except (RecordingInvalidAudioError, RecordingTooLargeError):
            raise
        except OSError as exc:
            raise RecordingUnavailableError("Unable to store uploaded audio") from exc
        finally:
            cleanup_paths = [temporary_path, converted_path]
            if not committed:
                cleanup_paths.extend((created_manifest_path, created_audio_path))
            for path in cleanup_paths:
                if path is not None and path.exists():
                    try:
                        path.unlink()
                    except OSError:
                        pass

    def _refresh(self, recording_id):
        record = self._records.get(recording_id)
        if record is None:
            return None
        session = record.get("_session")
        if session is None:
            return record
        was_recording = record["status"] == "recording"
        record.update(session.snapshot())
        if record.get("source") == "rtsp":
            record["device"] = None
        if was_recording and record["status"] != "recording":
            if self._active_id == recording_id:
                self._active_id = None
            record["_session"] = None
            try:
                self._save_manifest(record)
            except OSError:
                record["status"] = "failed"
                record["error"] = "Unable to persist recording metadata"
        return record

    def _watch(self, recording_id):
        with self._lock:
            record = self._records.get(recording_id)
            session = record.get("_session") if record else None
        if session is not None:
            session.wait()
            with self._lock:
                self._refresh(recording_id)

    def snapshot(self, recording_id):
        with self._lock:
            record = self._refresh(recording_id)
            if record is None:
                return None
            return {key: value for key, value in record.items() if not key.startswith("_")}

    def get(self, recording_id):
        with self._lock:
            if recording_id not in self._records:
                return None
        return self.snapshot(recording_id)

    def list(self):
        with self._lock:
            snapshots = [self.snapshot(item) for item in self._records]
            return sorted(snapshots, key=lambda item: (item["created_at"], item["recording_id"]), reverse=True)

    def stop(self, recording_id):
        with self._lock:
            if recording_id not in self._records:
                return None
            session = self._records[recording_id].get("_session")
        if session is not None:
            session.stop()
        return self.snapshot(recording_id)

    def audio_path(self, recording_id):
        with self._lock:
            record = self._records.get(recording_id)
            if record is None:
                return None
            self._refresh(recording_id)
            if record is None:
                return None
            if record["status"] == "recording":
                raise RecordingConflictError("Recording is still active")
            wav_path, _ = self._paths(recording_id)
            try:
                return wav_path if wav_path.is_file() else None
            except OSError as exc:
                raise RecordingUnavailableError("Unable to access recording audio") from exc

    def delete(self, recording_id):
        with self._lock:
            record = self._records.get(recording_id)
            if record is None:
                return False
            self._refresh(recording_id)
            if record["status"] == "recording":
                raise RecordingConflictError("Active recordings cannot be deleted")
            wav_path, manifest_path = self._paths(recording_id)
            try:
                for path in (wav_path, manifest_path):
                    path.unlink(missing_ok=True)
            except OSError as exc:
                raise RecordingUnavailableError("Unable to delete recording") from exc
            del self._records[recording_id]
            return True

    def close(self):
        with self._lock:
            active_id = self._active_id
        if active_id is not None:
            self.stop(active_id)
