# Add optional spectral denoising

Status: wontfix

## Scope

Add an opt-in stationary-noise denoising step to the saved-recording noise filtering workflow. The authoritative algorithm, defaults, callable interfaces, API shape, and validation behavior are in the [noise-filtering specification](../spec.md#optional-spectral-denoising).

## Acceptance checklist

- [x] A public WAV workflow analyzes an explicitly noise-only PCM16 WAV and returns a finite spectral noise profile.
- [x] The feature denoises mono or stereo samples without channel cancellation, amplification, non-finite output, or sample-layout changes.
- [x] Denoising is off by default and runs before the existing band-pass only when explicitly enabled with a same-rate noise source.
- [x] The page can upload or select a noise-only WAV, analyze it, and requires a ready matching profile before denoising.
- [x] The API validates the saved noise source and returns an immutable settings snapshot; source/noise files remain unchanged and no profile artifact is persisted.
- [x] Focused feature, workflow, API, and browser tests cover attenuation, speech-like tone preservation, rate mismatch, finite short/silent behavior, source preservation, and the opt-in page flow.

Legacy outcome: Implemented as optional stationary spectral gating before band-pass filtering. Focused Python tests, frontend build, and the noise-filtering browser flow pass.
