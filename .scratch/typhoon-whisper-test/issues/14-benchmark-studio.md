# 14: Benchmark Studio — compare runtimes from the browser

**What to build:** Record a clip on the server from the panel, then benchmark several runtimes against that one clip and see the comparison in the browser: per-chunk latency and RTF streaming in as each runtime runs, then a summary per runtime (mean/median/worst RTF, whether it keeps up with live speech) and the winner. Runtimes that aren't usable are skipped with the reason rather than failing the run. See ADR 0006.

**Blocked by:** 12, 13

**Status:** done — three bugs found and fixed during review (below)

- [x] `POST /record-server/start|stop|status` records a clip using the server's own microphone and saves it to `audio/`
- [x] `POST /benchmark` streams progress over SSE: init, per-runtime start/skip, per-chunk timings, per-runtime summary, final result with winner
- [x] Unavailable runtimes are skipped with their reason; a runtime that throws reports the error without aborting the whole run
- [x] Refuses to run while a live session is active
- [x] Benchmark Studio UI in the panel drives all of the above

**Three bugs found and fixed in review:**

1. **Arbitrary file write (security).** `/record-server/stop` wrote to `(AUDIO_DIR / filename)` with no containment check, while every other filesystem endpoint validated theirs. A caller-supplied `"../../../tmp/x"` or an absolute path escaped `audio/` entirely — on a server that binds `0.0.0.0` with no authentication, so anyone on the LAN could write a file anywhere the process could. Now validated at start (so a bad name fails before the operator records anything) and again at the write.
2. **Benchmark chunk size hardcoded to `5.0`** while `chunk_offsets()` used `CHUNK_SECONDS`. They agree only because `CHUNK_SECONDS` happens to be 5 — changing it, which the docs actively invite as a tuning experiment, would have silently produced mismatched chunks and wrong RTF. Both now use the constant.
3. **No guard against concurrent benchmarks.** Two devices could benchmark simultaneously and contend for the same CPU, silently corrupting the very timings the benchmark exists to produce. Now one at a time, with the slot released in a `finally` so an abandoned run (client closes the tab mid-benchmark) can't block later ones — verified it recovers, including on client disconnect.
