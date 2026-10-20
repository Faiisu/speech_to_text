# Add saved-recording filtering page

Status: wontfix

Legacy outcome: Implemented. The page reuses the audio-recording library for WAV, MP3, and M4A input; filtered output is retained as a separate recording and can be played beside the unchanged source. The API returns bounded spectrogram data generated from both PCM16 playback files.

## Scope

Add sidebar navigation and `/noise-filtering`. Let users upload or choose a saved clip, set band-pass cutoff frequencies valid for that clip, create a filtered WAV, and compare both audio and spectrograms on shared axes and a fixed dBFS scale. See the [noise-filtering specification](../spec.md) for the authoritative behavior.

## Acceptance checklist

- [x] Reuse saved recording listing and WAV/MP3/M4A upload.
- [x] Reject invalid cutoffs, including a high cutoff at or above recording Nyquist; allow lower Nyquist files with adjusted bounds.
- [x] Preserve source bytes and create a separately retained filtered recording with sample layout and duration intact.
- [x] Show actual bounded spectrograms computed from source and quantized filtered WAV, with RMS channel power aggregation and a shared -100–0 dBFS scale.
- [x] Play source and filtered recordings from the existing audio route; filtering does not load a model.
- [x] Cover workflow/API handling, spectrogram frequency content, upload/playback interaction, and source preservation.
