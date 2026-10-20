# Noise Filtering Workflow

## Purpose

Provide a reusable callable workflow that reads a bounded PCM16 WAV, optionally applies stationary-noise spectral denoising, applies a configurable Butterworth band-pass filter, and writes a separate filtered WAV for later transcription or listening. The existing band-pass mode remains the default and only suppresses frequencies outside its selected band. Optional spectral denoising estimates a fixed noise profile from an explicitly supplied noise-only clip; it does not detect speech or adapt to changing noise. The standalone WAV workflow does not load a speech model. The transcription workflow can reuse the same filtering features through the integration below.

## Callable contract

`filter_wav(input_path, output_path, *, config=None) -> FilteredAudio`

Install the locked project environment with `uv sync --locked`, then filter a WAV with custom cutoffs and order:

```python
from speech_to_text.workflows.noise_filtering import filter_wav

result = filter_wav(
    "input.wav",
    "filtered.wav",
    config={"low_hz": 150, "high_hz": 6000, "order": 4},
)
print(result)
```

The result reports the output path, sample layout, duration, and applied filter settings.

- Inputs are local WAV paths. The source must be complete PCM signed 16-bit mono or stereo audio, sampled at 8–48 kHz, with non-empty audio no longer than 120 seconds. The selected filter also requires enough samples for its forward-backward filter padding; the minimum depends on filter order.
- Output is a PCM16 WAV preserving the input sample rate, channel count, frame count, and duration. The original input is never modified.
- The default band is 100–7000 Hz with a fourth-order, zero-phase forward-backward Butterworth filter. Both cutoff frequencies must be finite, positive, ordered, and below the input Nyquist frequency.
- At sample rates of 14 kHz or lower, the default 7000 Hz high cutoff is invalid; callers must choose a lower `high_hz` appropriate for that file's Nyquist frequency.
- Configuration is optional and accepts only `low_hz`, `high_hz`, and `order`; defaults are `100.0`, `7000.0`, and `4`. Order is an integer from 1 through 8.
- Output paths must not exist and must differ from the input path. Failures remove any partially written output.
- Success returns the output path, sample rate, channels, frame count, duration, and applied cutoff/order metadata.
- Invalid configuration or unsupported/truncated WAV input raises `ValueError` or `AudioInputError`. Filesystem failures propagate as `OSError`.

## Acceptance

- The public feature applies a stable SOS Butterworth band-pass independently to each channel without changing frame layout or rate.
- The workflow validates WAV input, enforces the 120-second bound, prevents overwrite, cleans partial output on failure, and returns output metadata.
- Tests show attenuation below and above the pass band, preservation of an in-band tone, stereo/duration integrity, silence handling, invalid configuration/input handling, source preservation, and no overwrite.
- Callers can use the workflow without loading a speech model.

## Saved-recording workflow and API

The workflow exposes `filter_recording(recording_service, recording_id, *, low_hz, high_hz, order=4)`. It snapshots a finalized completed or failed recording through `RecordingService.copy_audio_snapshot`, checks the actual WAV sample rate and Nyquist bound, filters to a temporary WAV, computes before/after plots from those WAV files, then imports the filtered WAV into the same recording library. The temporary directory is removed after the response is assembled. The caller can play or delete the result with the recording library's existing routes. A missing ID returns `None`; active or invalid recordings keep the audio-recording workflow's conflict/validation errors; invalid cutoffs raise `ValueError`; storage errors use the audio-recording workflow's unavailable error.

`POST /api/v1/noise-filtering/recordings` accepts `{ "recording_id": "…", "low_hz": 100, "high_hz": 7000, "order": 4 }`. Success returns the source and newly created recording snapshots, filter settings (including sample rate, channel count, Nyquist frequency, and dB limits), and `before` / `after` spectrogram objects. Each spectrogram has `times_seconds`, `frequencies_hz`, and a frequency-major `dbfs` matrix. Each matrix is reduced by mean-power aggregation to at most 192 frequency bands by 640 time bins. It uses 1024-sample Hann STFT windows (shorter for short clips), channel-mean power, a full-scale normalized PCM power reference, and the same fixed -100 to 0 dBFS range. An active source returns 409, unknown recording returns 404, invalid audio or cutoff returns 422, and unavailable storage returns 503. The output is saved as an ordinary WAV recording; the source is preserved byte-for-byte.

## Boundaries and non-goals

- `features/noise_filtering/` owns band-pass configuration, stationary-noise profile estimation, and sample transformation.
- `workflows/noise_filtering/` owns WAV validation, file writing, and output behavior.
- The Noise filtering page reuses the audio-recording library and accepts finalized recordings imported as WAV, MP3, or M4A. Filtering creates a separate saved WAV in that same library; it never replaces the source.
- The page workflow validates cutoff frequencies against the selected recording's Nyquist frequency, filters once, and returns bounded before/after spectrogram data computed from the exact PCM16 WAV files offered for playback. Spectrogram values use RMS channel aggregation (so opposite-phase stereo does not cancel), a shared -100 to 0 dBFS scale, and time/frequency coordinates.
- The page workflow does not load a speech model or start live transcription. Its backend endpoint is transport only; filtering, noise analysis, plotting, and saved-output orchestration belong to the noise-filtering workflow.

## Optional spectral denoising

The existing frequency filter remains the default: `filter_wav(..., denoise_enabled=False)` and `filter_recording(..., denoise_enabled=False)` preserve the previous band-pass-only output. When enabled, a caller supplies a WAV known to contain noise and no human speech. The workflow estimates a stationary noise spectrum from that clip, applies spectral denoising to the target first, then applies the existing band-pass filter. This order is fixed.

The public feature interface exposes `analyze_noise(samples, sample_rate) -> NoiseProfile` and `denoise_audio(samples, sample_rate, profile, *, config=None) -> float32 samples`; the WAV workflow exposes `analyze_noise_wav(path) -> NoiseProfile`. A profile contains its sample rate, duration, fixed FFT/window settings, and finite mean power per frequency bin. Inputs must be finite mono or stereo samples, and the noise profile sample rate must equal the target rate. No resampling or speech detection is performed. A noise-only clip must contain at least 1024 frames; target clips shorter than one FFT are zero-padded for processing and trimmed back to their original frame count.

The estimator uses a 1024-sample Hann STFT with 75% overlap and zero boundary extension. The profile reports its FFT size, overlap, and window. It averages power across time and independently across channels, so opposite-phase stereo does not cancel. Denoising uses power subtraction with a square-root gain mask. `config` accepts `subtraction_strength` (default 1.0, range 0–10), `gain_floor` (default 0.1, range greater than 0 through 1), and `smoothing_size` (default 3 bins and frames, positive integer). The default gain floor limits attenuation to 20 dB per bin. Gains never exceed 1.0; a silent noise profile leaves the target unchanged. ISTFT uses the same settings and output is trimmed to the source frame count. These conservative stationary-noise defaults do not guarantee speech restoration or remove noise that changes rapidly over time.

The page adds an OFF-by-default spectral-denoising toggle, a checklist of saved WAV noise sources, and an explicit analyze action. Only finalized saved WAV recordings are eligible as noise sources. Changing the selected source set clears the analyzed-ready state. Analysis is exposed as `POST /api/v1/noise-filtering/noise-profiles` with `{ "recording_id": "…" }` for the compatible single-source form or `{ "recording_ids": ["…"] }` for multiple sources; it returns bounded profile metadata and no spectrum array. Filtering accepts the optional fields `denoise_enabled` (default `false`) and either `noise_recording_id` or `noise_recording_ids`; enabling requires at least one noise recording ID. The process endpoint reads and analyzes those saved WAVs again rather than trusting client-provided spectral values. Results include an immutable settings snapshot with the noise sources and every algorithm default. Existing before/after plots and source/output audio remain available.

Noise filtering accepts one or more noise-only WAV sources, with a maximum of 8 sources per analysis or filtering request. The legacy singular `recording_id` analysis field and `noise_recording_id` filtering field remain supported. Plural requests use `recording_ids` and `noise_recording_ids`; a request must use exactly one form, and collections must be non-empty, contain unique non-empty IDs, and not exceed the limit. Each source is analyzed separately using the same fixed STFT settings. The combined profile is the per-frequency-bin maximum of the individual mean power profiles, independent of input order. This envelope covers distinct stationary noise types without giving longer clips more weight. It can suppress weak speech in frequency bins present in any selected noise source; the existing gain floor remains in effect. Do not concatenate WAVs, mix waveforms, run denoising sequentially, sum profiles, or duration-weight profile averages. Profiles must share a sample rate and the fixed FFT/window settings. Analysis processes sources one at a time to bound memory. Results snapshot all selected source IDs and filenames, source count, total source duration, and the `maximum` combination method. The UI allows selecting saved WAVs and batch-uploading WAVs, with a maximum of 8 selected sources and the existing 20 MiB per-file upload limit. Processing recomputes trusted saved profiles and requires the exact analyzed ID set and target sample rate.

Noise analysis enforces the same complete PCM16 WAV and 120-second bounds as filtering. Missing recordings return 404, active recordings return 409, invalid/truncated files and sample-rate mismatch return 422, and storage failures return 503. Analysis creates no recording or persistent profile. Failed processing creates no output recording and leaves source and noise recordings unchanged.

## Speech-to-text workflow integration

The transcription workflow exposes `normalize_noise_filter(value=None)` and `prepare_audio_preprocessor(config=None, recording_service=None)` through its public interface. The returned callable is picklable and frozen for a run. It accepts complete 16 kHz mono inference chunks and preserves their frame counts. It analyzes noise only at preparation, never for each callback or inference chunk. Both stages default off, preserving existing transcription behavior. Its canonical `noise_filter` configuration contains `bandpass_enabled` (false), `low_hz` (100.0), `high_hz` (7000.0), `order` (4), `denoise_enabled` (false), and `noise_recording_ids` ([]). Cutoffs satisfy `0 < low_hz < high_hz < 8000`; order is an integer 1–8. Spectral denoising requires 1–8 unique saved noise-only recordings.

Preparation copies each source sequentially through the recording service's stable snapshot boundary. It validates complete PCM16 WAVs up to 120 seconds, downmixes stereo, resamples noise to 16 kHz with polyphase anti-alias filtering when necessary, estimates noise power, and combines profiles with the existing maximum envelope. The prepared object holds its spectrum and source identities in memory; deleting or editing a saved noise WAV after startup does not change an active run or its recovery. A later new run revalidates the saved files. The standalone comparison page retains its native-rate contract and does not resample its noise inputs.

The processing order is `capture → downmix/resample → existing STT chunks → optional spectral denoising → optional bandpass → silence gate → inference`. Full and final partial chunks use the same preprocessing. Bandpass uses the existing zero-phase filter, padding final chunks shorter than 64 frames by edge extension and trimming to the original length. Filtering does not operate independently on each hardware callback, add another microphone capture, create saved filtered WAVs, or add a second chunk wait. Chunk boundaries remain independent STT inference boundaries. Processing requires extra CPU and may affect throughput; target-hardware timing and recognition accuracy require acoustic evaluation.

Both shared and spawned execution use the same callable snapshot, including RTSP capture. Model-deployment feature code only invokes a generic preprocessing callable; the transcription workflow owns the orchestration between features. Invalid preprocessing output or runtime filtering failures fail the operation instead of silently passing unfiltered audio. Raw live-monitor playback is tapped before inference-chunk filtering. The [backend API contract](../backend-transcription-api/spec.md) owns request fields, profile persistence, run snapshots, and browser configuration.
