# 13: Replay a clip instead of recording

**What to build:** A session can read its audio from a stored clip instead of the microphone. Set **Source** to *Audio file* in the panel (or use the replay path directly) and the clip runs through the identical chunking, silence gate, keyword spotting and backend reporting, streaming the same events to the same live feed. Clips live in `audio/`, uploaded through the panel or dropped in directly; anything that isn't 16kHz mono is converted with ffmpeg on the way in. See ADR 0006.

**Blocked by:** 11

**Status:** done — verified end-to-end in a browser

- [x] `run_replay_session` emits exactly the events `run_recording_session` emits, so the feed renders both identically
- [x] Source selector swaps the mic-device field for a clip picker; button labels change to "Replay clip" / "Stop replay"
- [x] `GET /audio-files` lists clips with duration; `POST /audio-files` uploads one; `DELETE /audio-files/{name}` removes one
- [x] Uploads that aren't 16kHz mono are converted automatically; without ffmpeg the error names the command to run
- [x] Path traversal refused on every filename that reaches the filesystem
- [x] Chunks are processed as fast as the model manages rather than paced to real time; reported latency/RTF are the real figures
- [x] Verified: uploaded a 44.1kHz stereo clip, it was converted to 16kHz mono and replayed with per-chunk transcripts, latency and RTF in the live feed

**Two bugs found and fixed during verification:**

1. The mic field stayed visible when Source was set to *Audio file*. The CSS `.field { display: flex }` overrides the browser's `[hidden] { display: none }` rule, so setting `.hidden` had no visual effect. Caught only by looking at a screenshot — an automated check of the `.hidden` *property* reported `true` and passed. Checks for visibility now use computed style.
2. The feed said "Recording started" and the status lamp read "Recording" during a replay, when nothing was being recorded. Both now reflect the actual source.
