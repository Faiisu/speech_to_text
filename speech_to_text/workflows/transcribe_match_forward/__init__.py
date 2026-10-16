"""Compose transcription, keyword matching, and workflow-owned outputs."""

from .api import (
    PipelineResult,
    SessionWorkflow,
    forward_session,
    list_available_microphones,
    start_microphone_and_forward,
    transcribe_clip_and_forward,
)
from .http_output import (
    EgressPolicy,
    ForwardingError,
    ForwardingReceipt,
    HttpForwarder,
    HttpForwarderConfig,
)
from .destinations import (
    AuthConfig,
    DestinationConfig,
    DestinationConfigurationError,
    DestinationRegistry,
    DestinationUnavailableError,
    build_forwarder,
    destination_summary,
    validate_destination_config,
)
from .mqtt_output import MqttForwarder, MqttForwarderConfig, MqttForwardingReceipt
from .service import (
    TranscriptionService,
    WorkflowConfigurationError,
    WorkflowInputError,
    WorkflowServiceError,
    WorkflowUnavailableError,
    normalize_profile_decoding_options,
    prepare_rtsp_source,
    prepare_rtsp_configuration,
)

__all__ = [
    "AuthConfig",
    "DestinationConfig",
    "DestinationConfigurationError",
    "DestinationRegistry",
    "DestinationUnavailableError",
    "EgressPolicy",
    "ForwardingError",
    "ForwardingReceipt",
    "HttpForwarder",
    "HttpForwarderConfig",
    "MqttForwarder",
    "MqttForwarderConfig",
    "MqttForwardingReceipt",
    "PipelineResult",
    "SessionWorkflow",
    "TranscriptionService",
    "WorkflowConfigurationError",
    "WorkflowInputError",
    "WorkflowServiceError",
    "WorkflowUnavailableError",
    "build_forwarder",
    "destination_summary",
    "forward_session",
    "list_available_microphones",
    "normalize_profile_decoding_options",
    "prepare_rtsp_source",
    "prepare_rtsp_configuration",
    "start_microphone_and_forward",
    "transcribe_clip_and_forward",
    "validate_destination_config",
]
