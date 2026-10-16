"""Encryption key management for saved camera profile passwords."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading

from cryptography.fernet import Fernet, InvalidToken


class ProfileCredentialError(RuntimeError):
    """Saved profile credentials cannot be read or safely persisted."""


class ProfileCredentialCipher:
    def __init__(self, database_path):
        self._lock = threading.Lock()
        self._memory_key = None
        self._key_path = None if str(database_path) == ":memory:" else Path(f"{database_path}.key")

    def _fernet(self, *, create):
        with self._lock:
            if self._key_path is None:
                if self._memory_key is None:
                    if not create:
                        raise ProfileCredentialError("Saved camera credentials are unavailable")
                    self._memory_key = Fernet.generate_key()
                key = self._memory_key
            else:
                path = self._key_path
                try:
                    key = path.read_bytes()
                    path.chmod(0o600)
                except FileNotFoundError:
                    if not create:
                        raise ProfileCredentialError("Saved camera credentials are unavailable") from None
                    path.parent.mkdir(parents=True, exist_ok=True)
                    key = Fernet.generate_key()
                    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
                    try:
                        os.fchmod(descriptor, 0o600)
                        with os.fdopen(descriptor, "wb") as output:
                            output.write(key)
                            output.flush()
                            os.fsync(output.fileno())
                        try:
                            os.link(temporary, path)
                        except FileExistsError:
                            key = path.read_bytes()
                        path.chmod(0o600)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
                except OSError:
                    raise ProfileCredentialError("Saved camera credentials are unavailable") from None
            if self._key_path is not None:
                try:
                    self._key_path.chmod(0o600)
                except OSError:
                    raise ProfileCredentialError("Saved camera credentials are unavailable") from None
            try:
                return Fernet(key)
            except (TypeError, ValueError):
                raise ProfileCredentialError("Saved camera credentials are unavailable") from None

    def encrypt(self, password):
        try:
            return self._fernet(create=True).encrypt(password.encode("utf-8")).decode("ascii")
        except (OSError, UnicodeError, ValueError):
            raise ProfileCredentialError("Saved camera credentials are unavailable") from None

    def decrypt(self, token):
        try:
            return self._fernet(create=False).decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, OSError, UnicodeError, ValueError):
            raise ProfileCredentialError("Saved camera credentials are unavailable") from None
