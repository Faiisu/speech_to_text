# 04: Keyword/phrase spotting during real-time streaming

**What to build:** A `--keywords` CLI flag accepting a comma-separated list of target keywords/phrases (Thai words or multi-word phrases). While a real-time recording (ticket 03) is in progress, after each chunk is transcribed, the script checks that chunk's transcript for an exact (case-insensitive) substring match against each target keyword/phrase and prints an alert line (`[keyword detected] "<word>" at Xs`) for each hit. Duplicate detections caused by the 1s chunk overlap are suppressed via a time-window debounce (per ADR 0002) rather than word-level timestamp trimming, since Thai script has no word boundaries for the model to align to. The existing raw per-chunk transcript log and the final full-clip reference pass are unchanged.

**Blocked by:** 03

**Status:** code complete, awaiting live verification (sandbox has no mic access; needs a human to run it interactively)

- [x] `--keywords` accepts a comma-separated list of one or more target strings (single words or multi-word phrases)
- [x] Each chunk's transcript is checked for an exact, case-insensitive substring match against every target keyword/phrase
- [x] Each detected keyword prints an alert line with the keyword text and its approximate timestamp
- [x] A keyword detected in the overlapping region of two consecutive chunks is not double-alerted: a repeat alert of the same keyword text is suppressed if it already fired within the debounce window. Note: the debounce window had to be widened from the originally-planned "overlap + small buffer" (~2s) to the full chunk *step* interval + buffer (~4.5s with current 5s/1s chunk settings), because overlap-caused duplicates land exactly one chunk apart in time, not within the overlap duration itself. Verified with a standalone unit check of `spot_keywords`.
- [x] The existing raw per-chunk transcript log line and the final full-clip reference transcript are unchanged
- [x] Running without `--keywords` behaves exactly as before (no alerts, no behavior change)
- [ ] Verified live end-to-end by a human with microphone access
