"""Audio-only RTSP capture using PyAV."""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
from urllib.parse import quote, urlsplit, urlunsplit, unquote

import numpy as np

from .errors import AudioInputError, ConfigurationError


class RTSPCaptureUnavailableError(AudioInputError):
    """PyAV is not installed for RTSP capture."""


@dataclass(frozen=True)
class RTSPAudioSourceFactory:
    """Picklable source factory; the resolved URL is deliberately excluded from repr."""

    _url: str = field(repr=False)
    open_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 5.0

    def __call__(self, *, device, on_audio, on_error):
        return RTSPAudioSource(
            self._url,
            on_audio=on_audio,
            on_error=on_error,
            open_timeout_seconds=self.open_timeout_seconds,
            read_timeout_seconds=self.read_timeout_seconds,
        )

    def preflight(self):
        """Check capture support before the workflow loads its model."""
        _av_module()


class RTSPAudioSource:
    """Decode the first audio stream to 16 kHz mono float32 PCM."""

    sample_rate = 16000
    channels = 1

    def __init__(
        self,
        url,
        *,
        on_audio,
        on_error,
        open_timeout_seconds=5.0,
        read_timeout_seconds=5.0,
    ):
        self._url = url
        self._on_audio = on_audio
        self._on_error = on_error
        self._open_timeout = open_timeout_seconds
        self._read_timeout = read_timeout_seconds
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._startup_error = None
        self._thread = None
        self.started = False
        self.finished = False

    def __repr__(self):
        return "RTSPAudioSource(url=<redacted>)"

    def start(self):
        if self.started:
            return
        self.started = True
        self._thread = threading.Thread(
            target=self._capture, name="rtsp-audio-capture", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(self._open_timeout + 1.0):
            self._stop.set()
            self.stop()
            raise AudioInputError("RTSP connection did not become ready within its timeout")
        if self._startup_error is not None:
            self._thread.join(self._read_timeout + 1.0)
            raise AudioInputError(self._startup_error)

    def _capture(self):
        av = None
        container = None
        try:
            av = _av_module()
            container = av.open(
                self._url,
                mode="r",
                options={"rtsp_transport": "tcp"},
                timeout=(self._open_timeout, self._read_timeout),
            )
            stream = next(
                (item for item in container.streams if item.type == "audio"), None
            )
            if stream is None:
                self._startup_error = "RTSP stream has no audio track"
                self._ready.set()
                return
            resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
            self._ready.set()
            for frame in container.decode(stream):
                if self._stop.is_set():
                    break
                for output in resampler.resample(frame):
                    if self._stop.is_set():
                        break
                    samples = np.asarray(output.to_ndarray(), dtype=np.float32).reshape(-1)
                    if samples.size:
                        self._on_audio(samples.copy())
            if not self._stop.is_set():
                self._fail("RTSP audio stream ended")
        except RTSPCaptureUnavailableError:
            self._report_error(
                "RTSP capture support is unavailable; install the backend extra"
            )
        except Exception:
            if not self._stop.is_set():
                self._report_error("Unable to decode RTSP audio stream")
        finally:
            if container is not None:
                try:
                    container.close()
                except Exception:
                    pass
            self.finished = True
            self._ready.set()

    def _report_error(self, message):
        if not self._ready.is_set():
            self._startup_error = message
            self._ready.set()
        else:
            self._fail(message)

    def _fail(self, message):
        if not self._stop.is_set():
            self._on_error(AudioInputError(message))

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(self._open_timeout + self._read_timeout + 1.0)
            if thread.is_alive():
                raise AudioInputError("RTSP capture did not stop within its timeout")
        self.started = False


def _av_module():
    try:
        import av
    except ImportError:
        raise RTSPCaptureUnavailableError(
            "RTSP capture requires PyAV; install the backend extra"
        ) from None
    return av


def create_rtsp_audio_source_factory(
    url, *, open_timeout_seconds=5.0, read_timeout_seconds=5.0
):
    """Validate direct RTSP URLs and return a picklable, secret-safe factory."""
    if not isinstance(url, str) or not url or any(
        ord(char) <= 32 or ord(char) == 127 for char in url
    ):
        raise ConfigurationError("rtsp_url must be a valid RTSP URL")
    try:
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
        valid = (
            parsed.scheme in {"rtsp", "rtsps"}
            and bool(parsed.hostname)
            and (parsed.port is None or 1 <= parsed.port <= 65535)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
        )
    except (ValueError, UnicodeError):
        valid = False
    if not valid:
        raise ConfigurationError(
            "rtsp_url must use rtsp:// or rtsps:// with a host and valid port, and no credentials or fragment"
        )
    _validate_timeouts(open_timeout_seconds, read_timeout_seconds)
    return RTSPAudioSourceFactory(url, float(open_timeout_seconds), float(read_timeout_seconds))


@dataclass(frozen=True)
class RTSPSourceConfiguration:
    """Validated, sanitized source identity and private capture factory."""

    rtsp_url: str | None
    rtsp_username: str | None
    rtsp_has_password: bool
    audio_source_factory: RTSPAudioSourceFactory = field(repr=False)
    _password: str | None = field(default=None, repr=False)
    rtsp_url_secret_ref: str | None = None

def normalize_rtsp_source(rtsp_url, rtsp_username=None, rtsp_password=None):
    """Normalize direct RTSP input, keeping authentication out of source metadata."""
    if not isinstance(rtsp_url, str):
        raise ConfigurationError("rtsp_url must be a valid RTSP URL")
    if any(ord(char) <= 32 or ord(char) == 127 for char in rtsp_url):
        raise ConfigurationError("rtsp_url must be a valid RTSP URL")
    try:
        parsed = urlsplit(rtsp_url)
        if parsed.fragment:
            raise ConfigurationError("rtsp_url must not contain a fragment")
        embedded = parsed.username is not None or parsed.password is not None
        if embedded and (rtsp_username is not None or rtsp_password is not None):
            raise ConfigurationError("Use either URL credentials or separate credential fields")
        if embedded:
            username = unquote(parsed.username or "")
            password = unquote(parsed.password or "") if parsed.password is not None else None
        else:
            username = rtsp_username
            password = rtsp_password
        if username is not None and (not isinstance(username, str) or not username):
            raise ConfigurationError("rtsp_username must be a non-empty string")
        if password is not None and not isinstance(password, str):
            raise ConfigurationError("rtsp_password must be a string or null")
        hostname = parsed.hostname
        if not hostname:
            raise ConfigurationError("rtsp_url must be a valid RTSP URL")
        host = f"[{hostname}]" if ":" in hostname else hostname
        if parsed.port is not None:
            host += f":{parsed.port}"
        clean_url = urlunsplit((parsed.scheme, host, parsed.path, parsed.query, ""))
        clean_factory = create_rtsp_audio_source_factory(clean_url)
        if username is None and password not in (None, ""):
            raise ConfigurationError("A password requires a username")
        auth = ""
        if username is not None:
            auth = quote(username, safe="")
            if password is not None:
                auth += ":" + quote(password, safe="")
            auth += "@"
        private_url = urlunsplit((parsed.scheme, auth + host, parsed.path, parsed.query, ""))
        factory = clean_factory if not auth else RTSPAudioSourceFactory(
            private_url, clean_factory.open_timeout_seconds, clean_factory.read_timeout_seconds
        )
        return RTSPSourceConfiguration(clean_url, username, password is not None and password != "", factory, password)
    except ConfigurationError:
        raise
    except (ValueError, UnicodeError):
        raise ConfigurationError("rtsp_url must be a valid RTSP URL") from None


def create_rtsp_secret_source_factory(
    secret_ref, *, open_timeout_seconds=5.0, read_timeout_seconds=5.0
):
    """Resolve one allowlisted environment reference without exposing its value."""
    import os
    import re

    if not isinstance(secret_ref, str) or not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*", secret_ref
    ):
        raise ConfigurationError("rtsp_url_secret_ref must be an environment variable name")
    allowed = {
        name.strip()
        for name in os.environ.get("SPEECH_TO_TEXT_RTSP_SECRET_REFS", "").split(",")
        if name.strip()
    }
    if secret_ref not in allowed:
        raise ConfigurationError("RTSP secret reference is not deployment-approved")
    resolved = os.environ.get(secret_ref)
    if not resolved:
        raise ConfigurationError("Approved RTSP secret reference is not configured")
    _validate_resolved_url(resolved)
    _validate_timeouts(open_timeout_seconds, read_timeout_seconds)
    return RTSPAudioSourceFactory(
        resolved, float(open_timeout_seconds), float(read_timeout_seconds)
    )


def _validate_resolved_url(url):
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme in {"rtsp", "rtsps"}
            and bool(parsed.hostname)
            and (parsed.port is None or 1 <= parsed.port <= 65535)
            and not parsed.fragment
            and not any(ord(char) <= 32 or ord(char) == 127 for char in url)
        )
    except (ValueError, UnicodeError):
        valid = False
    if not valid:
        raise ConfigurationError("Configured RTSP secret is not a valid RTSP URL")


def _validate_timeouts(open_timeout_seconds, read_timeout_seconds):
    import math

    for name, value in (
        ("open_timeout_seconds", open_timeout_seconds),
        ("read_timeout_seconds", read_timeout_seconds),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ConfigurationError(f"{name} must be a finite positive number")
