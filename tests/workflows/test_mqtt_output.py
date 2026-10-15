"""MQTT boundary failures and opt-in real broker delivery."""

import json
import os
import queue
import ssl
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid

import pytest

from speech_to_text.workflows.transcribe_match_forward import (
    AuthConfig, DestinationConfig, DestinationConfigurationError, DestinationRegistry,
    ForwardingError, MqttForwarder, MqttForwarderConfig,
)


class FakeClient:
    def __init__(self, *, reason=0, error=None, acknowledged=True):
        self.reason, self.error, self.acknowledged = reason, error, acknowledged
        self.records = []
        self.closed = False
        self.loop_stopped = False

    def connect(self, host, port, keepalive):
        if self.error:
            raise self.error
        self.on_connect(self, None, None, self.reason, None)

    def loop_start(self):
        pass

    def publish(self, topic, body, qos, retain):
        self.records.append((topic, body, qos, retain))
        return SimpleNamespace(rc=0, wait_for_publish=lambda timeout: None,
                               is_published=lambda: self.acknowledged)

    def disconnect(self):
        self.closed = True

    def loop_stop(self):
        self.loop_stopped = True


@pytest.mark.parametrize("options", [
    {"mqtt_topic": ""}, {"mqtt_topic": "speech/#"}, {"mqtt_topic": "speech/+"},
    {"mqtt_qos": True}, {"mqtt_qos": 3}, {"mqtt_retain": "false"},
    {"endpoint_url": "mqtt://user:password@localhost"},
    {"endpoint_url": "mqtt://localhost/path"}, {"headers": {"X-Key": "value"}},
    {"auth": AuthConfig(type="bearer", secret_ref="TOKEN")},
    {"auth": AuthConfig(type="username_password", username_secret_ref="USER", password_secret_ref="PASS")},
])
def test_mqtt_destination_rejects_invalid_settings(options):
    values = {"destination_id": "mqtt", "endpoint_url": "mqtt://127.0.0.1", "protocol": "mqtt", "mqtt_topic": "speech/results"}
    values.update(options)
    with pytest.raises(DestinationConfigurationError):
        DestinationConfig(**values)


def test_retry_preserves_json_identity_and_always_closes_connections():
    clients = [FakeClient(error=ConnectionRefusedError()), FakeClient()]
    remaining = iter(clients)
    sleeps = []
    forwarder = MqttForwarder(MqttForwarderConfig("mqtt://127.0.0.1", "speech/ผลลัพธ์",
                              allowed_networks=("127.0.0.1/32",)),
                              client_factory=lambda *_args: next(remaining), sleeper=sleeps.append)
    record = {"source_id": "stable", "event_id": "stable", "transcript": "สวัสดี"}
    receipt = forwarder.forward(record)
    assert receipt.attempts == 2 and receipt.status_code is None
    assert receipt.topic == "speech/ผลลัพธ์" and receipt.qos == 1 and not receipt.retain
    assert json.loads(clients[1].records[0][1]) == record
    assert sleeps == [.25]
    assert all(client.closed and client.loop_stopped for client in clients)


def test_clear_retained_publishes_empty_retained_packet_with_configured_qos():
    client = FakeClient()
    forwarder = MqttForwarder(MqttForwarderConfig(
        "mqtt://127.0.0.1", "speech/results", qos=2, retain=False,
        allowed_networks=("127.0.0.1/32",),
    ), client_factory=lambda *_args: client)

    attempts = forwarder.clear_retained()

    assert attempts == 1
    assert client.records == [("speech/results", b"", 2, True)]
    assert client.closed and client.loop_stopped


def test_clear_retained_retries_publish_failure_and_reports_attempts():
    clients = []

    def factory(*_args):
        client = FakeClient(acknowledged=bool(clients))
        clients.append(client)
        return client

    forwarder = MqttForwarder(MqttForwarderConfig(
        "mqtt://127.0.0.1", "speech/results", max_attempts=2,
        allowed_networks=("127.0.0.1/32",),
    ), client_factory=factory, sleeper=lambda _: None)

    assert forwarder.clear_retained() == 2
    assert all(client.records == [("speech/results", b"", 1, True)] for client in clients)
    assert all(client.closed and client.loop_stopped for client in clients)


@pytest.mark.parametrize("client", [FakeClient(reason=135), FakeClient(error=ssl.SSLCertVerificationError("private detail"))])
def test_permanent_connection_rejections_are_not_retried(client):
    forwarder = MqttForwarder(MqttForwarderConfig("mqtt://127.0.0.1", "speech/results",
                              allowed_networks=("127.0.0.1/32",)),
                              client_factory=lambda *_args: client,
                              sleeper=lambda _delay: pytest.fail("permanent failure must not retry"))
    with pytest.raises(ForwardingError) as caught:
        forwarder.forward({"source_id": "id"})
    assert caught.value.attempts == 1 and not caught.value.transient
    assert "private detail" not in str(caught.value)
    assert client.closed and client.loop_stopped


def test_missing_publish_acknowledgement_exhausts_without_claiming_delivery():
    clients = []
    def factory(*_args):
        client = FakeClient(acknowledged=False)
        clients.append(client)
        return client
    forwarder = MqttForwarder(MqttForwarderConfig("mqtt://127.0.0.1", "speech/results", max_attempts=2,
                              allowed_networks=("127.0.0.1/32",)), client_factory=factory, sleeper=lambda _: None)
    with pytest.raises(ForwardingError) as caught:
        forwarder.forward({"source_id": "stable", "transcript": "สวัสดี"})
    assert caught.value.attempts == 2 and caught.value.transient
    assert clients[0].records[0][1] == clients[1].records[0][1]
    assert all(client.loop_stopped for client in clients)


def test_private_broker_requires_approved_network():
    forwarder = MqttForwarder(MqttForwarderConfig("mqtt://127.0.0.1", "speech/results"),
                              client_factory=lambda *_args: pytest.fail("must reject before connecting"))
    with pytest.raises(ForwardingError, match="unapproved network"):
        forwarder.forward({"source_id": "id"})


@pytest.fixture
def broker_subscription():
    import paho.mqtt.client as mqtt
    url = os.environ.get("MQTT_TEST_BROKER_URL")
    if not url:
        pytest.skip("set MQTT_TEST_BROKER_URL to run real broker integration")
    parsed = urlsplit(url)
    topic = "speech/tests/" + uuid.uuid4().hex
    received = queue.Queue()
    subscribed = threading.Event()
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = lambda client, *_args: client.subscribe(topic, qos=2)
    client.on_subscribe = lambda *_args: subscribed.set()
    client.on_message = lambda _client, _data, message: received.put(message)
    client.connect(parsed.hostname, parsed.port or 1883)
    client.loop_start()
    assert subscribed.wait(5), "test subscriber did not connect"
    try:
        yield url, topic, received
    finally:
        client.disconnect()
        client.loop_stop()


@pytest.mark.parametrize("qos", [0, 1, 2])
def test_real_broker_receives_utf8_json_with_selected_qos(broker_subscription, qos):
    url, topic, received = broker_subscription
    config = DestinationConfig("mqtt", url, protocol="mqtt", mqtt_topic=topic, mqtt_qos=qos)
    registry = DestinationRegistry([config], allowed_origins=(), allowed_networks=("127.0.0.1/32",))
    record = {"source_id": "run", "event_id": "run:chunk:1", "transcript": "สวัสดีครับ"}
    receipt = registry.build_forwarder("mqtt").forward(record)
    message = received.get(timeout=5)
    assert json.loads(message.payload) == record
    assert receipt.qos == message.qos == qos
    assert receipt.event_id == "run:chunk:1" and receipt.attempts == 1
    assert not message.retain
