# 02. Finite clip normalization and transcription

Status: ready-for-agent
Execution: completed
Blocked by: 01

## Scope

Implement WAV path/bytes decoding, validation, stereo downmix, resampling to 16 kHz mono float32, bounded non-overlapping chunking, silence gating, ordered transcript assembly, runtime decoding settings, and skipped-chunk warnings using the model handle from ticket 01.

## Acceptance checklist

- [x] Accept PCM16 mono/stereo WAV at 8–48 kHz and reject malformed/unsupported inputs as `AudioInputError` before inference.
- [x] Validate all flow settings and supported greedy decoding options; reject unknown keys/options clearly.
- [x] Feed independent contiguous chunks, with an unpadded final partial chunk; do not overlap inference chunks.
- [x] Skip chunks under the configured RMS threshold and preserve successful non-empty transcripts in sequence order with single spaces.
- [x] Warn and continue after a failed inference chunk; returned transcript contains no diagnostics.
- [x] Pass language and decoding options to the selected runtime for each chunk.
- [x] Existing Feature 01 clip-contract tests pass.

## Comments

The public settings and error names are fixed by the Feature 01 executable acceptance contract in `../spec.md`.

Verification: `.venv/bin/python -m pytest tests/feature_01 -q` (70 passed, 2 hardware skips), including clip normalization, resampling, ordering, partial chunk, silence, errors, and decoder options.
