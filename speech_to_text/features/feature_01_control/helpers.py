"""Small HTTP translation helpers shared by Feature 01 routes."""

from fastapi import HTTPException


def http_error(exc):
    return HTTPException(
        status_code=400, detail={"type": type(exc).__name__, "message": str(exc)}
    )


def flow_form(form):
    result = {
        "language": form.get("language", "th"),
        "chunk_seconds": float(form.get("chunk_seconds", 5)),
        "silence_threshold": float(form.get("silence_threshold", 0.05)),
    }
    options = {}
    for key, cast in (("beam_size", int), ("temperature", float)):
        if form.get(key) not in (None, ""):
            options[key] = cast(form[key])
    for key in ("condition_on_previous_text",):
        if form.get(key) not in (None, ""):
            options[key] = form[key].lower() == "true"
    if options:
        result["decoding_options"] = options
    return result
