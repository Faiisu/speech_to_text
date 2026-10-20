# Apply band-pass filtering to a WAV file

Status: wontfix

## Acceptance criteria

- [x] A public callable workflow reads a bounded PCM16 WAV and writes a separate filtered WAV.
- [x] The default Butterworth band-pass is 100–7000 Hz, configurable with validated cutoff frequencies and order.
- [x] Output preserves sample rate, channels, frame count, and duration; input remains unchanged and an existing output is never overwritten.
- [x] Invalid audio/configuration fails clearly and partial output is cleaned up.
- [x] Focused tests cover passband behavior and the file contract.

Legacy outcome: Implemented as a reusable sample filter and standalone WAV workflow. All 9 focused tests pass; the combined noise-filtering, clip, and audio-recording regression check passes 39 tests. No HTTP or frontend entry point was added.

## Comments

The workflow filters frequency bands; it does not perform speech-aware denoising.
