"""Configurable JSON POST adapter with bounded transient retries."""

from dataclasses import dataclass
import json
import math
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class ForwardingError(RuntimeError):
    """Delivery failure with safe status and retry context."""

    def __init__(self, message, *, status_code=None, attempts=1, transient=False):
        super().__init__(message)
        self.status_code = status_code
        self.attempts = attempts
        self.transient = transient


@dataclass(frozen=True)
class HttpForwarderConfig:
    endpoint_url: str
    bearer_token: str | None = None
    timeout_seconds: float = 5.0
    max_attempts: int = 3
    backoff_seconds: float = 0.25
    max_backoff_seconds: float = 2.0

    def __post_init__(self):
        if not isinstance(self.endpoint_url, str) or not self.endpoint_url.strip():
            raise ValueError("endpoint_url must be a non-empty HTTP(S) URL")
        parsed_url = urlsplit(self.endpoint_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("endpoint_url must be a valid HTTP(S) URL")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or self.max_attempts < 1
        ):
            raise ValueError("max_attempts must be at least 1")
        for name, value in (
            ("backoff_seconds", self.backoff_seconds),
            ("max_backoff_seconds", self.max_backoff_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be finite and non-negative")
        if self.bearer_token is not None and not isinstance(self.bearer_token, str):
            raise ValueError("bearer_token must be a string or None")


@dataclass(frozen=True)
class ForwardingReceipt:
    event_id: str
    status_code: int
    attempts: int


class HttpForwarder:
    """Send one JSON record to the configured endpoint."""

    def __init__(self, config, *, opener=urlopen, sleeper=time.sleep):
        if not isinstance(config, HttpForwarderConfig):
            raise TypeError("config must be an HttpForwarderConfig")
        self.config = config
        self._opener = opener
        self._sleeper = sleeper

    def forward(self, record):
        """POST a completed-source record using its ID as idempotency key."""
        if not isinstance(record, dict):
            raise TypeError("record must be a mapping")
        source_id = record.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("record.source_id must be a non-empty string")
        body = json.dumps(record, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Idempotency-Key": source_id,
        }
        if self.config.bearer_token:
            headers["Authorization"] = f"Bearer {self.config.bearer_token}"
        request = Request(
            self.config.endpoint_url, data=body, headers=headers, method="POST"
        )
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                with self._opener(request, timeout=self.config.timeout_seconds) as response:
                    status = response.getcode()
                if 200 <= status < 300:
                    return ForwardingReceipt(source_id, status, attempt)
                transient = status == 429 or 500 <= status <= 599
                if not transient:
                    raise ForwardingError(
                        f"Forwarding endpoint returned HTTP {status}",
                        status_code=status,
                        attempts=attempt,
                    )
                error = ForwardingError(
                    f"Forwarding endpoint returned transient HTTP {status}",
                    status_code=status,
                    attempts=attempt,
                    transient=True,
                )
            except HTTPError as exc:
                status = exc.code
                transient = status == 429 or 500 <= status <= 599
                error = ForwardingError(
                    f"Forwarding endpoint returned HTTP {status}",
                    status_code=status,
                    attempts=attempt,
                    transient=transient,
                )
                if not transient:
                    raise error from exc
            except (URLError, TimeoutError, OSError) as exc:
                error = ForwardingError(
                    f"Forwarding request failed ({type(exc).__name__})",
                    attempts=attempt,
                    transient=True,
                )
            if attempt == self.config.max_attempts:
                raise ForwardingError(
                    f"Forwarding failed after {attempt} attempts: {error}",
                    status_code=error.status_code,
                    attempts=attempt,
                    transient=True,
                ) from error
            delay = min(
                self.config.backoff_seconds * (2 ** (attempt - 1)),
                self.config.max_backoff_seconds,
            )
            self._sleeper(delay)
