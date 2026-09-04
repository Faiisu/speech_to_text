# 15: Choose the transcription language per session

**What to build:** A **Language** field in the panel (live session and Benchmark Studio) and `--language` on the CLI and benchmark, applied identically by every runtime. Defaults to Thai. Includes an explicit *auto-detect* option, which hands the choice to the model per chunk. See ADR 0005.

**Blocked by:** 12

**Status:** done — verified end-to-end across pytorch, ctranslate2 and whispercpp

- [x] `Runtime.transcribe(audio, language)` implemented for all five runtimes
- [x] `GET /languages` serves the list and default, so it lives only in `transcribe.LANGUAGES`
- [x] Language field in the live panel and in Benchmark Studio, populated from that endpoint
- [x] `--language` on `transcribe.py` and `benchmark.py`
- [x] `POST /start` and `POST /benchmark` accept and validate it; an unknown code is refused with `422`
- [x] The field is disabled while a session is running, like the other session settings
- [x] Auto-detect is labelled with what it actually does, in the panel hint and the help page
- [ ] OpenVINO paths exercised on real Intel hardware (blocked on the same hardware as ticket 12)

**Bug this fixed:** the runtimes did not agree on language. `ctranslate2` and `whispercpp` hardcoded `language="th"`; `pytorch` and both OpenVINO paths passed nothing and let Whisper auto-detect per chunk. So three of five ran an extra detection pass and could silently switch language — or *translate* rather than transcribe — mid-session, while two could not. Benchmarks were comparing runtimes doing measurably different amounts of work, which is the same failure as the `beam_size` mismatch in ticket 12 but harder to notice, since both behaviours look correct on clean Thai audio.

**Verified:** on `audio/test_clip.wav` (Thai speech), `pytorch` returns "สวัสดีครับ" for `th` and `auto`, and "Thank you." for `en`; `whispercpp` returns Thai for `auto` and "." for `en`. The setting demonstrably reaches the model on each path rather than being accepted and dropped.

**Observed while testing, not investigated:** on that same clip `ctranslate2` produced a long rambling transcript where `pytorch` produced "สวัสดีครับ". Same audio, same language, same chunk. Worth chasing before trusting `ctranslate2` output quality — it may be int8 quantization damage on this fine-tune, and it would not show up in any timing comparison.
