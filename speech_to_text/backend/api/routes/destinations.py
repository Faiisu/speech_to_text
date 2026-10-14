"""Operator-managed outbound HTTP destination configuration."""

import ipaddress
import os
import sqlite3
import uuid
from dataclasses import fields
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, status

from speech_to_text.backend.api.schemas.transcription import DestinationDefinition
from speech_to_text.workflows.transcribe_match_forward.destinations import (
    DestinationConfig,
    DestinationConfigurationError,
    DestinationRegistry,
    DestinationUnavailableError,
    destination_summary,
)
from speech_to_text.workflows.transcribe_match_forward.http_output import ForwardingError

router = APIRouter(prefix="/destinations")


def _csv(name, default=()):
    value = os.environ.get(name)
    if value is None:
        return tuple(default)
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _local_operator(request, *, allow_missing_origin=False):
    """Require a real loopback peer and same-machine browser origin/host."""
    client = request.client
    try:
        peer = ipaddress.ip_address(client.host) if client else None
        if peer is None or not peer.is_loopback:
            return False
    except ValueError:
        return False
    try:
        host = urlsplit("//" + request.headers.get("host", ""))
        api_port = int(os.environ.get("SPEECH_TO_TEXT_API_PORT", "8000"))
        raw_origin = request.headers.get("origin")
        if raw_origin is None and allow_missing_origin:
            allowed_host_ports = {api_port, 5173}
            return bool(
                host.hostname
                and host.hostname.lower().rstrip(".")
                in {"localhost", "127.0.0.1", "::1"}
                and host.port in allowed_host_ports
            )
        origin = urlsplit(raw_origin or "")
        if origin.scheme != "http" or not origin.hostname:
            return False
        origin_host = origin.hostname.lower().rstrip(".")
        if origin_host not in {"localhost", "127.0.0.1", "::1"}:
            return False
        if origin.path or origin.query or origin.fragment:
            return False
        allowed_origins = set(
            _csv(
                "SPEECH_TO_TEXT_OPERATOR_ORIGINS",
                ("http://127.0.0.1:5173", "http://localhost:5173"),
            )
        )
        if request.headers["origin"].rstrip("/") not in allowed_origins:
            return False
        if not host.hostname or host.hostname.lower().rstrip(".") not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            return False
        return host.port in {api_port, origin.port}
    except (KeyError, ValueError):
        return False


def _require_operator(request, *, allow_missing_origin=False):
    if not _local_operator(request, allow_missing_origin=allow_missing_origin):
        raise HTTPException(status_code=403, detail="local operator access required")


def _safe_store(callback):
    try:
        return callback()
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="destination store unavailable") from exc


def _config_body(body: DestinationDefinition, destination_id):
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="name must not be empty")
    values = body.model_dump(exclude={"name"})
    values["destination_id"] = destination_id
    try:
        return DestinationConfig.from_mapping(values)
    except DestinationConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _stored_configs(store, replacement=None):
    entries = store.list_destinations()
    configs = []
    for entry in entries:
        if replacement and entry["destination_id"] == replacement.destination_id:
            continue
        configs.append(
            DestinationConfig.from_mapping(
                {**entry["config"], "destination_id": entry["destination_id"]}
            )
        )
    if replacement is not None:
        configs.append(replacement)
    return configs


def _validate_registry(runtime, store, candidate=None):
    try:
        if candidate is not None:
            secret_refs = {
                candidate.auth.secret_ref,
                candidate.auth.username_secret_ref,
                candidate.auth.password_secret_ref,
            } - {None}
            resource_refs = {
                candidate.tls_ca_bundle_ref,
                candidate.tls_client_certificate_ref,
                candidate.tls_client_key_ref,
            } - {None}
            if not secret_refs.issubset(
                set(_csv("SPEECH_TO_TEXT_DESTINATION_SECRET_REFS"))
            ):
                raise DestinationConfigurationError(
                    "secret references must be approved by deployment configuration"
                )
            if not resource_refs.issubset(
                set(_csv("SPEECH_TO_TEXT_DESTINATION_RESOURCE_REFS"))
            ):
                raise DestinationConfigurationError(
                    "TLS resource references must be approved by deployment configuration"
                )
        return DestinationRegistry(
            _stored_configs(store, candidate),
            allowed_origins=runtime._csv_environment(
                "SPEECH_TO_TEXT_DESTINATION_ALLOWED_ORIGINS"
            ),
            allowed_networks=runtime._csv_environment(
                "SPEECH_TO_TEXT_DESTINATION_ALLOWED_NETWORKS"
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _persist_config(config):
    data = {
        field.name: getattr(config, field.name)
        for field in fields(config)
        if field.name != "destination_id"
    }
    data["auth"] = {
        field.name: getattr(config.auth, field.name) for field in fields(config.auth)
    }
    data["headers"] = dict(config.headers)
    return data


def _detail(entry):
    config = entry["config"]
    auth = config.get("auth") or {"type": "none"}
    return {
        "destination_id": entry["destination_id"],
        "name": entry["name"],
        "endpoint_origin": destination_summary(
            DestinationConfig.from_mapping(
                {**config, "destination_id": entry["destination_id"]}
            )
        )["endpoint_origin"],
        "method": config.get("method", "POST"),
        "auth_type": auth.get("type", "none"),
        "payload_format": config.get("payload_format", "match_results_v1"),
        "config": config,
        "created_at": entry["created_at"],
        "updated_at": entry["updated_at"],
    }


def _summary(entry):
    detail = _detail(entry)
    detail.pop("config")
    return detail


@router.get("")
def list_destinations(request: Request):
    runtime = request.app.state.backend_runtime
    entries = _safe_store(runtime.profile_store.list_destinations)
    return {"destinations": [_summary(entry) for entry in entries]}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_destination(request: Request, body: DestinationDefinition):
    _require_operator(request)
    runtime = request.app.state.backend_runtime
    destination_id = uuid.uuid4().hex
    config = _config_body(body, destination_id)
    _validate_registry(runtime, runtime.profile_store, config)
    entry = _safe_store(
        lambda: runtime.profile_store.save_destination(
            destination_id, body.name.strip(), _persist_config(config)
        )
    )
    runtime.reload_destination_registry()
    return _detail(entry)


@router.get("/{destination_id}")
def get_destination(destination_id: str, request: Request):
    _require_operator(request, allow_missing_origin=True)
    entry = _safe_store(
        lambda: request.app.state.backend_runtime.profile_store.get_destination(
            destination_id
        )
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="destination not found")
    return _detail(entry)


@router.put("/{destination_id}")
def replace_destination(
    destination_id: str, request: Request, body: DestinationDefinition
):
    _require_operator(request)
    runtime = request.app.state.backend_runtime
    store = runtime.profile_store
    if _safe_store(lambda: store.get_destination(destination_id)) is None:
        raise HTTPException(status_code=404, detail="destination not found")
    config = _config_body(body, destination_id)
    _validate_registry(runtime, store, config)
    entry = _safe_store(
        lambda: store.save_destination(
            destination_id, body.name.strip(), _persist_config(config)
        )
    )
    runtime.reload_destination_registry()
    return _detail(entry)


@router.delete("/{destination_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_destination(destination_id: str, request: Request):
    _require_operator(request)
    runtime = request.app.state.backend_runtime
    store = runtime.profile_store
    if _safe_store(lambda: store.get_destination(destination_id)) is None:
        raise HTTPException(status_code=404, detail="destination not found")
    if _safe_store(lambda: store.destination_is_referenced(destination_id)):
        raise HTTPException(status_code=409, detail="destination is selected by a profile")
    deleted = _safe_store(lambda: store.delete_destination(destination_id))
    if not deleted:
        if _safe_store(lambda: store.destination_is_referenced(destination_id)):
            raise HTTPException(
                status_code=409, detail="destination is selected by a profile"
            )
        raise HTTPException(status_code=404, detail="destination not found")
    runtime.reload_destination_registry()


@router.post("/{destination_id}/test")
def test_destination(destination_id: str, request: Request):
    _require_operator(request)
    runtime = request.app.state.backend_runtime
    if runtime.destination_registry.get_config(destination_id) is None:
        raise HTTPException(status_code=404, detail="destination not found")
    try:
        forwarder = runtime.destination_registry.build_forwarder(destination_id)
    except DestinationConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DestinationUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    event_id = "test-" + uuid.uuid4().hex
    payload = {
        "event_type": "transcription.match_results",
        "event_id": event_id,
        "source_id": event_id,
        "language": "th",
        "transcript": "Synthetic destination test",
        "matches": [{"keyword": "test", "count": 1}],
        "status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    try:
        receipt = runtime.executor.submit(forwarder.forward, payload).result()
    except ForwardingError as exc:
        return {
            "status": "failed",
            "event_id": event_id,
            "attempts": exc.attempts,
            "http_status": exc.status_code,
            "error_code": getattr(exc, "code", "delivery_failed"),
        }
    return {
        "status": "delivered",
        "event_id": receipt.event_id,
        "attempts": receipt.attempts,
        "http_status": receipt.status_code,
    }
