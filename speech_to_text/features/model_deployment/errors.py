"""Stable errors exposed by the model deployment feature."""


class ConfigurationError(ValueError):
    """A model or flow configuration contains an unsupported value."""


class ModelLoadError(RuntimeError):
    """A selected model runtime could not be initialized."""


class VADUnavailableError(RuntimeError):
    """Enabled Silero VAD could not load or process an audio chunk."""


class ModelClosedError(RuntimeError):
    """An operation was attempted with a closed model handle."""


class AudioInputError(ValueError):
    """An audio input cannot be decoded or normalized."""


class AudioInputDeviceError(AudioInputError):
    """A requested microphone device is missing, ambiguous, or invalid."""


class AudioCodecUnavailableError(RuntimeError):
    """Optional compressed-audio decoding support is unavailable."""


class ChunkInferenceWarning(UserWarning):
    """One audio chunk failed while later chunks remain processable."""
