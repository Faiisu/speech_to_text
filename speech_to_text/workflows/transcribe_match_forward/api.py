"""Public clip and session orchestration for completed transcripts."""

import queue
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from inspect import Parameter, signature

from speech_to_text.features.model_deployment import (
    list_microphone_devices,
    start_microphone_flow,
    transcribe_clip,
)
from speech_to_text.features.word_matching import (
    StreamingWordMatcher,
    WordMatchResult,
    WordMatchingConfig,
    WordMatchingError,
    match_keywords,
)


@dataclass(frozen=True)
class PipelineResult:
    transcript: str
    matches: tuple
    source_id: str
    forwarding_receipt: object | None
    forwarding_error: Exception | None = None
    latest_rtf: float | None = None


def _send(forwarder, record):
    method = getattr(forwarder, "forward", None)
    if callable(method):
        return method(record)
    if callable(forwarder):
        return forwarder(record)
    raise TypeError("forwarder must be callable or expose forward(record)")


def _match_rows(result: WordMatchResult):
    return [{"keyword": item.keyword, "count": item.count} for item in result.matches]


def _record(source_id, result, transcript, status, *, event_id=None, rtf=None, include_rtf=False):
    record = {
        "event_type": "transcription.match_results",
        "event_id": source_id if event_id is None else event_id,
        "source_id": source_id,
        "language": result.language,
        "transcript": transcript,
        "matches": _match_rows(result),
        "status": status,
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if include_rtf:
        record["rtf"] = rtf
    return record


def _validate_matching_config(config):
    if not isinstance(config, WordMatchingConfig):
        raise TypeError("matching_config must be a WordMatchingConfig")
    return config


def _accepts_keyword_argument(callable_, name):
    try:
        parameters = signature(callable_).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        (
            parameter.name == name
            and parameter.kind
            in (Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY)
        )
        or parameter.kind is Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def transcribe_clip_and_forward(
    clip,
    model_handle,
    forwarder,
    matching_config,
    flow_config=None,
    *,
    measurement_callback=None,
    audio_preprocessor=None,
):
    """Transcribe and match a finite clip; MQTT destinations are reserved for microphone chunks."""
    matching_config = _validate_matching_config(matching_config)
    flow_config = {} if flow_config is None else dict(flow_config)
    source_id = flow_config.get("source_id") or uuid.uuid4().hex
    flow_config["source_id"] = source_id
    language = flow_config.get("language", "th")
    if language != matching_config.language:
        raise WordMatchingError(
            f"Matching language {matching_config.language!r} must match transcript language {language!r}"
        )
    latest_rtf = None

    def record_measurement(measurement):
        nonlocal latest_rtf
        latest_rtf = measurement.get("rtf")
        if measurement_callback is not None:
            measurement_callback(measurement)

    transcription_options = {"source_id": source_id}
    if audio_preprocessor is not None:
        transcription_options["audio_preprocessor"] = audio_preprocessor
    if _accepts_keyword_argument(transcribe_clip, "measurement_sink"):
        transcription_options["measurement_sink"] = record_measurement
    transcript = transcribe_clip(
        clip, model_handle, flow_config, **transcription_options
    )
    matched = match_keywords(transcript, matching_config)
    receipt = None
    error = None
    if forwarder is not None and getattr(forwarder, "transport", None) != "mqtt":
        try:
            receipt = _send(forwarder, _record(source_id, matched, transcript, "completed"))
        except Exception as exc:  # Preserve the completed transcript and match results.
            error = exc
    return PipelineResult(
        transcript, matched.matches, source_id, receipt, error, latest_rtf
    )


class SessionWorkflow:
    """Expose ordered source events plus derived match and delivery events."""

    def __init__(
        self,
        session,
        forwarder,
        matching_config,
        on_finished=None,
        on_stop_error=None,
    ):
        self.session = session
        self.forwarder = forwarder
        self.matching_config = _validate_matching_config(matching_config)
        self._on_finished = on_finished
        self._on_stop_error = on_stop_error
        self.source_id = session.source_id
        self.output_queue = queue.Queue()
        self.result_queue = self.output_queue
        self._finished = threading.Event()
        self._stop_lock = threading.Lock()
        self._stop_requested = False
        self._thread = threading.Thread(
            target=self._consume,
            name=f"match-forward-{self.source_id}",
            daemon=True,
        )
        self._thread.start()

    def subscribe_audio(self):
        """Subscribe to transient 16 kHz mono PCM; caller must unsubscribe."""
        return self.session.audio_monitor.subscribe()

    def unsubscribe_audio(self, listener):
        self.session.audio_monitor.unsubscribe(listener)

    def _consume(self):
        matcher = StreamingWordMatcher(self.matching_config)
        latest_match = match_keywords("", self.matching_config)
        chunk_ordinal = 0
        cumulative_transcript = None
        measurement_rtfs = {}
        completed_event = None
        try:
            while completed_event is None:
                event = self.session.result_queue.get()
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "transcript":
                    chunk_ordinal += 1
                    self.output_queue.put(event)
                    try:
                        chunk_transcript = event.get("text", "")
                        if event.get("cumulative"):
                            cumulative_transcript = chunk_transcript
                            latest_match = match_keywords(chunk_transcript, self.matching_config)
                        else:
                            latest_match = matcher.add_chunk(chunk_transcript)
                    except Exception as exc:
                        self.output_queue.put(
                            {
                                "type": "matching_error",
                                "source_id": self.source_id,
                                "error": str(exc),
                            }
                        )
                        continue
                    self.output_queue.put(
                        {
                            "type": "match_results",
                            "source_id": self.source_id,
                            "language": latest_match.language,
                            "matches": _match_rows(latest_match),
                        }
                    )
                    if self.forwarder is not None and not event.get("cumulative"):
                        try:
                            chunk_matched = match_keywords(
                                chunk_transcript, self.matching_config
                            )
                            receipt = _send(
                                self.forwarder,
                                _record(
                                    self.source_id,
                                    chunk_matched,
                                    chunk_transcript,
                                    "completed",
                                    event_id=f"{self.source_id}:chunk:{chunk_ordinal}",
                                    rtf=measurement_rtfs.pop(event.get("sequence"), None),
                                    include_rtf=True,
                                ),
                            )
                            self.output_queue.put(
                                {
                                    "type": "forwarded",
                                    "source_id": self.source_id,
                                    "receipt": receipt,
                                }
                            )
                        except Exception as exc:
                            self.output_queue.put(
                                {
                                    "type": "forwarding_error",
                                    "source_id": self.source_id,
                                    "event_id": f"{self.source_id}:chunk:{chunk_ordinal}",
                                    "error": str(exc),
                                    "status_code": getattr(exc, "status_code", None),
                                    "attempts": getattr(exc, "attempts", None),
                                }
                            )
                    continue
                if event.get("type") == "measurement":
                    sequence = event.get("sequence")
                    if isinstance(sequence, int) and not isinstance(sequence, bool):
                        measurement_rtfs[sequence] = event.get("rtf")
                        while len(measurement_rtfs) > 128:
                            del measurement_rtfs[min(measurement_rtfs)]
                if event.get("type") == "completed":
                    completed_event = event
                else:
                    self.output_queue.put(event)

            try:
                if self.forwarder is not None and cumulative_transcript:
                    receipt = _send(
                        self.forwarder,
                        _record(
                            self.source_id, latest_match, cumulative_transcript,
                            "completed", event_id=f"{self.source_id}:final", include_rtf=True,
                        ),
                    )
                    self.output_queue.put(
                        {"type": "forwarded", "source_id": self.source_id, "receipt": receipt}
                    )
                self.output_queue.put(
                    {
                        "type": "match_results",
                        "source_id": self.source_id,
                        "language": latest_match.language,
                        "matches": _match_rows(latest_match),
                    }
                )
            except Exception as exc:
                self.output_queue.put(
                    {
                        "type": "matching_error",
                        "source_id": self.source_id,
                        "error": str(exc),
                    }
                )
        except Exception as exc:
            self.output_queue.put(
                {
                    "type": "workflow_error",
                    "source_id": self.source_id,
                    "error": str(exc),
                }
            )
        finally:
            on_finished, self._on_finished = self._on_finished, None
            if on_finished is not None:
                try:
                    on_finished()
                except Exception as exc:
                    self.output_queue.put(
                        {
                            "type": "workflow_error",
                            "source_id": self.source_id,
                            "error": str(exc),
                        }
                    )
            if completed_event is not None:
                self.output_queue.put(completed_event)
            self._finished.set()

    def stop(self, *, timeout=30):
        """Stop the Feature 01 source and wait for matching and delivery."""
        with self._stop_lock:
            if not self._stop_requested:
                self._stop_requested = True
                try:
                    self.session.stop(timeout=timeout)
                except Exception as exc:
                    error = str(exc).strip() or type(exc).__name__
                    if self._on_stop_error is not None:
                        try:
                            self._on_stop_error()
                        except Exception as cleanup_error:
                            cleanup_message = (
                                str(cleanup_error).strip()
                                or type(cleanup_error).__name__
                            )
                            error = f"{error}; forced shutdown failed: {cleanup_message}"
                    self.session.result_queue.put(
                        {
                            "type": "workflow_error",
                            "source_id": self.source_id,
                            "error": error,
                        }
                    )
                    self.session.result_queue.put(
                        {
                            "type": "completed",
                            "source_id": self.source_id,
                            "status": "failed",
                        }
                    )
        if not self._finished.wait(timeout):
            raise TimeoutError(
                f"Transcript workflow {self.source_id!r} did not finish before timeout"
            )

    def wait(self, timeout=None):
        """Wait for Feature 01 completion and workflow delivery processing."""
        if not self._finished.wait(timeout):
            raise TimeoutError(
                f"Transcript workflow {self.source_id!r} did not finish before timeout"
            )


def forward_session(
    session,
    forwarder,
    matching_config,
    *,
    on_finished=None,
    on_stop_error=None,
):
    """Wrap one Feature 01 source, including a process-backed session."""
    if not hasattr(session, "result_queue") or not hasattr(session, "source_id"):
        raise TypeError("session must expose source_id and result_queue")
    if not callable(getattr(session, "stop", None)):
        raise TypeError("session must expose stop(timeout=...)")
    return SessionWorkflow(
        session,
        forwarder,
        matching_config,
        on_finished,
        on_stop_error,
    )


def start_microphone_and_forward(
    device,
    model_handle,
    forwarder,
    matching_config,
    flow_config=None,
    *,
    on_finished=None,
    audio_source_factory=None,
    audio_preprocessor=None,
):
    """Start and wrap one Feature 01 microphone session as a workflow."""
    source_options = (
        {"audio_source_factory": audio_source_factory}
        if audio_source_factory is not None
        else {}
    )
    if audio_preprocessor is not None:
        source_options["audio_preprocessor"] = audio_preprocessor
    session = start_microphone_flow(device, model_handle, flow_config, **source_options)
    return forward_session(
        session, forwarder, matching_config, on_finished=on_finished
    )


def list_available_microphones():
    """Expose Feature 01 input-device discovery to workflow callers."""
    return list_microphone_devices()
