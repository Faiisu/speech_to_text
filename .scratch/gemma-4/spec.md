# Gemma 4 E2B/E4B transcription

## Goal

Make Google's `google/gemma-4-E2B-it` and `google/gemma-4-E4B-it` optional, locally runnable speech recognition models in the existing speech-to-text application. Keep the current Whisper model and existing behavior intact; the optional Gemma Compose deployment defaults to E2B, with E4B still selectable.

## User outcome

An operator can prepare either Gemma 4 E2B or E4B from Hugging Face, select the model and a compatible runtime, and use the existing finite-clip, microphone/profile, and model-tuning transcription flows. The transcript remains plain text and goes through the existing matching and forwarding behavior.

## Ownership and compatibility

- The model-deployment feature owns the Gemma inference adapter and model catalog/runtime compatibility.
- The existing model-management workflow and page own model preparation and selection.
- The transcription workflow continues owning chunking, pre-processing, transcript events, matching, and output delivery.
- Do not change the standard image, installer, or Whisper defaults/behavior.
- Do not edit `legacies-poc/`.

## Functional requirements

1. Discover `google/gemma-4-E2B-it` and `google/gemma-4-E4B-it` as named downloadable models and expose only runtime/precision combinations the shared adapter can load. Existing catalog entries and Whisper runtime compatibility remain unchanged.
2. Prepare the selected repository into the current managed model storage with provenance, using existing model-management job lifecycle and safe local loading behavior. No remote code execution. If Gemma cannot use an existing conversion path, offer source preparation and a compatible Transformers runtime instead of advertising unsupported conversions.
3. The adapter accepts the existing mono 16 kHz audio chunk and language/decoding inputs, builds the documented audio transcription prompt, runs inference, and returns transcript text only. Preserve Thai, English, and automatic language selection where supported.
4. Respect Gemma 4 E2B/E4B's documented maximum 30-second audio input. Existing configured chunks are at most 30 seconds; reject incompatible input clearly if preprocessing or direct clip calls could exceed this bound.
5. Model loading errors identify missing runtime dependencies, unavailable model files, or unsupported runtime settings without leaking credentials or changing another runtime's behavior.
6. The frontend can prepare and select Gemma through existing model controls. Existing language, profile, tuning, and model API contracts remain valid.
7. Provide a separate optional Gemma Docker Compose deployment using the same in-process adapter and frontend. It must persist `/opt/models` and `/data`, and default to Gemma E2B with the Transformers runtime. Keep the existing OpenVINO image and UBX installer defaults intact.
8. Update authoritative feature/configuration/deployment documentation with installation requirements, model identifier, supported runtime and precision, audio-length limit, deployment command, storage behavior, and known hardware constraints.

## Acceptance criteria

- Model catalog reports both Gemma repositories and a truthful supported-runtime/readiness state using local filesystem inspection; each can be prepared and selected independently.
- Existing Whisper models retain their current runtime options, defaults, chunking, and transcript behavior.
- Gemma E2B and E4B can each be downloaded/prepared through the managed lifecycle and loaded from their own local artifacts without enabling `trust_remote_code`.
- The adapter forms a transcription request for audio and language, strips model control tokens from final text, and returns plain transcript text.
- Thai and auto-language values are handled explicitly; unsupported language values fail as normal configuration errors.
- Audio greater than 30 seconds is never passed as one Gemma audio input.
- Profile, clip, and model-tuning paths continue using the existing model handle interface.
- Documentation points to this specification as the contract.

## Out of scope

- Changing the standard OpenVINO image or UBX installer default model.
- Model fine-tuning, quantization/export formats not natively supported by the chosen runtime, speech translation, streaming/token-level transcripts, or a separate deployment service.
- Establishing recognition quality, latency, or hardware capacity without target-hardware audio evaluation.

## Runtime requirements and limits

- Hugging Face repositories and local catalog keys: `google/gemma-4-E2B-it` / `gemma-4-e2b`, and `google/gemma-4-E4B-it` / `gemma-4-e4b`.
- Install `uv sync --locked --extra backend --extra model-management --extra gemma4`. The `gemma4` extra supplies Transformers 5.10.1 or later, PyTorch 2.4 or later, Accelerate, librosa, and torchvision.
- The optional `compose.gemma4.yaml` deployment builds the image with the `gemma4` and CTranslate2 extras. It omits OpenVINO because the Optimum Intel dependency set conflicts with Transformers 5.10.1 or later. The standard production image and UBX installer remain OpenVINO-based. The optional `compose.gemma4.microphone.yaml` adds host microphone access; the base Gemma Compose deployment supports the existing audio-upload flow without an audio device.
- Supported runtime: local Transformers only. E2B exposes `bf16` for the 16 GB target; E4B retains `bf16` and `float32`. Gemma 4 does not use the existing Whisper OpenVINO or CTranslate2 conversions.
- Only local model files are loaded at inference time, with `trust_remote_code=False`. Safetensors weights are required.
- Each audio input is mono 16 kHz and at most 30 seconds. Longer clips are handled by the existing workflow's 30-second chunking; the adapter rejects longer direct inputs.
- Previous-text conditioning and the OpenVINO speech filter are inactive for Transformers. Their saved settings remain dormant and the corresponding controls are disabled in the UI for Gemma.
- [Google's audio guide](https://ai.google.dev/gemma/docs/capabilities/audio?hl=en) lists the E2B checkpoint at about 10.2 GB and E4B at about 16 GB. [Google's overview](https://ai.google.dev/gemma/docs/core.md) estimates 11.4 GB GPU/TPU memory for E2B BF16 and 17.9 GB for E4B BF16 including overhead. These estimates are not guarantees for this app's CPU Transformers runtime or Intel GPU. E2B BF16 is the only advertised E2B precision and is still tight on a 16 GB system once the operating system and runtime are included. Allow additional disk for download staging/cache and additional RAM for runtime overhead. Target-host memory, latency, and recognition quality remain unverified.
- The application frontend sends model-management requests to its backend. The backend downloads Gemma source files into `/opt/models` and loads inference in its own process. Compose persists `/opt/models` and `/data`; the model is not supplied by a separate HTTP inference API.
- The optional Gemma Compose flavor does not configure GPU passthrough or an accelerator-specific PyTorch build. It uses devices visible to PyTorch in the container and normally runs on CPU; accelerator use and target-host performance are unverified.

## Upstream reference

Model cards: <https://huggingface.co/google/gemma-4-E2B-it> and <https://huggingface.co/google/gemma-4-E4B-it>. Google's [audio guide](https://ai.google.dev/gemma/docs/capabilities/audio?hl=en) documents multilingual speech recognition and a maximum audio length of 30 seconds for both. Transformers usage follows the [Gemma 4 Transformers guide](https://huggingface.co/docs/transformers/model_doc/gemma4).
