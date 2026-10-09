"""Public clip and session orchestration for completed transcripts."""

from dataclasses import dataclass
from datetime import datetime, timezone
import queue
import threading
import uuid

from speech_to_text.features.model_deployment import transcribe_clip
from speech_to_text.features.word_matching import (
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


def _send(forwarder, record):
    method = getattr(forwarder, "forward", None)
    if callable(method):
        return method(record)
    if callable(forwarder):
        return forwarder(record)
    raise TypeError("forwarder must be callable or expose forward(record)")


def _match_rows(result: WordMatchResult):
    return [{"keyword": item.keyword, "count": item.count} for item in result.matches]


def _record(source_id, result, transcript, status):
    return {
        "event_type": "transcription.match_results",
        "event_id": source_id,
        "source_id": source_id,
        "language": result.language,
        "transcript": transcript,
        "matches": _match_rows(result),
        "status": status,
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _validate_matching_config(config):
    if not isinstance(config, WordMatchingConfig):
        raise TypeError("matching_config must be a WordMatchingConfig")
    return config


def transcribe_clip_and_forward(
    clip, model_handle, forwarder, matching_config, flow_config=None
):
    """Transcribe one finite clip, match the full result, then forward it."""
    matching_config = _validate_matching_config(matching_config)
    flow_config = {} if flow_config is None else dict(flow_config)
    source_id = flow_config.get("source_id") or uuid.uuid4().hex
    flow_config["source_id"] = source_id
    language = flow_config.get("language", "th")
    if language != matching_config.language:
        raise WordMatchingError(
            f"Matching language {matching_config.language!r} must match transcript language {language!r}"
        )
    transcript = transcribe_clip(clip, model_handle, flow_config, source_id=source_id)
    matched = match_keywords(transcript, matching_config)
    try:
        receipt = _send(
            forwarder,
            _record(source_id, matched, transcript, "completed"),
        )
        error = None
    except Exception as exc:  # Preserve the completed transcript and match results.
        receipt = None
        error = exc
    return PipelineResult(
        transcript, matched.matches, source_id, receipt, error
    )


class SessionWorkflow:
    """Expose ordered source events plus derived match and delivery events."""

    def __init__(self, session, forwarder, matching_config):
        self.session = session
        self.forwarder = forwarder
        self.matching_config = _validate_matching_config(matching_config)
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

    def _consume(self):
        transcript_events = []
        completed_event = None
        try:
            while completed_event is None:
                event = self.session.result_queue.get()
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "transcript":
                    transcript_events.append(event)
                if event.get("type") == "completed":
                    completed_event = event
                else:
                    self.output_queue.put(event)

            transcript_events.sort(key=lambda event: event.get("sequence", 0))
            transcript = " ".join(
                event.get("text", "")
                for event in transcript_events
                if isinstance(event.get("text", ""), str)
            )
            try:
                matched = match_keywords(transcript, self.matching_config)
                self.output_queue.put(
                    {
                        "type": "match_results",
                        "source_id": self.source_id,
                        "language": matched.language,
                        "engine": matched.engine,
                        "matches": _match_rows(matched),
                    }
                )
                try:
                    receipt = _send(
                        self.forwarder,
                        _record(
                            self.source_id,
                            matched,
                            transcript,
                            completed_event.get("status", "completed"),
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
                            "error": str(exc),
                            "status_code": getattr(exc, "status_code", None),
                            "attempts": getattr(exc, "attempts", None),
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
            self.output_queue.put(completed_event)
        except Exception as exc:
            self.output_queue.put(
                {
                    "type": "workflow_error",
                    "source_id": self.source_id,
                    "error": str(exc),
                }
            )
        finally:
            self._finished.set()

    def stop(self, *, timeout=30):
        """Stop the Feature 01 source and wait for matching and delivery."""
        with self._stop_lock:
            if not self._stop_requested:
                self._stop_requested = True
                self.session.stop(timeout=timeout)
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


def forward_session(session, forwarder, matching_config):
    """Wrap one Feature 01 source, including a process-backed session."""
    if not hasattr(session, "result_queue") or not hasattr(session, "source_id"):
        raise TypeError("session must expose source_id and result_queue")
    if not callable(getattr(session, "stop", None)):
        raise TypeError("session must expose stop(timeout=...)")
    return SessionWorkflow(session, forwarder, matching_config)
