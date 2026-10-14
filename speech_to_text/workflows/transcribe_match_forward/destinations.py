"""Validated deployment-managed HTTP destination configuration."""

from dataclasses import dataclass, field
import base64
import os
import re
from types import MappingProxyType
from typing import Mapping

from .http_output import EgressPolicy, HttpForwarder, HttpForwarderConfig, _check_endpoint, _validate_header


class DestinationConfigurationError(ValueError):
    """A destination entry is malformed or conflicts with deployment policy."""


class DestinationUnavailableError(RuntimeError):
    """An approved secret or TLS resource is unavailable at runtime."""


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class AuthConfig:
    type: str = "none"
    secret_ref: str | None = None
    header_name: str | None = None
    username_secret_ref: str | None = None
    password_secret_ref: str | None = None

    def __post_init__(self):
        if self.type not in {"none", "bearer", "basic", "api_key_header"}:
            raise DestinationConfigurationError("auth.type must be none, bearer, basic, or api_key_header")
        if self.type in {"bearer", "api_key_header"} and not _valid_ref(self.secret_ref):
            raise DestinationConfigurationError("auth.secret_ref must name an approved environment variable")
        if self.type == "basic" and not all(
            _valid_ref(ref) for ref in (self.username_secret_ref, self.password_secret_ref)
        ):
            raise DestinationConfigurationError("basic auth requires username and password secret refs")
        if self.type == "api_key_header":
            try:
                _validate_header(self.header_name, "secret")
            except ValueError as exc:
                raise DestinationConfigurationError("auth.header_name must be a valid HTTP header name") from exc
            if self.header_name.lower() in {"content-type", "host", "content-length", "transfer-encoding", "connection", "idempotency-key", "authorization"}:
                raise DestinationConfigurationError("auth.header_name conflicts with a transport header")
        if self.type == "none" and any((self.secret_ref, self.header_name, self.username_secret_ref, self.password_secret_ref)):
            raise DestinationConfigurationError("auth settings must be empty when auth.type is none")
        if self.type == "bearer" and any((self.header_name, self.username_secret_ref, self.password_secret_ref)):
            raise DestinationConfigurationError("bearer auth accepts only secret_ref")
        if self.type == "api_key_header" and any((self.username_secret_ref, self.password_secret_ref)):
            raise DestinationConfigurationError("API key auth accepts only secret_ref and header_name")
        if self.type == "basic" and any((self.secret_ref, self.header_name)):
            raise DestinationConfigurationError("basic auth accepts only username and password secret refs")


def _valid_ref(ref):
    return isinstance(ref, str) and bool(_ENV_NAME.fullmatch(ref))


@dataclass(frozen=True)
class DestinationConfig:
    destination_id: str
    endpoint_url: str
    method: str = "POST"
    headers: Mapping[str, str] = field(default_factory=dict)
    auth: AuthConfig = field(default_factory=AuthConfig)
    timeout_seconds: float = 5.0
    max_attempts: int = 3
    backoff_seconds: float = 0.25
    max_backoff_seconds: float = 2.0
    max_retry_after_seconds: float = 30.0
    follow_redirects: bool = False
    idempotency_header: str | None = "Idempotency-Key"
    payload_format: str = "match_results_v1"
    tls_ca_bundle_ref: str | None = None
    tls_client_certificate_ref: str | None = None
    tls_client_key_ref: str | None = None

    def __post_init__(self):
        if not isinstance(self.destination_id, str) or not self.destination_id or len(self.destination_id) > 128:
            raise DestinationConfigurationError("destination_id must be a non-empty string of at most 128 characters")
        try:
            parsed = _check_endpoint(self.endpoint_url)
        except ValueError as exc:
            raise DestinationConfigurationError(str(exc)) from exc
        if self.method not in {"POST", "PUT", "PATCH"}:
            raise DestinationConfigurationError("method must be POST, PUT, or PATCH")
        if self.follow_redirects is not False:
            raise DestinationConfigurationError("follow_redirects must be false")
        if self.payload_format != "match_results_v1":
            raise DestinationConfigurationError("payload_format must be match_results_v1")
        if not isinstance(self.auth, AuthConfig):
            raise DestinationConfigurationError("auth must be an AuthConfig")
        if parsed.scheme != "https" and self.auth.type != "none":
            raise DestinationConfigurationError("credential-bearing destinations require HTTPS")
        if not isinstance(self.headers, Mapping):
            raise DestinationConfigurationError("headers must be an object of string values")
        copied = {}
        reserved = {"content-type", "host", "content-length", "transfer-encoding", "connection", "authorization"}
        for name, value in self.headers.items():
            try:
                _validate_header(name, value)
            except ValueError as exc:
                raise DestinationConfigurationError(str(exc)) from exc
            if name.lower() in reserved:
                raise DestinationConfigurationError(f"{name} is controlled by the HTTP transport or auth configuration")
            if any(existing.lower() == name.lower() for existing in copied):
                raise DestinationConfigurationError("custom header names must be unique")
            copied[name] = value
        object.__setattr__(self, "headers", MappingProxyType(copied))
        if self.auth.type == "api_key_header" and any(
            name.lower() == self.auth.header_name.lower() for name in copied
        ):
            raise DestinationConfigurationError("API key auth header conflicts with a custom header")
        if self.idempotency_header is not None:
            try:
                _validate_header(self.idempotency_header, "event-id")
            except ValueError as exc:
                raise DestinationConfigurationError("idempotency_header must be a valid header name or null") from exc
            if self.idempotency_header.lower() in {"content-type", "host", "content-length", "transfer-encoding", "connection", "authorization"}:
                raise DestinationConfigurationError("idempotency_header conflicts with a transport header")
            if any(name.lower() == self.idempotency_header.lower() for name in copied):
                raise DestinationConfigurationError("idempotency_header conflicts with a custom header")
            if self.auth.type == "api_key_header" and self.idempotency_header.lower() == self.auth.header_name.lower():
                raise DestinationConfigurationError("idempotency_header conflicts with the API key auth header")
        for ref in (self.tls_ca_bundle_ref, self.tls_client_certificate_ref, self.tls_client_key_ref):
            if ref is not None and not _valid_ref(ref):
                raise DestinationConfigurationError("TLS resource refs must name environment variables")
        if parsed.scheme != "https" and any((self.tls_ca_bundle_ref, self.tls_client_certificate_ref, self.tls_client_key_ref)):
            raise DestinationConfigurationError("TLS resources require an HTTPS endpoint")
        if bool(self.tls_client_certificate_ref) != bool(self.tls_client_key_ref):
            raise DestinationConfigurationError("client certificate and key refs must be configured together")
        try:
            HttpForwarderConfig(
                self.endpoint_url,
                timeout_seconds=self.timeout_seconds,
                max_attempts=self.max_attempts,
                backoff_seconds=self.backoff_seconds,
                max_backoff_seconds=self.max_backoff_seconds,
                max_retry_after_seconds=self.max_retry_after_seconds,
                method=self.method,
                headers=tuple(copied.items()),
                idempotency_header=self.idempotency_header,
            )
        except (TypeError, ValueError) as exc:
            raise DestinationConfigurationError(str(exc)) from exc

    @classmethod
    def from_mapping(cls, values):
        """Build from JSON-compatible destination fields without accepting secret values."""
        if not isinstance(values, Mapping):
            raise DestinationConfigurationError("destination must be an object")
        data = dict(values)
        allowed = {
            "destination_id", "endpoint_url", "method", "headers", "auth", "timeout_seconds",
            "max_attempts", "backoff_seconds", "max_backoff_seconds", "max_retry_after_seconds",
            "follow_redirects", "idempotency_header", "payload_format", "tls_ca_bundle_ref",
            "tls_client_certificate_ref", "tls_client_key_ref",
        }
        unexpected = set(data) - allowed
        if unexpected:
            raise DestinationConfigurationError("unsupported destination fields: " + ", ".join(sorted(unexpected)))
        auth = data.pop("auth", {})
        if not isinstance(auth, Mapping):
            raise DestinationConfigurationError("auth must be an object")
        auth_allowed = {"type", "secret_ref", "header_name", "username_secret_ref", "password_secret_ref"}
        if set(auth) - auth_allowed:
            raise DestinationConfigurationError("auth contains unsupported fields")
        data["auth"] = AuthConfig(**dict(auth))
        try:
            return cls(**data)
        except TypeError as exc:
            raise DestinationConfigurationError("destination fields are incomplete or invalid") from exc


def validate_destination_config(config):
    if not isinstance(config, DestinationConfig):
        raise TypeError("config must be a DestinationConfig")
    return config


def destination_summary(config):
    config = validate_destination_config(config)
    parsed = _check_endpoint(config.endpoint_url)
    return {
        "destination_id": config.destination_id,
        "endpoint_origin": f"{parsed.scheme}://{parsed.netloc}",
        "method": config.method,
        "auth_type": config.auth.type,
        "payload_format": config.payload_format,
    }


def _approved_env_set(env_name):
    return frozenset(item.strip() for item in os.environ.get(env_name, "").split(",") if item.strip())


def _default_secret_resolver(ref):
    if ref not in _approved_env_set("SPEECH_TO_TEXT_DESTINATION_SECRET_REFS"):
        raise DestinationUnavailableError("destination secret reference is not deployment-approved")
    value = os.environ.get(ref)
    if not value:
        raise DestinationUnavailableError("approved destination secret is unavailable")
    return value


def _default_resource_resolver(ref):
    if ref not in _approved_env_set("SPEECH_TO_TEXT_DESTINATION_RESOURCE_REFS"):
        raise DestinationUnavailableError("destination resource reference is not deployment-approved")
    value = os.environ.get(ref)
    if not value or not os.path.isabs(value) or not os.path.isfile(value):
        raise DestinationUnavailableError("approved destination TLS resource is unavailable")
    return value


def _resolve_secret(ref, resolver):
    try:
        value = resolver(ref)
    except DestinationUnavailableError:
        raise
    except Exception as exc:
        raise DestinationUnavailableError("approved destination secret is unavailable") from exc
    if not isinstance(value, str) or not value:
        raise DestinationUnavailableError("approved destination secret is unavailable")
    return value


def _resolve_resource(ref, resolver):
    if ref is None:
        return None
    try:
        value = resolver(ref)
    except DestinationUnavailableError:
        raise
    except Exception as exc:
        raise DestinationUnavailableError("approved destination TLS resource is unavailable") from exc
    if not isinstance(value, str) or not os.path.isabs(value) or not os.path.isfile(value):
        raise DestinationUnavailableError("approved destination TLS resource is unavailable")
    return value


def build_forwarder(config, *, allowed_origins, allowed_networks=(), secret_resolver=None, resource_resolver=None):
    """Resolve one approved config into a forwarder with a per-run secret/TLS snapshot."""
    config = validate_destination_config(config)
    origin = next((item for item in allowed_origins if _origin_text(item) == _origin_text(config.endpoint_url)), None)
    if origin is None:
        raise DestinationConfigurationError("destination origin is not deployment-approved")
    policy = EgressPolicy(frozenset({_origin_text(origin)}), tuple(allowed_networks))
    secret_resolver = secret_resolver or _default_secret_resolver
    resource_resolver = resource_resolver or _default_resource_resolver
    auth_header = None
    if config.auth.type == "bearer":
        auth_header = "Bearer " + _resolve_secret(config.auth.secret_ref, secret_resolver)
    elif config.auth.type == "basic":
        username = _resolve_secret(config.auth.username_secret_ref, secret_resolver)
        password = _resolve_secret(config.auth.password_secret_ref, secret_resolver)
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        auth_header = "Basic " + token
    elif config.auth.type == "api_key_header":
        auth_header = _resolve_secret(config.auth.secret_ref, secret_resolver)
    resources = [config.tls_ca_bundle_ref, config.tls_client_certificate_ref, config.tls_client_key_ref]
    resolved = [_resolve_resource(ref, resource_resolver) for ref in resources]
    headers = dict(config.headers)
    if config.auth.type == "api_key_header":
        headers[config.auth.header_name] = auth_header
        auth_header = None
    try:
        return HttpForwarder(HttpForwarderConfig(
            endpoint_url=config.endpoint_url,
            timeout_seconds=config.timeout_seconds,
            max_attempts=config.max_attempts,
            backoff_seconds=config.backoff_seconds,
            max_backoff_seconds=config.max_backoff_seconds,
            max_retry_after_seconds=config.max_retry_after_seconds,
            method=config.method,
            headers=tuple(headers.items()),
            auth_header=auth_header,
            idempotency_header=config.idempotency_header,
            egress_policy=policy,
            tls_ca_bundle=resolved[0],
            tls_client_certificate=resolved[1],
            tls_client_key=resolved[2],
        ))
    except (OSError, ValueError) as exc:
        raise DestinationUnavailableError("approved destination TLS configuration is unavailable") from exc


def _origin_text(url):
    parsed = _check_endpoint(url)
    host = parsed.hostname.lower().rstrip(".")
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    default_port = 443 if parsed.scheme == "https" else 80
    return f"{parsed.scheme}://{host}" + (f":{port}" if port != default_port else "")


class DestinationRegistry:
    """Deployment-owned registry with a fixed egress policy."""

    def __init__(self, configs, *, allowed_origins, allowed_networks=(), secret_resolver=None, resource_resolver=None):
        self._configs = {}
        for raw in configs:
            config = raw if isinstance(raw, DestinationConfig) else DestinationConfig.from_mapping(raw)
            if config.destination_id in self._configs:
                raise DestinationConfigurationError("destination_id values must be unique")
            self._configs[config.destination_id] = config
        self._allowed_origins = tuple(_origin_text(origin) for origin in allowed_origins)
        self._allowed_networks = tuple(allowed_networks)
        self._secret_resolver = secret_resolver
        self._resource_resolver = resource_resolver
        # Fail registry construction for malformed deployment egress settings.
        EgressPolicy(frozenset(self._allowed_origins), self._allowed_networks)
        for config in self._configs.values():
            # Registration is an explicit operator approval because destination writes are local-admin only.
            if _origin_text(config.endpoint_url) not in self._allowed_origins:
                self._allowed_origins += (_origin_text(config.endpoint_url),)

    def get_config(self, destination_id):
        return self._configs.get(destination_id)

    def summary(self, config_or_id):
        config = self.get_config(config_or_id) if isinstance(config_or_id, str) else config_or_id
        return destination_summary(config) if config is not None else None

    def build_forwarder(self, config_or_id):
        config = self.get_config(config_or_id) if isinstance(config_or_id, str) else config_or_id
        if config is None:
            raise DestinationConfigurationError("unknown destination_id")
        return build_forwarder(
            config,
            allowed_origins=self._allowed_origins,
            allowed_networks=self._allowed_networks,
            secret_resolver=self._secret_resolver,
            resource_resolver=self._resource_resolver,
        )
