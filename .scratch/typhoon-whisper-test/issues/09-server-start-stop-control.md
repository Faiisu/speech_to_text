# 09: Start/stop recording control via HTTP, no GUI yet

**What to build:** A new `server.py` that imports the existing recording/transcription logic from `transcribe.py` as a library and wraps it in a FastAPI app. Running `python server.py` starts a web server bound to `0.0.0.0` (reachable from other devices on the LAN, like the backend already is). `POST /start` (JSON body: model, keywords, mic device, silence threshold, backend URL) begins a recording session in the background; `POST /stop` ends it; `GET /status` reports whether a session is active and its current config. `transcribe.py`'s existing CLI modes are untouched by this change.

**Blocked by:** None (can start immediately)

**Status:** code complete; one real bug found and fixed during testing; full live verification still needed by a human (see note)

- [x] `python server.py` starts a web server reachable on the LAN (bound to `0.0.0.0`), not just localhost
- [x] `POST /start` accepts model, keywords, mic device, silence threshold, and backend URL, and begins a recording session in the background without blocking the HTTP response (verified: returns immediately, `/status` transitions `loading` → `recording`)
- [x] `POST /stop` ends the active recording session (equivalent to the CLI's "press Enter to stop")
- [x] `GET /status` reports whether a session is currently recording and, if so, its active config
- [x] Calling `POST /start` while a session is already active returns a clear error (409) rather than starting a second concurrent session — verified
- [x] `transcribe.py`'s existing CLI behavior is unchanged — re-verified file-mode transcription and the `spot_keywords` debounce logic after the refactor extracting `run_recording_session`
- [ ] Full live verification (a real recording actually starting and stopping cleanly end-to-end) — this sandbox cannot reliably grant a background process real, stable microphone access; see bug note below for what happened when tested here

**Bug found and fixed:** while testing here, a live session got stuck in `stopping` state indefinitely after `POST /stop` — `worker.join()` (waiting for the chunk-processing thread to notice the stop request) blocked with no timeout, so a slow/stuck transcription call (this hardware has shown RTF > 1, i.e. slower than real-time) could hang `/stop` forever with zero feedback. Root cause in *this* run couldn't be conclusively pinned down (no `py-spy` without sudo in this sandbox, and mic/Metal access for a headless background process here is itself unreliable — the same limitation that blocked live verification of tickets 02/03/04/08). Fixed defensively regardless of root cause: `worker.join(timeout=STOP_JOIN_TIMEOUT_SECONDS)` (60s) now bounds the wait; if the thread is still alive after that, a `warning` event is emitted and the function returns with whatever audio was captured, instead of hanging forever. Verified the timeout-and-degrade mechanism in isolation with a synthetic stuck-thread test. This benefits the CLI too, which had the same unbounded-hang risk on "press Enter to stop" before this fix.

Needs a human with real microphone access to confirm a full start → speak → stop cycle completes cleanly end-to-end via the API.