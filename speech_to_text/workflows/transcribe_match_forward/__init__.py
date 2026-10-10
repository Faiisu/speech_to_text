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
    ForwardingError,
    ForwardingReceipt,
    HttpForwarder,
    HttpForwarderConfig,
)
from .service import (
    TranscriptionService,
    WorkflowConfigurationError,
    WorkflowInputError,
    WorkflowServiceError,
    WorkflowUnavailableError,
)

__all__ = [
    "ForwardingError",
    "ForwardingReceipt",
    "HttpForwarder",
    "HttpForwarderConfig",
    "PipelineResult",
    "SessionWorkflow",
    "TranscriptionService",
    "WorkflowConfigurationError",
    "WorkflowInputError",
    "WorkflowServiceError",
    "WorkflowUnavailableError",
    "forward_session",
    "list_available_microphones",
    "start_microphone_and_forward",
    "transcribe_clip_and_forward",
]
