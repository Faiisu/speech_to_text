# 11: Live chunk/keyword feed on the page

**What to build:** While a recording session (started from the GUI in ticket 10) is active, chunk transcripts and keyword detections stream into the page in real time via Server-Sent Events — the browser equivalent of watching the terminal's `[chunk @ Xs] ...` / `[keyword detected] ...` lines, with no page refresh needed.

**Blocked by:** 10

**Status:** done — verified live in a real browser with a real recording session

- [x] The server exposes an SSE endpoint (`GET /stream`, `text/event-stream`) that streams chunk transcript events (text, latency, RTF, timestamp) and keyword detection events as they happen during an active recording session
- [x] The GUI page subscribes via `EventSource` and appends each event to a live-updating log on the page, without polling or manual refresh
- [x] The feed correctly starts showing events after a GUI-triggered "start" and clearly marks the end after "stop" (a `session stopped` line carrying the full-clip reference transcript); session `loading`/`recording`/`error` transitions are shown too
- [x] Silence-skipped chunks (per ADR 0004) are reflected on the page, consistent with the terminal output
- [x] Opening the page in a second browser tab/device receives the same live feed — verified with two tabs open simultaneously, both showing byte-identical event logs (each connection gets its own queue; a slow/dead client is dropped rather than stalling the recording thread)

**Verified live** (real mic, real Thai speech, two browser tabs):

```
… loading model
● recording started (d4de610d-…)
[0.0s] เราไม่บังคับ   (latency 2.59s, RTF 0.52)
[4.0s] สวัสดีครับ   (latency 2.72s, RTF 0.54)
★ keyword detected: "สวัสดี" at 4.0s
★ keyword detected: "ครับ" at 4.0s
[8.0s] แล้ว   (latency 2.38s, RTF 0.48)
■ session stopped — reference transcript: …
```

Notable measurement: **RTF came in at 0.48–0.70** (comfortably faster than real-time on this M2), versus the RTF 3.82 / 19s latency seen before the ADR 0004 anti-repetition fix. That earlier figure was inflated by the repetition loop generating hundreds of junk tokens per chunk — so the repetition fix improved throughput roughly 6x, not just output quality. The debounce was also observed working end-to-end (a "สวัสดี" at 16.0s was correctly suppressed, being within 4.5s of the 12.0s detection).

Also confirmed the whole chain end-to-end: GUI → recording → keyword spotting → backend `POST /events` → TimescaleDB → queryable via `GET /counts`.