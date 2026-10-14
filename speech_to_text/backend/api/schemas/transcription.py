"""HTTP request schemas for transcription workflows."""

from typing import Literal

from pydantic import BaseModel, Field, StrictStr, validator


class MicrophoneTranscriptionRequest(BaseModel):
    class Config:
        extra = "forbid"

    keywords: list[StrictStr]
    device: StrictStr | None = None
    execution_mode: Literal["shared", "per_workflow_process"] = "shared"
    destination_id: StrictStr | None = None


class MicrophoneProfileDefinition(BaseModel):
    class Config:
        extra = "forbid"

    name: StrictStr
    device: StrictStr | None = None
    execution_mode: Literal["shared", "per_workflow_process"] = "shared"
    keywords: list[StrictStr]
    silence_threshold: float = Field(
        default=0.00, ge=0.0, lt=1.0, allow_inf_nan=False
    )
    model: StrictStr | None = None
    runtime: StrictStr | None = None
    destination_id: StrictStr | None = None

    @validator("silence_threshold", pre=True)
    def reject_boolean_silence_threshold(cls, value):
        if isinstance(value, bool):
            raise ValueError("silence_threshold must be a number")
        return value


class DestinationAuthDefinition(BaseModel):
    class Config:
        extra = "forbid"

    type: Literal["none", "bearer", "basic", "api_key_header", "username_password"] = "none"
    secret_ref: StrictStr | None = None
    header_name: StrictStr | None = None
    username_secret_ref: StrictStr | None = None
    password_secret_ref: StrictStr | None = None


class DestinationDefinition(BaseModel):
    class Config:
        extra = "forbid"

    name: StrictStr
    endpoint_url: StrictStr
    method: Literal["POST", "PUT", "PATCH"] = "POST"
    headers: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    auth: DestinationAuthDefinition = Field(default_factory=DestinationAuthDefinition)
    timeout_seconds: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    max_attempts: int = Field(default=3, ge=1)
    backoff_seconds: float = Field(default=0.25, ge=0, allow_inf_nan=False)
    max_backoff_seconds: float = Field(default=2.0, ge=0, allow_inf_nan=False)
    max_retry_after_seconds: float = Field(default=30.0, ge=0, allow_inf_nan=False)
    follow_redirects: bool = False
    idempotency_header: StrictStr | None = "Idempotency-Key"
    payload_format: Literal["match_results_v1"] = "match_results_v1"
    tls_ca_bundle_ref: StrictStr | None = None
    tls_client_certificate_ref: StrictStr | None = None
    tls_client_key_ref: StrictStr | None = None
    protocol: Literal["http", "mqtt"] = "http"
    mqtt_topic: StrictStr | None = None
    mqtt_qos: int = Field(default=1, ge=0, le=2, strict=True)
    mqtt_retain: bool = Field(default=False, strict=True)

    @validator(
        "timeout_seconds",
        "backoff_seconds",
        "max_backoff_seconds",
        "max_retry_after_seconds",
        pre=True,
    )
    def reject_boolean_timeouts(cls, value):
        if isinstance(value, bool):
            raise ValueError("timeout settings must be numbers")
        return value

    @validator("max_attempts", pre=True)
    def reject_boolean_attempts(cls, value):
        if isinstance(value, bool):
            raise ValueError("max_attempts must be an integer")
        return value
