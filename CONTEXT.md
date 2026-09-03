# Speech-to-Text Testing

Testing and benchmarking Typhoon Whisper speech-to-text models (Thai-language Whisper fine-tunes) for transcription quality and hardware performance.

## Language

**Real-time transcription**:
Live incremental transcription of audio as it's being spoken, produced by re-transcribing overlapping buffered chunks while recording is still in progress (not a native streaming model capability). Distinct from a single batch transcription run over a completed recording.
_Avoid_: Streaming (ambiguous — could imply native model streaming support, which these models don't have)

**Chunk**:
A fixed-length window of buffered audio (5 seconds, with 1 second of overlap with the previous chunk) that gets independently transcribed during a real-time session. Overlapping regions are not deduplicated or stitched — each chunk's transcript is logged as-is.

**RTF_chunk**:
Real-time factor for a single chunk: transcription latency for that chunk divided by the chunk's audio duration. RTF_chunk < 1 means the model kept pace with live speech; RTF_chunk ≥ 1 means it fell behind.

**Reference transcript**:
The full-clip batch transcription produced once, after a real-time session stops, by transcribing the entire recording in one pass. Used as a manual quality comparison point against the chunked/streamed output — not a ground-truth score (no automated WER/diff, since no independently-verified ground truth exists).

**Keyword spotting**:
Detecting whether one or more target keywords/phrases were spoken during a real-time session, by exact substring match against each chunk's transcribed text. Distinct from general transcription — the goal is "was this said," not a full readable transcript.
_Avoid_: Keyword detection, wake word (this project's targets aren't limited to wake words)

**Debounce window**:
The time window during which a repeat alert for the same keyword text is suppressed, to avoid double-alerting when a keyword falls across the overlap between two chunks. Sized to the chunk *step* interval (chunk length minus overlap) plus a small buffer, not the overlap duration itself — an overlap-caused duplicate detection always lands exactly one chunk later, i.e. one step apart, not within the overlap window. See ADR 0002.
