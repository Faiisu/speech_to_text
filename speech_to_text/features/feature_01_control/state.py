"""Lifecycle state shared by Feature 01's HTTP route groups."""

import os
import threading
import time
from urllib.parse import urlsplit


class FeatureState:
    def __init__(
        self,
        *,
        runtime_factory=None,
        audio_source_factory=None,
        process_audio_source_factory=None,
    ):
        self.runtime_factory = runtime_factory
        self.audio_source_factory = audio_source_factory
        self.process_audio_source_factory = process_audio_source_factory
        self.models = {}
        self.sessions = {}
        self.session_start_lock = threading.Lock()
        self.groups = {}
        self.recent_errors = []
        self.host_bridge_enabled = os.environ.get(
            "SPEECH_TO_TEXT_HOST_MICROPHONE_BRIDGE", ""
        ).lower() in {"1", "true", "yes"}
        self.host_bridge_url = os.environ.get(
            "SPEECH_TO_TEXT_HOST_MICROPHONE_BRIDGE_URL", "http://127.0.0.1:18767/api"
        ).rstrip("/")
        bridge_url = urlsplit(self.host_bridge_url)
        if (
            bridge_url.scheme != "http"
            or bridge_url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or bridge_url.username
            or bridge_url.password
            or bridge_url.path != "/api"
        ):
            raise ValueError(
                "SPEECH_TO_TEXT_HOST_MICROPHONE_BRIDGE_URL must use local HTTP at /api"
            )

    def close_all(self):
        for session in list(self.sessions.values()):
            try:
                session.stop(timeout=5)
            except Exception as exc:
                self.record_error(str(exc))
        for group_id, group in list(self.groups.items()):
            try:
                self.stop_process_group(group_id, timeout=5)
            except Exception as exc:
                try:
                    group.abort(timeout=2)
                except Exception as abort_exc:  # noqa: BLE001
                    self.record_error(str(abort_exc))
                self.record_error(str(exc))
        for handle in list(self.models.values()):
            try:
                handle.close(timeout=5)
            except Exception as exc:  # noqa: BLE001
                self.record_error(str(exc))

    def record_error(self, message):
        self.recent_errors.append({"at": time.time(), "message": message})
        self.recent_errors[:] = self.recent_errors[-20:]

    def stop_process_group(self, group_id, *, timeout=30):
        group = self.groups[group_id]
        group.stop(timeout=timeout)
        return group

    def find_session(self, source_id):
        session = self.sessions.get(source_id)
        if session is not None:
            return session
        return next(
            (
                session
                for group in self.groups.values()
                for session in group.sessions
                if session.source_id == source_id
            ),
            None,
        )

    def session_summaries(self, feature_id):
        rows = []
        for kind, collection in (
            ("microphone", self.sessions),
            ("process", self.groups),
        ):
            for value in collection.values():
                items = getattr(value, "sessions", [value])
                for session in items:
                    if not getattr(session, "_terminal", False):
                        rows.append(
                            {
                                "feature": feature_id,
                                "kind": kind,
                                "id": session.source_id,
                                "state": "running",
                            }
                        )
        return rows
