# 01: Batch-transcribe a single audio file with either Typhoon model

**What to build:** Running `python transcribe.py --model turbo --file audio.wav` (or `--model large-v3`) loads the corresponding Typhoon Whisper model (`typhoon-ai/typhoon-whisper-turbo` or `typhoon-ai/typhoon-whisper-large-v3`), picks the PyTorch MPS backend if available and falls back to CPU otherwise, transcribes the whole file in one batch pass, and prints the transcript together with which device/platform ran it. This is the transcription core that later tickets reuse — model loading, device fallback, and the two correct model IDs need to work here first.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] `--model turbo` and `--model large-v3` both load their correct Hugging Face repo (`typhoon-ai/typhoon-whisper-turbo`, `typhoon-ai/typhoon-whisper-large-v3`) without requiring `git-lfs`
- [x] Device selection automatically uses MPS when available, otherwise CPU, with no code change needed to run on a CPU-only Linux machine
- [x] `--file <path>` transcribes the entire audio file in a single batch pass and prints the resulting transcript
- [x] Output includes which device/platform (e.g. `mps` / `cpu`, OS) ran the transcription
