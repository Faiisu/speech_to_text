# Typhoon Whisper Speech Detection Research

Research snapshot: 2026-10-22. This note records model/runtime capability and the current application's integration. It does not change requirements or demonstrate recognition quality on project recordings.

## Model identity

The built-in `turbo` key maps to `typhoon-ai/typhoon-whisper-turbo` in [the catalog](../../speech_to_text/features/model_deployment/catalog.py). The local catalog inspected for this research also maps `typhoon-large` to `typhoon-ai/typhoon-whisper-large-v3`; local registration does not prove which model an active workflow uses. Both belong to the Whisper family: the official cards identify the [Turbo base as Whisper Large v3 Turbo](https://huggingface.co/typhoon-ai/typhoon-whisper-turbo) and the [full model as Whisper Large v3](https://huggingface.co/typhoon-ai/typhoon-whisper-large-v3).

## Capability and current behavior

| Mechanism | Upstream capability | Current application |
| --- | --- | --- |
| RMS silence gate | Measures signal energy, not speech probability. | Implemented before inference; defaults and validation belong to the [Feature 01 configuration contract](../new-speech-to-text/spec.md#config-scope-and-defaults). It cannot distinguish loud background noise from speech. |
| `no_speech_threshold`, CTranslate2 | `WhisperModel.transcribe` in locked faster-whisper 1.2.1 defaults to `0.6`, with `log_prob_threshold=-1.0`. The skip check compares the model's no-speech probability and lets a sufficiently high transcript log probability override skipping. [Versioned source](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/transcribe.py#L700). | The [CTranslate2 adapter](../../speech_to_text/features/model_deployment/runtime.py) does not override these arguments, so it inherits those defaults. This mechanism already applies when that adapter is selected, although callers cannot tune it through application settings. |
| `no_speech_threshold`, OpenVINO | Transformers documents the parameter for long-form transcription, together with `logprob_threshold`. [Whisper documentation](https://huggingface.co/docs/transformers/v4.57.1/en/model_doc/whisper). Optimum Intel 2.2.0 uses Whisper's generation implementation in its Whisper wrapper. [Versioned Optimum source](https://github.com/huggingface/optimum-intel/blob/v2.2.0/optimum/intel/openvino/modeling_seq2seq.py). | The adapter does not pass the parameter. The app feeds independent chunks of at most 30 seconds into `generate`; adding the argument alone is not an established solution for this short-form path. |
| VAD | faster-whisper integrates Silero VAD using `vad_filter=True` and optional `vad_parameters`. [Official usage](https://github.com/SYSTRAN/faster-whisper#vad-filter). The regular `WhisperModel.transcribe` defaults to `vad_filter=False`; batched transcription has a different default. [Versioned source](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/transcribe.py#L700). | The adapter calls regular `WhisperModel.transcribe` without enabling VAD. OpenVINO has no separate VAD stage in the application. The [configuration validator](../../speech_to_text/features/model_deployment/config.py) accepts neither VAD nor no-speech tuning options. |

The lockfile records faster-whisper 1.2.1, Transformers 4.57.6, and Optimum Intel 2.2.0. Findings describe that dependency contract, not a verified installation on a deployed host.

## Practical direction

For rejecting non-speech before inference with either runtime, evaluate an external VAD stage. This is an integration recommendation, not a claim that Typhoon weights contain a separate VAD model. The existing CTranslate2 path can use faster-whisper's integrated Silero option; an OpenVINO path would need a separate preprocessing stage. VAD and decoder no-speech filtering can complement one another because they operate at different stages.

If exposing no-speech tuning, preserve runtime-specific semantics: faster-whisper names its companion setting `log_prob_threshold`, while Transformers names it `logprob_threshold`. Do not promise equal behavior across adapters merely because the threshold names match.

No model weights were loaded and no inference benchmark was run for this research. Speech recall, hallucination reduction, runtime overhead, and suitable thresholds remain unverified on this project's Thai speech and background noise.
