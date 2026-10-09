"""Stable errors exposed by the model deployment feature."""


class ConfigurationError(ValueError):
    """A model or flow configuration contains an unsupported value."""


class ModelLoadError(RuntimeError):
    """A selected model runtime could not be initialized."""


class ModelClosedError(RuntimeError):
    """An operation was attempted with a closed model handle."""


class AudioInputError(ValueError):
    """An audio input cannot be decoded or normalized."""


class ChunkInferenceWarning(UserWarning):
    """One audio chunk failed while later chunks remain processable."""
