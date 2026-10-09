"""Feature 01's operation measurement record shape."""

from datetime import datetime, timezone
import os


def inference_measurement(*, operation, source_id, sequence, audio_seconds, inference_seconds,
                         status="completed", error=None, clock=None):
    completed_at = (clock or (lambda: datetime.now(timezone.utc)))()
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)
    return {"feature_id": "feature-01-model-deployment", "operation": operation,
            "pid": os.getpid(), "completed_at": completed_at.astimezone(timezone.utc).isoformat(),
            "source_id": source_id, "sequence": sequence,
            "elapsed_seconds": float(inference_seconds), "audio_seconds": float(audio_seconds),
            "inference_seconds": float(inference_seconds),
            "rtf": float(inference_seconds) / float(audio_seconds) if audio_seconds else None,
            "status": status, "error": str(error) if error is not None else None}


def publish_measurement(sink, record):
    """Publish once through a callable or non-blocking queue sink."""
    if callable(sink):
        sink(record)
    elif callable(getattr(sink, "put_nowait", None)):
        sink.put_nowait(record)
    else:
        sink.put(record)


def publish_inference_measurement(sink, *, operation, source_id, sequence, audio_seconds,
                                  inference_seconds, status="completed", error=None, clock=None):
    """Create and best-effort publish one measurement without affecting inference."""
    if sink is None:
        return False
    try:
        record = inference_measurement(operation=operation, source_id=source_id,
            sequence=sequence, audio_seconds=audio_seconds, inference_seconds=inference_seconds,
            status=status, error=error, clock=clock)
        publish_measurement(sink, record)
        return True
    except Exception:
        return False


def publish_service_event(sink, *, event_name, severity="info", feature_id="feature-01-model-deployment",
                          pid=None, source_id=None, attributes=None):
    """Best-effort publication using the shared writer's safe event contract."""
    if sink is None:
        return False
    try:
        from ..system_observability.writer import make_event
        event = make_event(event_name=event_name, severity=severity, feature_id=feature_id,
                           pid=pid, source_id=source_id, attributes=attributes)
        sink.publish_event(event)
        return True
    except Exception:
        return False
