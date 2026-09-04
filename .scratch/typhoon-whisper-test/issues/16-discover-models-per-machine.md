# 16: Discover models per machine instead of hardcoding two

**What to build:** The panel's Model field lists what this machine can actually run, rebuilt on every request. Installing a model — downloading it into the Hugging Face cache, naming it in `models.local.json`, or converting weights into `models/` — makes it appear on **Rescan**, with no code change and no restart. See ADR 0005.

**Blocked by:** 15

**Status:** done — verified by adding a model to a running server and transcribing with it

- [x] `model_catalog.py` merges four sources: builtin, `models.local.json`, the Hugging Face cache, converted `models/` directories
- [x] `GET /models` rediscovers on every call and reports repo, sources, converted runtimes, and `multilingual`
- [x] Model dropdowns in the panel (live + benchmark) and the help page's status board are all populated from it
- [x] `Literal["turbo","large-v3"]` in `StartRequest`/`BenchmarkRequest` replaced with validation against the catalogue
- [x] `--model` choices on `transcribe.py`, `benchmark.py` and `convert_model.py` come from the catalogue
- [x] Model keys are validated as path-safe, since they now come from disk and become directory names
- [x] A failed `/models` fetch leaves a usable fallback and says what went wrong
- [x] An English-only model with a pinned language is refused with an explanation, not a raw library error

**Verified:** with the server running, writing `models.local.json` made a new key appear in `GET /models` immediately; `openai/whisper-tiny`, which was never named anywhere in the code, ran a full replay session end-to-end and produced a transcript. A duplicate — the same repo reachable as both a builtin key and a cache key — is merged into one entry rather than offered twice under two names.

**Bug found and fixed in `convert_model.py`.** `convert_openvino` wrote a "GPU copy" and a "CPU copy", but `converted_dir()` maps `openvino-gpu` and `openvino-cpu` to the *same* path (`models/openvino-<key>`). So the function saved the IR, then `rmtree`'d that very directory as the "existing secondary", then tried to `copytree` from the directory it had just deleted. The OpenVINO conversion could never have succeeded. Now one directory, no copy. This was on the critical path for the UBX-330M, the machine OpenVINO exists for, and had never been run because no Intel hardware was available.

**Known limits, documented rather than fixed:** `whispercpp` still only has GGML weights for `turbo`; a model found only as converted weights has no repo and so can't run on `pytorch`; and the Language field genuinely does not apply to `.en` models.
