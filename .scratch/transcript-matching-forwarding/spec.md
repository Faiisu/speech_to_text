# Transcript Matching and Forwarding

## Purpose

Compose the existing Feature 01 transcription interface with Thai keyword/phrase matching and workflow-owned output delivery. Feature 01 continues to own audio input, model execution, chunking, transcript events, and session lifecycle. The matching feature finds configured terms in completed transcripts and reports each term's occurrence count. The workflow returns its result to the backend caller and decides whether to send additional output to another backend or external system.

## Matching contract

- `WordMatchingConfig` contains a non-empty list of unique target keywords or phrases and `language="th"`.
- `match_keywords(text, config)` returns a `WordMatchResult` containing the language and one `{keyword, count}` result per configured target, including targets with zero occurrences.
- Normalize source text and each configured target to Unicode NFC, then case-fold them before comparison.
- Match literal substrings. Whitespace and punctuation are ordinary characters and only match when present in both source and target. Count overlapping occurrences for each target independently; different targets may match overlapping spans.
- The initial implementation supports Thai matching configuration. Raise `WordMatchingError` for unsupported languages or invalid targets. Matching does not require a tokenizer dependency.

## Pipeline behavior

- `transcribe_clip_and_forward(clip, model_handle, forwarder, matching_config, flow_config=None)` invokes Feature 01 clip transcription, matches the complete transcript, forwards one result, and returns a `PipelineResult` containing transcript, per-keyword counts, source ID, and delivery result.
- `forward_session(session, forwarder, matching_config)` consumes a Feature 01 session result queue, assembles transcript events in `sequence` order, and matches once after the terminal event. It returns a wrapper exposing the source ID, output event queue, and idempotent `stop()` that delegates to Feature 01 and waits for matching and delivery.
- The session workflow can wrap a `TranscriptionSession` or `ProcessSession`; callers wrap each source from a process group independently.
- Session output exposes original Feature 01 events, followed by a `match_results` event and a forwarding result/error before the terminal completion event. Forwarding failures do not erase the transcript or match counts.
- A failed Feature 01 session forwards its partial transcript with `status: "failed"` so the receiver can distinguish incomplete results.

## Forwarding contract

The HTTP forwarder accepts a required HTTP(S) endpoint URL, optional bearer token, timeout, and retry settings through a configuration object. Defaults are a 5-second request timeout and 3 total attempts. Retry network failures, HTTP 429, and HTTP 5xx responses with bounded backoff; report other non-2xx responses as `ForwardingError` without retrying. No persistent queue is included; after retries are exhausted, return the failure to the caller.

POST one JSON record per completed source:

```json
{
  "event_type": "transcription.match_results",
  "event_id": "<source_id>",
  "source_id": "<source_id>",
  "language": "th",
  "transcript": "<complete transcript>",
  "matches": [
    {"keyword": "สวัสดี", "count": 2},
    {"keyword": "ลาก่อน", "count": 0}
  ],
  "status": "completed",
  "completed_at": "<UTC ISO 8601 timestamp>"
}
```

For clips, `status` is `completed`; for microphone sources it copies Feature 01's terminal status (`completed`, `stopped`, or `failed`). Include every configured target in `matches`, including zero-count entries. Send `Idempotency-Key: <source_id>` and, when configured, `Authorization: Bearer <token>`. Treat any 2xx response as delivered; the response body is not part of the contract.

## Usage

Install the project, then compose the callable modules from the host application:

```python
import os

from speech_to_text.features.model_deployment import load_model, start_microphone_flow
from speech_to_text.features.word_matching import WordMatchingConfig
from speech_to_text.workflows.transcribe_match_forward import (
    HttpForwarder,
    HttpForwarderConfig,
    forward_session,
)

model = load_model({"model": "turbo", "runtime": "openvino-gpu"})
forwarder = HttpForwarder(HttpForwarderConfig(
    endpoint_url=os.environ["TRANSCRIPT_WEBHOOK_URL"],
    bearer_token=os.environ.get("TRANSCRIPT_WEBHOOK_TOKEN"),
))
matching = WordMatchingConfig(keywords=("สวัสดี", "ลาก่อน"))
session = start_microphone_flow(None, model, {"language": "th"})
workflow = forward_session(session, forwarder, matching)

# The workflow exposes transcript, match_results, forwarding, and completion events.
workflow.stop()
model.close()
```

For a finite WAV, use `transcribe_clip_and_forward(clip, model, forwarder, matching, flow_config)` from the same workflow module. `forward_session` can also wrap each session returned by Feature 01's process-group API.

`list_available_microphones()` delegates device discovery to Feature 01's public callable so backend routes can preserve the workflow boundary.

## Module layout

- `speech_to_text/features/word_matching/` exposes keyword/phrase matching and hides its normalization and substring-search implementation.
- `speech_to_text/workflows/transcribe_match_forward/` composes Feature 01 and matching, returns results to its caller, and owns configured output delivery for clips and sessions. Its `http_output.py` module implements the optional outbound HTTP destination.

The public `TranscriptionService` facade owns one lazy model handle for a backend process. It reads `SPEECH_TO_TEXT_MODEL`, `SPEECH_TO_TEXT_RUNTIME`, and `SPEECH_TO_TEXT_PRECISION` when no explicit model configuration is supplied, then exposes keyword validation, clip transcription, microphone startup, device listing, and `close()`. It translates Feature 01 and matching errors into workflow-owned configuration, input, and dependency errors for backend callers.

HTTP failures raise `ForwardingError` with status and safe diagnostic context.

## Scope limits

- Match configured literal substrings in each source's assembled transcript.
- Forward once when a clip is complete or a microphone session emits its terminal event.
- No UI page, inbound HTTP route, database, durable outbox, destination-specific payload mapping, or per-chunk forwarding is included.

## Acceptance criteria

- Matching is callable independently and supports the documented Thai configuration, normalization, literal-substring, and occurrence-count behavior.
- Clip orchestration invokes Feature 01 once, matches the assembled transcript, forwards one record with a stable source ID, and returns the result.
- Session orchestration preserves Feature 01 event order, matches once after completion, and forwards once per source for local sessions and process sessions.
- HTTP forwarding sends the documented JSON and headers, honors timeout/retry settings, and reports permanent and exhausted transient failures explicitly.
- The implementation stays in the module layout above and does not alter Feature 01's transcript/event contract.

## References

- [Feature 01 contract](../new-speech-to-text/spec.md)
