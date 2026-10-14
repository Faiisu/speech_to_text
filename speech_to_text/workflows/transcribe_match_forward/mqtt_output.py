"""Terminal JSON publishing over MQTT 3.1.1 with bounded broker acknowledgements."""

from dataclasses import dataclass, field
import json
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from .http_output import EgressPolicy, ForwardingError, _approved_addresses, _valid_nonnegative, _valid_positive


def parse_broker_url(url):
    if not isinstance(url, str) or any(ord(char) <= 32 or ord(char) == 127 for char in url):
        raise ValueError("broker URL must be an absolute mqtt:// or mqtts:// URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("broker URL has an invalid host or port") from exc
    if (parsed.scheme not in {"mqtt", "mqtts"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or port is not None and not 1 <= port <= 65535):
        raise ValueError("broker URL must contain only mqtt/mqtts, host, and optional port")
    return parsed


@dataclass(frozen=True)
class MqttForwarderConfig:
    endpoint_url: str
    topic: str
    qos: int = 1
    retain: bool = False
    timeout_seconds: float = 5.0
    max_attempts: int = 3
    backoff_seconds: float = 0.25
    max_backoff_seconds: float = 2.0
    allowed_networks: tuple[str, ...] = ()
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)
    tls_context: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        parsed = parse_broker_url(self.endpoint_url)
        try:
            valid_topic = isinstance(self.topic, str) and bool(self.topic) and len(self.topic.encode("utf-8")) <= 65535
        except UnicodeEncodeError:
            valid_topic = False
        if not valid_topic or any(char in self.topic for char in ("\0", "+", "#")):
            raise ValueError("MQTT topic must be non-empty UTF-8 without NUL or subscription wildcards")
        if isinstance(self.qos, bool) or not isinstance(self.qos, int) or self.qos not in {0, 1, 2}:
            raise ValueError("MQTT QoS must be 0, 1, or 2")
        if not isinstance(self.retain, bool):
            raise ValueError("MQTT retain must be a boolean")
        _valid_positive("timeout_seconds", self.timeout_seconds)
        _valid_nonnegative("backoff_seconds", self.backoff_seconds)
        _valid_nonnegative("max_backoff_seconds", self.max_backoff_seconds)
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if bool(self.username is not None) != bool(self.password is not None):
            raise ValueError("MQTT credentials must be configured together")
        if self.username is not None and parsed.scheme != "mqtts":
            raise ValueError("MQTT credentials require mqtts://")
        if self.username is not None:
            try:
                valid_credentials = all(isinstance(value, str) and 0 < len(value.encode("utf-8")) <= 65535
                                        for value in (self.username, self.password))
            except UnicodeEncodeError:
                valid_credentials = False
            if not valid_credentials or "\0" in self.username:
                raise ValueError("MQTT credentials contain invalid values")
        EgressPolicy(frozenset(), self.allowed_networks)


@dataclass(frozen=True)
class MqttForwardingReceipt:
    event_id: str
    attempts: int
    topic: str
    qos: int
    retain: bool
    transport: str = "mqtt"
    status_code: int | None = None


def _make_client(addresses, port, timeout):
    import paho.mqtt.client as mqtt

    class ApprovedClient(mqtt.Client):
        # Paho 2.x socket hook pins approved IPs, preserving the original TLS SNI hostname.
        def _create_socket_connection(self):
            deadline = time.monotonic() + timeout
            last_error = None
            for address in sorted(addresses, key=str):
                sock = socket.socket(socket.AF_INET6 if address.version == 6 else socket.AF_INET, socket.SOCK_STREAM)
                try:
                    sock.settimeout(max(0.001, deadline - time.monotonic()))
                    sock.connect((str(address), port))
                    return sock
                except OSError as exc:
                    sock.close()
                    last_error = exc
                    if time.monotonic() >= deadline:
                        break
            raise last_error or OSError("MQTT broker connection failed")

    client = ApprovedClient(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                            protocol=mqtt.MQTTv311, reconnect_on_failure=False)
    client.connect_timeout = timeout
    return client


class MqttForwarder:
    """Implement the existing forward(record) workflow interface for an MQTT broker."""

    transport = "mqtt"

    def __init__(self, config, *, client_factory=None, sleeper=time.sleep):
        if not isinstance(config, MqttForwarderConfig):
            raise TypeError("config must be a MqttForwarderConfig")
        if client_factory is None:
            try:
                import paho.mqtt.client  # noqa: F401
            except ImportError as exc:
                raise ImportError("MQTT output requires the mqtt extra") from exc
        self.config = config
        self._client_factory = client_factory or _make_client
        self._sleeper = sleeper
        self._tls_context = config.tls_context or ssl.create_default_context()

    def forward(self, record):
        if not isinstance(record, dict) or not isinstance(record.get("source_id"), str) or not record["source_id"]:
            raise ValueError("record.source_id must be a non-empty string")
        body = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        parsed = parse_broker_url(self.config.endpoint_url)
        port = parsed.port or (8883 if parsed.scheme == "mqtts" else 1883)
        policy = EgressPolicy(frozenset(), self.config.allowed_networks)
        for attempt in range(1, self.config.max_attempts + 1):
            client = None
            try:
                addresses = _approved_addresses(parsed.hostname, port, policy)
                client = self._client_factory(addresses, port, self.config.timeout_seconds)
                if self.config.username is not None:
                    client.username_pw_set(self.config.username, self.config.password)
                if parsed.scheme == "mqtts":
                    client.tls_set_context(self._tls_context)
                connected = threading.Event()
                connection_reason = []

                def on_connect(_client, _userdata, _flags, reason, _properties):
                    connection_reason.append(getattr(reason, "value", reason))
                    connected.set()

                client.on_connect = on_connect
                deadline = time.monotonic() + self.config.timeout_seconds
                client.connect(parsed.hostname, port, keepalive=30)
                client.loop_start()
                if not connected.wait(max(0, deadline - time.monotonic())):
                    raise TimeoutError("MQTT connection acknowledgement timed out")
                if connection_reason[0] != 0:
                    raise ForwardingError("MQTT broker rejected the connection", attempts=attempt,
                                          transient=connection_reason[0] in {136, 137})
                message = client.publish(self.config.topic, body, qos=self.config.qos, retain=self.config.retain)
                if message.rc != 0:
                    raise OSError("MQTT publish could not be queued")
                message.wait_for_publish(timeout=max(0, deadline - time.monotonic()))
                if not message.is_published():
                    raise TimeoutError("MQTT publish acknowledgement timed out")
                return MqttForwardingReceipt(record["source_id"], attempt, self.config.topic,
                                             self.config.qos, self.config.retain)
            except ssl.SSLError as exc:
                raise ForwardingError("MQTT TLS validation failed", attempts=attempt) from exc
            except (ForwardingError, OSError, RuntimeError, ValueError) as exc:
                if isinstance(exc, ForwardingError) and not exc.transient:
                    raise
                error = ForwardingError("MQTT delivery failed", attempts=attempt, transient=True)
                if attempt == self.config.max_attempts:
                    raise error from exc
            finally:
                if client is not None:
                    try:
                        client.disconnect()
                    finally:
                        client.loop_stop()
            self._sleeper(min(self.config.backoff_seconds * 2 ** (attempt - 1), self.config.max_backoff_seconds))
