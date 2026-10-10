"""Local per-chunk inference measurements."""

from datetime import datetime, timezone
import os


def inference_measurement(
    *,
    operation,
    source_id,
    sequence,
    audio_seconds,
    inference_seconds,
    queue_wait_seconds=0.0,
    status="completed",
    error=None,
    clock=None,
):
    """Build one UTC-stamped RTF record for an inferred audio chunk."""
    completed_at = (clock or (lambda: datetime.now(timezone.utc)))()
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)
    audio_seconds = float(audio_seconds)
    inference_seconds = float(inference_seconds)
    queue_wait_seconds = max(0.0, float(queue_wait_seconds))
    elapsed_seconds = queue_wait_seconds + inference_seconds
    return {
        "type": "measurement",
        "feature_id": "feature-01-model-deployment",
        "operation": operation,
        "pid": os.getpid(),
        "completed_at": completed_at.astimezone(timezone.utc).isoformat(),
        "source_id": source_id,
        "sequence": sequence,
        "elapsed_seconds": elapsed_seconds,
        "audio_seconds": audio_seconds,
        "queue_wait_seconds": queue_wait_seconds,
        "inference_seconds": inference_seconds,
        "rtf": elapsed_seconds / audio_seconds if audio_seconds else None,
        "status": status,
        "error": str(error) if error is not None else None,
    }


def publish_inference_measurement(
    sink,
    *,
    operation,
    source_id,
    sequence,
    audio_seconds,
    inference_seconds,
    queue_wait_seconds=0.0,
    status="completed",
    error=None,
    clock=None,
):
    """Build and best-effort invoke the optional measurement callback."""
    record = inference_measurement(
        operation=operation,
        source_id=source_id,
        sequence=sequence,
        audio_seconds=audio_seconds,
        inference_seconds=inference_seconds,
        queue_wait_seconds=queue_wait_seconds,
        status=status,
        error=error,
        clock=clock,
    )
    if sink is not None:
        try:
            sink(record)
        except Exception:
            pass
    return record
