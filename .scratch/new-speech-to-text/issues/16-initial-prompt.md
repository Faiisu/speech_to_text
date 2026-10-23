# Configure initial transcription prompts

Status: wontfix

Legacy outcome: Implemented per-flow `decoding_options.initial_prompt` for CTranslate2 and OpenVINO, with Profile and Model tuning controls, existing JSON persistence, and immutable run/recovery/trial snapshots.

## Scope and acceptance

The [initial prompt contract](../spec.md#initial-prompt) owns defaults, validation, runtime behavior, and token budgeting. Empty prompts preserve previous behavior. Non-string values are rejected. Independent workflows can share a model while using different prompts. OpenVINO reserves output positions after its prompt and task prefix. Profile save/reopen/duplicate/start, recovery, direct live requests, and tuning upload paths retain the effective prompt.

## Verification

2026-10-23: Core, backend propagation, and frontend integration were developed by separate `gpt-6-luna` sub-agents with high reasoning; the parent reviewed integration. No dependencies, database columns, or model artifacts changed.

Frontend production build passed. Chromium passed 27 tests across Model tuning and Profile/run lifecycle, including Thai prompt persistence and separate trial snapshots. Focused backend tests passed 152 cases. Core tests cover empty/default behavior, strict types, both runtime arguments, prompt token limits, and sequential independent prompts with automatic and explicit language selection.

Parent integration gate: `uv run --no-sync python -m pytest -q --import-mode=importlib --disable-warnings --tb=short` passed 578 tests with 9 optional skips. `git diff --check` passed. Tuning submission trims the prompt before retaining its trial snapshot, matching backend normalization.

Real CPU smoke used the first two seconds of `audio/test-audio.wav` with a Thai vocabulary prompt. The installed `typhoon-large` CTranslate2 int8 artifact accepted the prompt and returned text. The installed `turbo` OpenVINO source artifact accepted prompt IDs with its speech filter both disabled and enabled, returning plain text without the supplied prompt prefix. This verifies execution, not transcription quality: the CTranslate2 large result on this short sample was garbled. OpenVINO large-v3 conversion, GPU/UBX hardware, representative accuracy, and capacity were not exercised. Detailed transient output is `/tmp/speech-initial-prompt-smoke.json` on the development host.

### Prompt-quality regression report

2026-10-23: A user reported repetitive output after adding a Thai vocabulary list to `typhoon-large` with CTranslate2. Parent reproduced degradation on the first 8 seconds of the previously provided `custoner_serve_subway.wav`, using the installed int8 artifacts, Thai language, greedy decoding, previous-text conditioning disabled, no-repeat n-gram size 3, and repetition penalty 1. Only `initial_prompt` changed between the large-model trials.

- Empty prompt: `you ถาม ชีพ ได้ ไหม ค่ะ ได่ คุณ เป็น เมนู อะไร คะ เอ่อ ดู สิ ใน ในนั้น`.
- User prompt: `สวัสดี, ขอบคุณ, ทานชีสได้ไหม, ไก่, บาบีคิว, บูโกกิ, ลมควัน, อบ` (60 text tokens, below the library's retained prompt-context bound).
- Same large artifact with that prompt: `ชีข้างชี ชีธง ชี้ ชิน ชา ชัน แก็ ช็ ค่า คา แม ช่า`.
- `turbo` CTranslate2 int8 with the same prompt/audio/settings: `ทานชีสได้ไหมคะ ได้ค่ะ เป็นเมนูอะไรคะ เอ่อ ดูสิ เปรี้ยวเก่งมาก`.

The adapter forwards the supplied text directly to faster-whisper's documented `initial_prompt` argument; the reproduced degradation occurs with native prompting and is not evidence of an application-side prompt concatenation or shared-model state leak. This narrows the issue to the tested checkpoint/runtime artifact's prompt-conditioned behavior, without proving that the source checkpoint, conversion, or quantization alone caused it. Clearing the prompt is the established workaround. Do not claim that shortening the list or changing repetition controls restores accuracy without a controlled test. Transient A/B evidence is `/tmp/diagnose-typhoon-prompt.json` on the development host.

Follow-up with the same large artifact/audio/settings: a one-word prompt `ไก่` returned `ครัครับครันชินไงครังครินงครา`; a short context sentence `ลูกค้าสั่งอาหารที่ร้านซับเวย์` returned `ชาสั่งชินชิหน้าชิงชาชา`. Shortening or rephrasing did not restore intelligible output in these trials. No runtime behavior was changed in response to this diagnosis.

### Checkpoint versus runtime evidence

2026-10-23 follow-up: Typhoon's own [0.6b contextual model card](https://huggingface.co/typhoon-ai/typhoon-asr-qwen-0.6b-ctx) and [1.7b card](https://huggingface.co/typhoon-ai/typhoon-asr-qwen-1.7b-ctx) report prompt-list collapse and echo loops for `typhoon-whisper-large-v3` in their comparison. This corroborates a checkpoint-level prompting limitation, but the cards do not document enough runtime/precision detail to independently exclude every conversion or quantization effect.

The installed source and CTranslate2 int8 artifact share repository `typhoon-ai/typhoon-whisper-large-v3` and revision `748e8a418697d5920e62bced10590072dce90da5`. SHA-256 hashes of their `tokenizer.json` and `tokenizer_config.json` match, reducing evidence for a copied-tokenizer mismatch. There is no local OpenVINO large-v3 artifact. The source weights occupy about 5.8 GiB in float32, while only about 5 GiB RAM was available on the 16 GiB development host, so no large-model conversion or additional full-precision load was attempted. OpenVINO large-v3 remains unverified; the existing real OpenVINO prompting proof applies to `turbo` only. Clearing the prompt remains the demonstrated workaround for the tested large checkpoint. No decoder-setting change has been proven to restore its prompt quality.
