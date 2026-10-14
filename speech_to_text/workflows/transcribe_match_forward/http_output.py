"""Workflow-owned HTTP JSON delivery with bounded transient retries."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import http.client
import ipaddress
import json
import math
import socket
import ssl
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


def _check_endpoint(endpoint_url):
    if not isinstance(endpoint_url, str) or not endpoint_url.strip():
        raise ValueError("endpoint_url must be a non-empty HTTP(S) URL")
    if any(ord(char) < 32 or ord(char) == 127 for char in endpoint_url):
        raise ValueError("endpoint_url contains control characters")
    try:
        parsed = urlsplit(endpoint_url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("endpoint_url must be a valid HTTP(S) URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("endpoint_url must be an absolute HTTP(S) URL without userinfo or fragment")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("endpoint_url has an invalid port")
    return parsed


def _validate_header(name, value):
    if not isinstance(name, str) or not name or any(
        char not in "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for char in name
    ):
        raise ValueError("header names must be valid HTTP tokens")
    if not isinstance(value, str) or any(char in value for char in "\r\n") or any(
        (ord(char) < 32 and char != "\t") or ord(char) == 127 for char in value
    ):
        raise ValueError("header values must not contain control characters")
    try:
        value.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise ValueError("header values must be encodable as Latin-1") from exc


def _valid_positive(name, value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be finite and positive")


def _valid_nonnegative(name, value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{name} must be finite and non-negative")


@dataclass(frozen=True)
class EgressPolicy:
    """Explicitly approved HTTP origins and destination address ranges."""

    allowed_origins: frozenset[str]
    allowed_networks: tuple[str, ...] = ()

    def __post_init__(self):
        origins = frozenset(self.allowed_origins)
        for origin in origins:
            parsed = _check_endpoint(origin)
            if parsed.path not in {"", "/"} or parsed.query:
                raise ValueError("allowed origins must contain only scheme, host, and optional port")
        for network in self.allowed_networks:
            ipaddress.ip_network(network, strict=False)
        object.__setattr__(self, "allowed_origins", origins)


def _origin(parsed):
    host = parsed.hostname.lower().rstrip(".")
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    default_port = 443 if parsed.scheme == "https" else 80
    return f"{parsed.scheme}://{host}" + (f":{port}" if port != default_port else "")


def _approved_addresses(host, port, policy):
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            }
        except OSError as exc:
            raise ForwardingError("Forwarding endpoint DNS lookup failed", transient=True) from exc
    if not addresses:
        raise ForwardingError("Forwarding endpoint has no usable addresses", transient=True)
    approved_networks = tuple(ipaddress.ip_network(net, strict=False) for net in policy.allowed_networks)
    for address in addresses:
        mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
        if address.is_link_local or (mapped is not None and mapped.is_link_local) or str(address) in {
            "169.254.169.254", "169.254.170.2", "100.100.100.200", "fd00:ec2::254"
        } or (mapped is not None and str(mapped) in {"169.254.169.254", "169.254.170.2", "100.100.100.200"}):
            raise ForwardingError("Forwarding endpoint resolved to a blocked address")
        policy_address = mapped or address
        if policy_address.is_private or policy_address.is_loopback or policy_address.is_reserved or not policy_address.is_global:
            if not any(policy_address.version == net.version and policy_address in net for net in approved_networks):
                raise ForwardingError("Forwarding endpoint resolved to an unapproved network")
    return addresses


def _tls_context(config):
    if not any((config.tls_ca_bundle, config.tls_client_certificate, config.tls_client_key)):
        return ssl.create_default_context()
    context = ssl.create_default_context(cafile=config.tls_ca_bundle)
    if bool(config.tls_client_certificate) != bool(config.tls_client_key):
        raise ValueError("client certificate and key must be configured together")
    if config.tls_client_certificate:
        context.load_cert_chain(config.tls_client_certificate, config.tls_client_key)
    return context


def _safe_request(endpoint_url, method, body, headers, timeout, policy, tls_context):
    parsed = _check_endpoint(endpoint_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if policy is None or _origin(parsed) not in policy.allowed_origins:
        raise ForwardingError("Forwarding endpoint origin is not approved")
    addresses = _approved_addresses(parsed.hostname, port, policy)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    host_header = parsed.hostname
    if ":" in host_header:
        host_header = f"[{host_header}]"
    if parsed.port:
        host_header += f":{parsed.port}"
    merged_headers = dict(headers)
    merged_headers.setdefault("Host", host_header)
    last_error = None
    for address in addresses:
        sock = socket.socket(address.version == 6 and socket.AF_INET6 or socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((str(address), port))
            if parsed.scheme == "https":
                sock = tls_context.wrap_socket(sock, server_hostname=parsed.hostname)
            connection = (
                http.client.HTTPSConnection(parsed.hostname, port, timeout=timeout, context=tls_context)
                if parsed.scheme == "https"
                else http.client.HTTPConnection(parsed.hostname, port, timeout=timeout)
            )
            connection.sock = sock
            connection.request(method, path, body=body, headers=merged_headers)
            response = connection.getresponse()
            status = response.status
            response_headers = {key.lower(): value for key, value in response.getheaders()}
            response.close()
            connection.close()
            return status, response_headers
        except ssl.SSLError:
            sock.close()
            raise
        except (OSError, http.client.HTTPException) as exc:
            sock.close()
            last_error = exc
    raise last_error or OSError("connection failed")


@dataclass(frozen=True)
class HttpForwarderConfig:
    """Transport configuration; legacy endpoint and bearer callers remain supported."""

    endpoint_url: str
    bearer_token: str | None = field(default=None, repr=False)
    timeout_seconds: float = 5.0
    max_attempts: int = 3
    backoff_seconds: float = 0.25
    max_backoff_seconds: float = 2.0
    max_retry_after_seconds: float = 30.0
    method: str = "POST"
    headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    auth_header: str | None = field(default=None, repr=False)
    idempotency_header: str | None = "Idempotency-Key"
    egress_policy: EgressPolicy | None = field(default=None, repr=False, compare=False)
    tls_ca_bundle: str | None = field(default=None, repr=False, compare=False)
    tls_client_certificate: str | None = field(default=None, repr=False, compare=False)
    tls_client_key: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        _check_endpoint(self.endpoint_url)
        _valid_positive("timeout_seconds", self.timeout_seconds)
        _valid_nonnegative("max_retry_after_seconds", self.max_retry_after_seconds)
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        for name, value in (("backoff_seconds", self.backoff_seconds), ("max_backoff_seconds", self.max_backoff_seconds)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.method not in {"POST", "PUT", "PATCH"}:
            raise ValueError("method must be POST, PUT, or PATCH")
        seen = set()
        for name, value in self.headers:
            _validate_header(name, value)
            lower_name = name.lower()
            if lower_name in seen:
                raise ValueError("custom headers must have unique names")
            if lower_name in {"content-type", "host", "content-length", "transfer-encoding", "connection", "idempotency-key", "authorization"}:
                raise ValueError(f"{name} is controlled by the HTTP transport")
            seen.add(lower_name)
        if self.bearer_token is not None and not isinstance(self.bearer_token, str):
            raise ValueError("bearer_token must be a string or None")
        if self.bearer_token is not None and any(char in self.bearer_token for char in "\r\n"):
            raise ValueError("bearer_token must not contain CR or LF")
        if self.auth_header is not None:
            _validate_header("Authorization", self.auth_header)
        if self.idempotency_header is not None:
            _validate_header(self.idempotency_header, "event-id")
            if self.idempotency_header.lower() in {"content-type", "host", "content-length", "transfer-encoding", "connection", "authorization"}:
                raise ValueError("idempotency_header conflicts with a transport header")
            if any(name.lower() == self.idempotency_header.lower() for name, _ in self.headers):
                raise ValueError("idempotency_header conflicts with a custom header")


@dataclass(frozen=True)
class ForwardingReceipt:
    event_id: str
    status_code: int
    attempts: int


def _retry_after(value):
    if value is None:
        return None
    try:
        text = value.strip()
        if text.isascii() and text.isdigit():
            return float(int(text))
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            return None
        return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


class HttpForwarder:
    """Serialize each source once and deliver identical bytes on bounded retries."""

    def __init__(self, config, *, opener=None, sleeper=time.sleep):
        if not isinstance(config, HttpForwarderConfig):
            raise TypeError("config must be an HttpForwarderConfig")
        self.config = config
        self._opener = opener
        self._sleeper = sleeper
        self._tls_context = _tls_context(config)

    def forward(self, record):
        """Send a completed-source JSON record to the approved receiver."""
        if not isinstance(record, dict):
            raise TypeError("record must be a mapping")
        source_id = record.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("record.source_id must be a non-empty string")
        body = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        headers.update(dict(self.config.headers))
        if self.config.idempotency_header:
            _validate_header(self.config.idempotency_header, source_id)
            headers[self.config.idempotency_header] = source_id
        auth_header = self.config.auth_header
        if self.config.bearer_token:
            auth_header = f"Bearer {self.config.bearer_token}"
        if auth_header:
            headers["Authorization"] = auth_header
        request = Request(self.config.endpoint_url, data=body, headers=headers, method=self.config.method)
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                if self._opener is not None:
                    with self._opener(request, timeout=self.config.timeout_seconds) as response:
                        status = response.getcode()
                        response_headers = {key.lower(): value for key, value in response.headers.items()}
                elif self.config.egress_policy is not None:
                    status, response_headers = _safe_request(
                        self.config.endpoint_url, self.config.method, body, headers,
                        self.config.timeout_seconds, self.config.egress_policy, self._tls_context,
                    )
                else:
                    # Legacy direct callers keep the original urllib transport behavior.
                    with urlopen(request, timeout=self.config.timeout_seconds) as response:
                        status = response.getcode()
                        response_headers = {key.lower(): value for key, value in response.headers.items()}
                if 200 <= status < 300:
                    return ForwardingReceipt(source_id, status, attempt)
                if 300 <= status < 400:
                    error = ForwardingError(
                        f"Forwarding endpoint returned HTTP {status} (redirects disabled)",
                        status_code=status, attempts=attempt,
                    )
                    raise error
                transient = status == 429 or 500 <= status <= 599
                error = ForwardingError(
                    f"Forwarding endpoint returned HTTP {status}", status_code=status,
                    attempts=attempt, transient=transient,
                )
                if not transient:
                    raise error
            except HTTPError as exc:
                status = exc.code
                transient = status == 429 or 500 <= status <= 599
                error = ForwardingError(f"Forwarding endpoint returned HTTP {status}", status_code=status, attempts=attempt, transient=transient)
                response_headers = {key.lower(): value for key, value in exc.headers.items()}
                exc.close()
                if not transient:
                    raise error from exc
            except (URLError, TimeoutError, OSError, ssl.SSLError, http.client.HTTPException, ForwardingError) as exc:
                if isinstance(exc, ForwardingError):
                    if not exc.transient:
                        raise
                    error = exc
                elif isinstance(exc, ssl.SSLError):
                    raise ForwardingError("Forwarding TLS validation failed", attempts=attempt) from exc
                else:
                    error = ForwardingError(f"Forwarding request failed ({type(exc).__name__})", attempts=attempt, transient=True)
                response_headers = {}
            if attempt == self.config.max_attempts:
                raise ForwardingError(
                    f"Forwarding failed after {attempt} attempts: {error}",
                    status_code=error.status_code, attempts=attempt, transient=True,
                ) from error
            requested_delay = _retry_after(response_headers.get("retry-after"))
            if requested_delay is not None and requested_delay > self.config.max_retry_after_seconds:
                raise ForwardingError(
                    "Forwarding deferred because Retry-After exceeds the configured limit",
                    status_code=error.status_code, attempts=attempt, transient=True,
                ) from error
            delay = requested_delay if requested_delay is not None else min(
                self.config.backoff_seconds * (2 ** (attempt - 1)), self.config.max_backoff_seconds
            )
            self._sleeper(delay)
