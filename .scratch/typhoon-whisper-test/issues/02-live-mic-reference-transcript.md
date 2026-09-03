# 02: Live mic recording produces one reference transcript

**What to build:** Running the script with no `--file` starts a live microphone recording: press Enter to start recording, press Enter again to stop. On stop, the full recorded clip is fed through the batch transcription core from ticket 01 and the resulting reference transcript is printed. Demoable end-to-end: talk into the mic, get a transcript back.

**Blocked by:** 01

**Status:** code complete, awaiting live verification (sandbox has no mic access; needs a human to run it interactively)

- [x] Omitting `--file` switches the script into live microphone recording mode instead of transcribing a file
- [x] Recording starts on Enter and stops on a second Enter (manual start/stop, no fixed duration)
- [x] On stop, the entire recorded clip is transcribed in one batch pass using the ticket 01 transcription core, for whichever `--model` was selected
- [x] The resulting reference transcript is printed after recording stops
- [ ] Verified live end-to-end by a human with microphone access
