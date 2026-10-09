"""Callable public interface for speech-to-text model deployment.

Model configuration defaults to ``turbo`` + ``openvino-gpu`` + source
checkpoint precision. Flow configuration defaults to Thai, 5 second chunks,
and an RMS silence threshold of 0.05. Use ``load_model`` once per owning
process and reuse that handle for clips or microphone flows.
"""

from .catalog import list_models
from .errors import (
    AudioInputError,
    ChunkInferenceWarning,
    ConfigurationError,
    ModelClosedError,
    ModelLoadError,
)
from .model import ModelHandle, load_model, transcribe_clip
from .process_topology import (
    ProcessFlowGroup,
    ProcessSession,
    start_multiprocess_microphone_flows,
)
from .session import TranscriptionSession


def list_available_models(*, catalog_path=None):
    """Rescan built-in, configured, converted, and local-cache model sources."""
    return list_models(catalog_path=catalog_path)


def start_microphone_flow(
    device=None, model_handle=None, flow_config=None, *, audio_source_factory=None
):
    """Start one microphone source owned by the current process.

    ``device`` is omitted for the host OS default or is an exact stable device
    name. A supplied audio source factory replaces only the hardware boundary;
    buffering, inference routing, queueing, and events stay feature-owned.
    """
    if not isinstance(model_handle, ModelHandle):
        raise TypeError("model_handle must be a ModelHandle returned by load_model")
    model_handle._check()
    model_handle._ensure_worker()
    session = TranscriptionSession(
        device,
        model_handle,
        {} if flow_config is None else flow_config,
        audio_source_factory,
    )
    model_handle._sessions.add(session)
    return session


__all__ = [
    "AudioInputError",
    "ChunkInferenceWarning",
    "ConfigurationError",
    "ModelClosedError",
    "ModelHandle",
    "ModelLoadError",
    "ProcessFlowGroup",
    "ProcessSession",
    "TranscriptionSession",
    "list_available_models",
    "load_model",
    "start_microphone_flow",
    "start_multiprocess_microphone_flows",
    "transcribe_clip",
]
