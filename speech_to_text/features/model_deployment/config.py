"""Configuration validation for one model handle and its independent flows."""

from __future__ import annotations

import math
import re
from copy import deepcopy

from .errors import ConfigurationError

MODEL_DEFAULTS = {
    "model": "turbo",
    "runtime": "openvino-gpu",
    "precision": "source",
    "queue_capacity": 6,
    "enqueue_timeout_seconds": 1.0,
}
MODEL_KEYS = frozenset(MODEL_DEFAULTS)
RUNTIME_OPTIONS = ("openvino-gpu", "openvino-cpu", "ctranslate2")
FLOW_DEFAULTS = {
    "language": "th",
    "chunk_seconds": 30.0,
    "silence_threshold": 0.05,
    "decoding_options": {
        "beam_size": 1,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        "no_repeat_ngram_size": 0,
        "repetition_penalty": 1.0,
    },
}
FLOW_KEYS = frozenset(
    {"source_id", "language", "chunk_seconds", "silence_threshold", "decoding_options"}
)
DECODING_DEFAULTS = FLOW_DEFAULTS["decoding_options"]


def model_config(value):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ConfigurationError("model_config must be a mapping")
    unknown = set(value) - MODEL_KEYS
    if unknown:
        raise ConfigurationError(
            f"Unknown model configuration keys: {', '.join(sorted(unknown))}"
        )
    result = {**MODEL_DEFAULTS, **deepcopy(value)}
    if not isinstance(result["model"], str) or not result["model"].strip():
        raise ConfigurationError("model must be a non-empty model key")
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", result["model"])
        or ".." in result["model"]
    ):
        raise ConfigurationError(
            "model must be a safe catalog key without path separators or '..'"
        )
    if result["runtime"] not in RUNTIME_OPTIONS:
        raise ConfigurationError(
            f"Unsupported runtime {result['runtime']!r}; choose one of {', '.join(RUNTIME_OPTIONS)}"
        )
    precision = result["precision"]
    supported = (
        {"source", "bf16", "int8", "int4"}
        if result["runtime"].startswith("openvino")
        else {"int8", "float32"}
    )
    if precision not in supported:
        raise ConfigurationError(
            f"Precision {precision!r} is not supported by {result['runtime']}; choose from {', '.join(sorted(supported))}"
        )
    capacity = result["queue_capacity"]
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
        raise ConfigurationError("queue_capacity must be a positive integer")
    timeout = result["enqueue_timeout_seconds"]
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ConfigurationError(
            "enqueue_timeout_seconds must be a finite positive number"
        )
    result["enqueue_timeout_seconds"] = float(timeout)
    return result


def flow_config(value):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ConfigurationError("flow_config must be a mapping")
    unknown = set(value) - FLOW_KEYS
    if unknown:
        raise ConfigurationError(
            f"Unknown flow configuration keys: {', '.join(sorted(unknown))}"
        )
    result = {
        "language": FLOW_DEFAULTS["language"],
        "chunk_seconds": FLOW_DEFAULTS["chunk_seconds"],
        "silence_threshold": FLOW_DEFAULTS["silence_threshold"],
        "decoding_options": deepcopy(DECODING_DEFAULTS),
        **deepcopy(value),
    }
    chunk = result["chunk_seconds"]
    if (
        isinstance(chunk, bool)
        or not isinstance(chunk, (int, float))
        or not math.isfinite(chunk)
        or not 0 < chunk <= 30
    ):
        raise ConfigurationError("chunk_seconds must be finite and in (0, 30]")
    threshold = result["silence_threshold"]
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(threshold)
        or not 0 <= threshold < 1
    ):
        raise ConfigurationError("silence_threshold must be in [0, 1)")
    language = result["language"]
    if not isinstance(language, str) or not language.strip():
        raise ConfigurationError("language must be a non-empty language code or 'auto'")
    options = result["decoding_options"]
    if not isinstance(options, dict) or set(options) - set(DECODING_DEFAULTS):
        unknown_options = (
            set(options) - set(DECODING_DEFAULTS)
            if isinstance(options, dict)
            else set()
        )
        raise ConfigurationError(
            f"Unsupported decoding options: {', '.join(sorted(unknown_options)) or 'expected a mapping'}"
        )
    options = {**DECODING_DEFAULTS, **options}
    beam = options["beam_size"]
    if isinstance(beam, bool) or not isinstance(beam, int) or not 1 <= beam <= 10:
        raise ConfigurationError("beam_size must be an integer in [1, 10]")
    temperature = options["temperature"]
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not math.isfinite(temperature)
        or not 0 <= temperature <= 1
    ):
        raise ConfigurationError("temperature must be finite and in [0, 1]")
    if not isinstance(options["condition_on_previous_text"], bool):
        raise ConfigurationError("condition_on_previous_text must be a boolean")
    ngram = options["no_repeat_ngram_size"]
    if isinstance(ngram, bool) or not isinstance(ngram, int) or not 0 <= ngram <= 20:
        raise ConfigurationError("no_repeat_ngram_size must be an integer in [0, 20]")
    penalty = options["repetition_penalty"]
    if (
        isinstance(penalty, bool)
        or not isinstance(penalty, (int, float))
        or not math.isfinite(penalty)
        or not 1 <= penalty <= 2
    ):
        raise ConfigurationError("repetition_penalty must be finite and in [1, 2]")
    result["decoding_options"] = options
    if "source_id" in result and (
        not isinstance(result["source_id"], str) or not result["source_id"].strip()
    ):
        raise ConfigurationError("source_id must be a non-empty string")
    result["chunk_seconds"] = float(chunk)
    result["silence_threshold"] = float(threshold)
    result["language"] = language.strip()
    return result
