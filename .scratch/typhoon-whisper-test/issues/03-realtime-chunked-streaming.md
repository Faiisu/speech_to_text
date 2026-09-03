# 03: Real-time chunked streaming transcription with per-chunk metrics

**What to build:** While a live recording (from ticket 02) is in progress, audio is buffered into 5-second chunks with 1-second overlap between consecutive chunks. Each chunk is transcribed as it fills and printed as a new append-only log line showing that chunk's transcript, transcription latency, and RTF_chunk (latency ÷ chunk duration, per `CONTEXT.md`). When recording stops, the existing full-clip reference transcript (ticket 02) still prints afterward for manual comparison against the chunked output.

**Blocked by:** 01, 02

**Status:** done — verified live by the user

- [x] While recording is active, audio is buffered into 5s chunks with 1s overlap between consecutive chunks
- [x] Each chunk is transcribed independently as soon as its window fills, without waiting for recording to stop
- [x] Each chunk's result prints as a new log line (append-only, not overwriting previous lines) showing: transcript text, latency, and RTF_chunk
- [x] Overlapping audio between chunks is not stitched or deduplicated — each chunk's transcript stands alone, per ADR 0001
- [x] After recording stops, the ticket 02 full-clip reference transcript still prints as the final output
- [x] Verified live end-to-end by a human with microphone access
