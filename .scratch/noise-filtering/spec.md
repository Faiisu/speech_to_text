# Noise Filtering Workflow

## Purpose

Provide a reusable callable workflow that reads a bounded PCM16 WAV, applies a configurable Butterworth band-pass filter, and writes a separate filtered WAV for later transcription or listening. This is frequency filtering: it suppresses frequencies outside the selected band, but does not identify or remove noise that overlaps speech frequencies. It does not load a speech model or integrate with live microphone transcription.

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

- `features/noise_filtering/` owns filter configuration and sample transformation.
- `workflows/noise_filtering/` owns WAV validation, file writing, and output behavior.
- The Noise filtering page reuses the audio-recording library and accepts finalized recordings imported as WAV, MP3, or M4A. Filtering creates a separate saved WAV in that same library; it never replaces the source.
- The page workflow validates cutoff frequencies against the selected recording's Nyquist frequency, filters once, and returns bounded before/after spectrogram data computed from the exact PCM16 WAV files offered for playback. Spectrogram values use RMS channel aggregation (so opposite-phase stereo does not cancel), a shared -100 to 0 dBFS scale, and time/frequency coordinates.
- The page workflow does not load a speech model or start live transcription. Its backend endpoint is transport only; filtering, plotting, and saved-output orchestration belong to the noise-filtering workflow.
