from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import shutil
import socket
import ssl
import subprocess
import threading
import time

import pytest

from speech_to_text.workflows.transcribe_match_forward import (
    AuthConfig,
    DestinationConfig,
    DestinationConfigurationError,
    DestinationRegistry,
    DestinationUnavailableError,
    EgressPolicy,
    ForwardingError,
    HttpForwarder,
    HttpForwarderConfig,
    destination_summary,
)


class Receiver(BaseHTTPRequestHandler):
    records = []
    statuses = []
    retry_after = "0"
    redirect_location = None
    delay_seconds = 0

    def _handle(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.records.append((self.command, dict(self.headers), body))
        status = self.statuses.pop(0) if self.statuses else 204
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        self.send_response(status)
        if status in {429, 503}:
            self.send_header("Retry-After", self.retry_after)
        if status == 302 and self.redirect_location:
            self.send_header("Location", self.redirect_location)
        self.end_headers()

    do_POST = _handle
    do_PUT = _handle
    do_PATCH = _handle

    def log_message(self, *_args):
        pass


@pytest.fixture
def receiver():
    Receiver.records = []
    Receiver.statuses = []
    Receiver.retry_after = "0"
    Receiver.redirect_location = None
    Receiver.delay_seconds = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/transcripts"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def mutual_tls_receiver(tmp_path):
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("openssl is required to generate ephemeral test certificates")
    paths = {name: tmp_path / name for name in (
        "ca.key", "ca.crt", "server.key", "server.csr", "server.crt", "server.ext",
        "client.key", "client.csr", "client.crt", "client.ext",
    )}

    def run(*args):
        subprocess.run([openssl, *map(str, args)], check=True, capture_output=True)

    run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", paths["ca.key"],
        "-out", paths["ca.crt"], "-days", "1", "-subj", "/CN=Temporary Test CA",
        "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    for name, usage in (("server", "serverAuth"), ("client", "clientAuth")):
        key, csr, cert = (paths[f"{name}.{suffix}"] for suffix in ("key", "csr", "crt"))
        run("req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
            "-out", csr, "-subj", f"/CN={name}.test")
        ext = paths[f"{name}.ext"]
        ext.write_text(f"subjectAltName=IP:127.0.0.1\nextendedKeyUsage={usage}\n", encoding="ascii")
        run("x509", "-req", "-in", csr, "-CA", paths["ca.crt"], "-CAkey", paths["ca.key"],
            "-CAcreateserial", "-out", cert, "-days", "1", "-extfile", ext)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(paths["server.crt"], paths["server.key"])
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cafile=paths["ca.crt"])
    Receiver.records = []
    Receiver.statuses = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            f"https://127.0.0.1:{server.server_port}/transcripts",
            paths,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def local_policy(endpoint):
    origin = endpoint.split("/transcripts", 1)[0]
    return EgressPolicy(frozenset({origin}), ("127.0.0.0/8",))


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
def test_forwarder_delivers_utf8_json_and_configured_headers_to_local_receiver(receiver, method):
    forwarder = HttpForwarder(HttpForwarderConfig(
        receiver,
        method=method,
        headers=(("X-Station", "north"),),
        auth_header="Bearer configured-token",
        egress_policy=local_policy(receiver),
    ))

    receipt = forwarder.forward({"source_id": "run-7", "transcript": "สวัสดี"})

    command, headers, body = Receiver.records[0]
    assert command == method
    assert headers["Content-Type"] == "application/json"
    assert headers["X-Station"] == "north"
    assert headers["Authorization"] == "Bearer configured-token"
    assert headers["Idempotency-Key"] == "run-7"
    assert json.loads(body.decode("utf-8")) == {"source_id": "run-7", "transcript": "สวัสดี"}
    assert receipt.event_id == "run-7"
    assert receipt.status_code == 204
    assert receipt.attempts == 1


def test_retry_uses_identical_body_and_honors_bounded_retry_after(receiver):
    Receiver.statuses = [429, 202]
    delays = []
    forwarder = HttpForwarder(HttpForwarderConfig(
        receiver, max_attempts=2, max_retry_after_seconds=1,
        egress_policy=local_policy(receiver),
    ), sleeper=delays.append)

    receipt = forwarder.forward({"source_id": "stable-run", "transcript": "ฝน"})

    assert receipt.status_code == 202
    assert receipt.attempts == 2
    assert Receiver.records[0][2] == Receiver.records[1][2]
    assert delays == [0]


def test_live_chunk_idempotency_keys_are_distinct_and_stable_across_retries(receiver):
    Receiver.statuses = [503, 204, 204]
    forwarder = HttpForwarder(HttpForwarderConfig(
        receiver, max_attempts=2, egress_policy=local_policy(receiver),
    ), sleeper=lambda _delay: None)

    first = {
        "event_type": "transcription.match_results",
        "source_id": "live-run",
        "event_id": "live-run:chunk:1",
        "transcript": "one",
    }
    second = {**first, "event_id": "live-run:chunk:2", "transcript": "two"}
    first_receipt = forwarder.forward(first)
    second_receipt = forwarder.forward(second)

    assert [entry[1]["Idempotency-Key"] for entry in Receiver.records] == [
        "live-run:chunk:1", "live-run:chunk:1", "live-run:chunk:2",
    ]
    assert Receiver.records[0][2] == Receiver.records[1][2]
    assert first_receipt.event_id == "live-run:chunk:1"
    assert second_receipt.event_id == "live-run:chunk:2"


def test_redirect_is_not_followed(receiver):
    Receiver.statuses = [302, 204]
    Receiver.redirect_location = receiver
    forwarder = HttpForwarder(HttpForwarderConfig(
        receiver, max_attempts=3, egress_policy=local_policy(receiver)
    ))

    with pytest.raises(ForwardingError, match="redirects disabled") as caught:
        forwarder.forward({"source_id": "redirect"})

    assert caught.value.status_code == 302
    assert len(Receiver.records) == 1


def test_non_finite_values_are_rejected_before_network_io(receiver):
    forwarder = HttpForwarder(HttpForwarderConfig(
        receiver, egress_policy=local_policy(receiver)
    ))

    with pytest.raises(ValueError, match="Out of range float"):
        forwarder.forward({"source_id": "bad-json", "value": float("nan")})

    assert Receiver.records == []


def test_permanent_http_failure_is_not_retried(receiver):
    Receiver.statuses = [422, 204]
    forwarder = HttpForwarder(HttpForwarderConfig(
        receiver, max_attempts=3, egress_policy=local_policy(receiver)
    ), sleeper=lambda _delay: pytest.fail("permanent failure must not sleep"))

    with pytest.raises(ForwardingError) as caught:
        forwarder.forward({"source_id": "bad-request"})

    assert caught.value.status_code == 422
    assert caught.value.attempts == 1
    assert caught.value.transient is False
    assert len(Receiver.records) == 1


def test_request_timeout_uses_remaining_attempts(receiver):
    Receiver.delay_seconds = 0.2
    forwarder = HttpForwarder(HttpForwarderConfig(
        receiver, timeout_seconds=0.05, max_attempts=2,
        egress_policy=local_policy(receiver),
    ), sleeper=lambda _delay: None)

    with pytest.raises(ForwardingError, match="failed after 2 attempts") as caught:
        forwarder.forward({"source_id": "timeout"})

    assert caught.value.attempts == 2


def test_retry_after_above_limit_returns_without_retrying(receiver):
    Receiver.statuses = [503, 204]
    Receiver.retry_after = "31"
    forwarder = HttpForwarder(HttpForwarderConfig(
        receiver, max_attempts=3, max_retry_after_seconds=1,
        egress_policy=local_policy(receiver),
    ), sleeper=lambda _delay: pytest.fail("oversized Retry-After must not retry early"))

    with pytest.raises(ForwardingError, match="Retry-After exceeds") as caught:
        forwarder.forward({"source_id": "deferred"})

    assert caught.value.attempts == 1
    assert len(Receiver.records) == 1


def test_destination_registry_hides_refs_and_resolves_only_when_building():
    config = DestinationConfig(
        destination_id="ops",
        endpoint_url="https://receiver.example.com/transcripts",
        auth=AuthConfig(type="api_key_header", secret_ref="RECEIVER_KEY", header_name="X-API-Key"),
    )
    resolved = []
    registry = DestinationRegistry(
        [config], allowed_origins=(), allowed_networks=("127.0.0.0/8",),
        secret_resolver=lambda ref: resolved.append(ref) or "secret-value",
    )

    assert resolved == []
    assert registry.get_config("ops") is config
    assert registry.summary("ops") == {
        "destination_id": "ops",
        "endpoint_origin": "https://receiver.example.com",
        "method": "POST",
        "auth_type": "api_key_header",
        "payload_format": "match_results_v1",
    }
    assert "secret" not in repr(registry.summary("ops"))
    forwarder = registry.build_forwarder("ops")
    assert resolved == ["RECEIVER_KEY"]
    assert "secret-value" not in repr(forwarder.config)
    assert dict(forwarder.config.headers)["X-API-Key"] == "secret-value"


@pytest.mark.parametrize(
    ("auth", "secrets", "expected_name", "expected_value"),
    [
        (AuthConfig(type="bearer", secret_ref="TOKEN"), {"TOKEN": "bearer-secret"}, "Authorization", "Bearer bearer-secret"),
        (AuthConfig(type="basic", username_secret_ref="USER", password_secret_ref="PASS"), {"USER": "service", "PASS": "p@ss"}, "Authorization", "Basic c2VydmljZTpwQHNz"),
        (AuthConfig(type="api_key_header", secret_ref="KEY", header_name="X-API-Key"), {"KEY": "api-key-secret"}, "X-API-Key", "api-key-secret"),
    ],
)
def test_secret_auth_and_mutual_tls_work_with_private_ca(
    mutual_tls_receiver, monkeypatch, auth, secrets, expected_name, expected_value
):
    endpoint, paths = mutual_tls_receiver
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    config = DestinationConfig(
        destination_id="secured",
        endpoint_url=endpoint,
        auth=auth,
        tls_ca_bundle_ref="CA_CERT",
        tls_client_certificate_ref="CLIENT_CERT",
        tls_client_key_ref="CLIENT_KEY",
    )
    registry = DestinationRegistry(
        [config],
        allowed_origins=(),
        allowed_networks=("127.0.0.0/8",),
        secret_resolver=lambda ref: secrets[ref],
        resource_resolver=lambda ref: {
            "CA_CERT": str(paths["ca.crt"]),
            "CLIENT_CERT": str(paths["client.crt"]),
            "CLIENT_KEY": str(paths["client.key"]),
        }[ref],
    )

    receipt = registry.build_forwarder("secured").forward({"source_id": "mtls"})

    assert receipt.status_code == 204
    assert Receiver.records[-1][1][expected_name] == expected_value


@pytest.mark.parametrize("failure", ["hostname", "trust"])
def test_tls_hostname_or_trust_failure_is_permanent(mutual_tls_receiver, failure):
    endpoint, paths = mutual_tls_receiver
    port = int(endpoint.split(":")[2].split("/")[0])
    target = f"https://localhost:{port}/transcripts" if failure == "hostname" else endpoint
    ca_bundle = paths["ca.crt"] if failure == "hostname" else paths["client.crt"]
    origin = target.rsplit("/transcripts", 1)[0]
    forwarder = HttpForwarder(HttpForwarderConfig(
        target,
        max_attempts=3,
        egress_policy=EgressPolicy(frozenset({origin}), ("127.0.0.0/8", "::1/128")),
        tls_ca_bundle=str(ca_bundle),
        tls_client_certificate=str(paths["client.crt"]),
        tls_client_key=str(paths["client.key"]),
    ), sleeper=lambda _delay: pytest.fail("TLS validation failures must not retry"))

    with pytest.raises(ForwardingError, match="TLS validation failed") as caught:
        forwarder.forward({"source_id": "wrong-host"})

    assert caught.value.attempts == 1


def test_ipv4_mapped_ipv6_metadata_address_is_blocked():
    from speech_to_text.workflows.transcribe_match_forward.http_output import _approved_addresses

    policy = EgressPolicy(frozenset(), ("::/0",))
    with pytest.raises(ForwardingError, match="blocked address"):
        _approved_addresses("::ffff:169.254.169.254", 443, policy)


def test_missing_or_unapproved_secret_is_reported_without_exposing_value(monkeypatch):
    config = DestinationConfig(
        destination_id="secured",
        endpoint_url="https://receiver.example.com/transcripts",
        auth=AuthConfig(type="bearer", secret_ref="PRIVATE_TOKEN"),
    )
    monkeypatch.setenv("PRIVATE_TOKEN", "do-not-leak")
    monkeypatch.delenv("SPEECH_TO_TEXT_DESTINATION_SECRET_REFS", raising=False)
    registry = DestinationRegistry([config], allowed_origins=())

    with pytest.raises(DestinationUnavailableError) as caught:
        registry.build_forwarder("secured")

    assert "do-not-leak" not in str(caught.value)
    assert "not deployment-approved" in str(caught.value)


@pytest.mark.parametrize("invalid_value", ["bad\x7fheader", "สวัสดี🙂"])
def test_invalid_custom_header_values_are_rejected_without_echoing_value(invalid_value):
    with pytest.raises(DestinationConfigurationError) as caught:
        DestinationConfig(
            destination_id="bad-header",
            endpoint_url="https://receiver.example.com/transcripts",
            headers={"X-Station": invalid_value},
        )

    assert invalid_value not in str(caught.value)


def test_invalid_secret_header_value_is_sanitized():
    config = DestinationConfig(
        destination_id="secured",
        endpoint_url="https://receiver.example.com/transcripts",
        auth=AuthConfig(type="bearer", secret_ref="TOKEN"),
    )
    registry = DestinationRegistry(
        [config], allowed_origins=(),
        secret_resolver=lambda _ref: "private-secret🙂",
    )

    with pytest.raises(DestinationUnavailableError) as caught:
        registry.build_forwarder("secured")

    assert "private-secret" not in str(caught.value)


def test_invalid_ca_bundle_bytes_are_reported_as_unavailable(tmp_path):
    ca_bundle = tmp_path / "bad-ca.pem"
    ca_bundle.write_bytes(b"not a PEM certificate")
    config = DestinationConfig(
        destination_id="secured",
        endpoint_url="https://receiver.example.com/transcripts",
        tls_ca_bundle_ref="CA_BUNDLE",
    )
    registry = DestinationRegistry(
        [config], allowed_origins=(),
        resource_resolver=lambda _ref: str(ca_bundle),
    )

    with pytest.raises(DestinationUnavailableError, match="TLS configuration is unavailable"):
        registry.build_forwarder("secured")


def test_destination_validation_requires_tls_for_credentials_and_rejects_header_injection():
    with pytest.raises(DestinationConfigurationError, match="require HTTPS"):
        DestinationConfig(
            destination_id="cleartext", endpoint_url="http://receiver.example/hook",
            auth=AuthConfig(type="bearer", secret_ref="TOKEN"),
        )
    with pytest.raises(DestinationConfigurationError):
        DestinationConfig(
            destination_id="bad-header", endpoint_url="https://receiver.example/hook",
            headers={"X-Bad": "one\r\ntwo"},
        )


def test_destination_config_rejects_private_network_without_explicit_cidr(receiver):
    config = DestinationConfig(destination_id="local", endpoint_url=receiver)
    with pytest.raises(ForwardingError, match="unapproved network"):
        HttpForwarder(HttpForwarderConfig(
            receiver,
            egress_policy=EgressPolicy(frozenset({receiver.split("/transcripts", 1)[0]})),
        )).forward({"source_id": "denied"})


def test_mixed_dns_result_with_blocked_address_is_rejected(receiver, monkeypatch):
    from speech_to_text.workflows.transcribe_match_forward import http_output

    port = int(receiver.split(":")[2].split("/")[0])
    monkeypatch.setattr(http_output.socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port)),
    ])
    hostname_url = receiver.replace("127.0.0.1", "receiver.test")
    policy = EgressPolicy(frozenset({hostname_url.split("/transcripts", 1)[0]}), ("127.0.0.0/8",))
    forwarder = HttpForwarder(HttpForwarderConfig(hostname_url, egress_policy=policy))

    with pytest.raises(ForwardingError, match="blocked address"):
        forwarder.forward({"source_id": "mixed-dns"})
