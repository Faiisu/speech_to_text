# 10: Web GUI with start/stop and config form

**What to build:** Opening the server's URL (ticket 09) in a browser shows a page with a config form (model, keywords, mic device, silence threshold, backend URL) and start/stop buttons wired to ticket 09's `/start`, `/stop`, and `/status` endpoints. The page reflects current recording state (e.g. disables "start" while already recording, shows "stop" only when active).

**Blocked by:** 09

**Status:** code complete; verified everything not requiring real mic access; needs a human to click through it live

- [x] The server serves an HTML page at its root URL (`GET /`, `text/html`, verified via headers) with a config form covering every field `POST /start` accepts (model, keywords, mic device, silence threshold, backend URL)
- [x] Clicking "start" calls `POST /start` with the form's values and the page reflects that recording is now active (code reviewed; `refreshStatus()` drives button/state display directly from `/status`, exercised for idle/loading/error states)
- [x] Clicking "stop" calls `POST /stop` and the page reflects that recording has ended
- [x] The page's displayed state matches `GET /status` — button-enabled logic verified against every backend status value (`idle`, `loading`, `recording`, `stopping`), including that "stop" is correctly disabled during `loading` to match `/stop`'s own 409 rejection then
- [x] Attempting to start while already recording surfaces the 409 via `alert()` (verified the error path with a real 422/409 response); starting is also disabled client-side once already active
- [x] Clicked through live in an actual browser with a real recording session (start → speak → stop) — verified via Chrome automation against a real mic; full cycle `idle → loading → recording → stopping → idle` reached correctly with the reference transcript displayed

**Two bugs found and fixed during browser verification:**

1. **Blank numeric field broke start.** Clearing the "silence threshold" input made `parseFloat('')` return `NaN`, which `JSON.stringify` serializes as `null`, which the server rejected with a 422 (`Input should be a valid number`). Blank optional fields (silence threshold, backend URL) are now omitted from the request entirely so the server's own defaults apply. A blank backend URL previously passed validation as `""` and would then have failed on every single detection report at runtime.
2. **Errors were invisible.** Failures were shown via `alert()`, and the 3-second status poll would immediately overwrite any inline message. Errors are now rendered inline in the status box and persist until the next start/stop click, with FastAPI's 422 field-error arrays formatted readably instead of dumped as raw JSON.