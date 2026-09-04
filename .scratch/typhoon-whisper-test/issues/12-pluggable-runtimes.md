# 12: Pluggable inference runtimes

**What to build:** Transcription goes through a `Runtime` interface so the same model can be executed by different machinery, chosen per session from the CLI (`--runtime`) or the panel's Runtime field: `pytorch`, `openvino-gpu`, `openvino-cpu`, `ctranslate2`, `whispercpp`. A probe reports which are usable on this machine and, for the rest, why not (library missing / no Intel GPU / weights not converted). `convert_model.py` converts the weights once per machine into `models/`. See ADR 0005.

**Blocked by:** 01

**Status:** done — `pytorch` and `ctranslate2` verified end-to-end; Intel paths unverified (see note)

- [x] `Runtime` interface with one `transcribe(audio) -> str` method, implemented for all five runtimes
- [x] `probe()` reports availability per runtime *and the reason* when unavailable, per model
- [x] `--runtime` on `transcribe.py` and `benchmark.py`; Runtime field in the panel, refreshed when the model changes
- [x] Unavailable runtimes are greyed out in the panel, and `POST /start` refuses one that isn't ready with the reason (client-side `disabled` alone is not authoritative)
- [x] `convert_model.py` converts weights for openvino / ctranslate2 / whispercpp
- [x] Decoding parameters matched across runtimes so timings are comparable
- [ ] OpenVINO (GPU/CPU) and whisper.cpp verified on real Intel hardware

**Verified:** the CTranslate2 conversion of the fine-tuned `typhoon-whisper-turbo` succeeded and transcribed Thai correctly on macOS. That was the step most likely to fail, since Typhoon is a fine-tune rather than stock Whisper.

**Bug found and fixed:** faster-whisper defaults to `beam_size=5` with a temperature-fallback ladder, while the PyTorch path decodes greedily in one pass. Left as-is this did ~5x the work and would have made CTranslate2 look far slower than it is — invalidating the whole point of comparing runtimes. Pinned to `beam_size=1`, `temperature=0.0`, `condition_on_previous_text=False`. Any runtime added later needs the same check.

**Not verified by the author:** the OpenVINO and whisper.cpp paths were written against hardware that wasn't available for testing (no Intel GPU, no AVX-VNNI). Treat the first run on the UBX-330M as the real test; the conversion step in particular may need adjusting.
