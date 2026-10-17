"""Callable public interface for speech-to-text model deployment.

Model configuration defaults to ``turbo`` + ``openvino-gpu`` + source
checkpoint precision. Flow configuration defaults to its configured language, 30 second chunks,
and an RMS silence threshold of 0.05. Use ``load_model`` once per owning
process and reuse that handle for clips or microphone flows.
"""

from .audio import convert_audio_to_wav, validate_wav
from .catalog import list_models
from .config import flow_config
from .capture import list_input_devices
from .errors import (
    AudioCodecUnavailableError,
    AudioInputError,
    ChunkInferenceWarning,
    ConfigurationError,
    ModelClosedError,
    ModelLoadError,
)
from .model import ModelHandle, load_model, transcribe_clip
from .model_management import (
    ModelPreparationError,
    ModelPreparationUnavailableError,
    delete_local_model,
    inspect_huggingface_model,
    plan_local_model_deletion,
    prepare_huggingface_model,
)
from .replay_source import PacedWavSource
from .recording import AudioRecording, start_audio_recording
from .rtsp import (
    RTSPAudioSourceFactory,
    RTSPCaptureUnavailableError,
    RTSPSourceConfiguration,
    create_rtsp_audio_source_factory,
    create_rtsp_secret_source_factory,
    normalize_rtsp_source,
)
from .process_topology import (
    ProcessFlowGroup,
    ProcessSession,
    start_multiprocess_microphone_flows,
)
from .session import TranscriptionSession


def list_available_models(*, catalog_path=None):
    """Rescan built-in, configured, converted, and local-cache model sources."""
    return list_models(catalog_path=catalog_path)


def list_microphone_devices():
    """List host input devices without exposing transient device indexes."""
    return list_input_devices()


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


def normalize_decoding_options(value=None):
    """Validate decoding settings and return a complete independent snapshot."""
    return flow_config({} if value is None else {"decoding_options": value})[
        "decoding_options"
    ]


__all__ = [
    "AudioInputError",
    "AudioCodecUnavailableError",
    "AudioRecording",
    "ChunkInferenceWarning",
    "ConfigurationError",
    "ModelClosedError",
    "ModelHandle",
    "ModelLoadError",
    "ModelPreparationError",
    "ModelPreparationUnavailableError",
    "delete_local_model",
    "PacedWavSource",
    "ProcessFlowGroup",
    "ProcessSession",
    "RTSPAudioSourceFactory",
    "RTSPCaptureUnavailableError",
    "RTSPSourceConfiguration",
    "TranscriptionSession",
    "list_available_models",
    "inspect_huggingface_model",
    "plan_local_model_deletion",
    "normalize_decoding_options",
    "prepare_huggingface_model",
    "list_microphone_devices",
    "load_model",
    "start_microphone_flow",
    "start_audio_recording",
    "start_multiprocess_microphone_flows",
    "transcribe_clip",
    "validate_wav",
    "convert_audio_to_wav",
    "create_rtsp_audio_source_factory",
    "create_rtsp_secret_source_factory",
    "normalize_rtsp_source",
]
