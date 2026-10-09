# Transcript Matching and Forwarding

## Purpose

Compose the existing Feature 01 transcription interface with Thai keyword/phrase matching and outbound HTTP forwarding. Feature 01 continues to own audio input, model execution, chunking, transcript events, and session lifecycle. The matching feature finds configured terms in completed transcripts and reports each term's occurrence count.

## Matching contract

- `WordMatchingConfig` contains a non-empty list of unique target keywords or phrases, `language="th"`, and `engine="newmm"`.
- `match_keywords(text, config)` returns a `WordMatchResult` containing the language, engine, and one `{keyword, count}` result per configured target, including targets with zero occurrences.
- Normalize source text and targets to Unicode NFC and case-fold tokens before comparison.
- Tokenize source and targets with the same PyThaiNLP `word_tokenize` engine. Match exact contiguous token sequences; substring matches inside a larger token do not count.
- Whitespace separates tokens but does not break a phrase match. Punctuation separates token sequences and cannot match a keyword. Count overlapping occurrences for each target independently; different targets may match overlapping spans.
- The initial implementation supports Thai. Raise `WordMatchingError` for unsupported languages, invalid targets, or an unavailable/unsupported tokenizer. Install the optional `thai-word-matching` extra to provide PyThaiNLP.

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

Install the project with the optional Thai tokenizer, then compose the callable modules from the host application:

```python
import os

from speech_to_text.features.model_deployment import load_model, start_microphone_flow
from speech_to_text.features.word_matching import WordMatchingConfig
from speech_to_text.integrations.http_forwarder import HttpForwarder, HttpForwarderConfig
from speech_to_text.workflows.transcribe_match_forward import forward_session

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

## Module layout

- `speech_to_text/features/word_matching/` exposes keyword/phrase matching and hides tokenizer details.
- `speech_to_text/integrations/http_forwarder/` implements the outbound HTTP adapter.
- `speech_to_text/workflows/transcribe_match_forward/` composes Feature 01, matching, and forwarding for clips and sessions.

HTTP failures raise `ForwardingError` with status and safe diagnostic context.

## Scope limits

- Match configured complete token/phrase sequences in each source's assembled transcript, not substring fragments.
- Forward once when a clip is complete or a microphone session emits its terminal event.
- No UI page, inbound HTTP route, database, durable outbox, destination-specific payload mapping, or per-chunk forwarding is included.

## Acceptance criteria

- Matching is callable independently and supports the documented Thai normalization, tokenization, exact-sequence, and occurrence-count behavior.
- Clip orchestration invokes Feature 01 once, matches the assembled transcript, forwards one record with a stable source ID, and returns the result.
- Session orchestration preserves Feature 01 event order, matches once after completion, and forwards once per source for local sessions and process sessions.
- HTTP forwarding sends the documented JSON and headers, honors timeout/retry settings, and reports permanent and exhausted transient failures explicitly.
- The implementation stays in the module layout above and does not alter Feature 01's transcript/event contract.

## References

- [Feature 01 contract](../new-speech-to-text/spec.md)
- [PyThaiNLP word tokenization](https://pythainlp.org/docs/5.3.4/api/tokenize.html)
