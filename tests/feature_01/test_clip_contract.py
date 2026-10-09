"""Finite WAV input normalization, transcript assembly, and chunk failure policy."""

import numpy as np
import pytest

from .support import RuntimeFactory, ScriptedRuntime, wav_bytes


@pytest.mark.parametrize("input_kind", ["bytes", "path"])
def test_clip_preserves_chunk_order_without_overlap_and_flushes_remainder(api, tmp_path, input_kind):
    samples = [10000] * 1600 + [-10000] * 1600 + [3000] * 800
    clip = wav_bytes(samples)
    if input_kind == "path":
        clip_path = tmp_path / "reference.wav"
        clip_path.write_bytes(clip)
        clip = clip_path
    runtime = ScriptedRuntime(["สวัสดี", "ขอบคุณ", "ครับ"])
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    try:
        assert api.transcribe_clip(clip, handle, {"chunk_seconds": 0.1}) == "สวัสดี ขอบคุณ ครับ"
        assert [len(audio) for audio in runtime.audio] == [1600, 1600, 800]
        assert np.allclose(runtime.audio[0], 10000 / 32768)
        assert np.allclose(runtime.audio[1], -10000 / 32768)
        assert np.allclose(runtime.audio[2], 3000 / 32768)
    finally:
        handle.close()


@pytest.mark.parametrize("sample_rate", [8000, 16000, 44100, 48000])
def test_stereo_clip_is_downmixed_and_resampled_before_inference(api, sample_rate):
    stereo = np.tile([12000, 4000], (sample_rate, 1))
    runtime = ScriptedRuntime(["normalized"])
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    try:
        assert api.transcribe_clip(wav_bytes(stereo, sample_rate=sample_rate, channels=2), handle, {}) == "normalized"
        assert runtime.audio[0].shape == (16000,)
        assert runtime.audio[0].dtype == np.float32
        assert np.mean(runtime.audio[0][100:-100]) == pytest.approx(8000 / 32768, abs=0.002)
    finally:
        handle.close()


def test_failed_chunk_is_reported_and_later_text_is_kept(api):
    runtime = ScriptedRuntime(["first", RuntimeError("decode failed"), "third"])
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    try:
        with pytest.warns(api.ChunkInferenceWarning, match="decode failed"):
            result = api.transcribe_clip(wav_bytes([10000] * 4800), handle, {"chunk_seconds": 0.1})
        assert result == "first third"
        assert "decode failed" not in result
    finally:
        handle.close()


def test_silence_gate_skips_quiet_audio_without_inventing_text(api):
    runtime = ScriptedRuntime(["speech"])
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    try:
        result = api.transcribe_clip(wav_bytes([0] * 1600 + [10000] * 1600), handle, {"chunk_seconds": 0.1})
        assert result == "speech"
        assert len(runtime.audio) == 1
    finally:
        handle.close()


def test_language_and_decoding_options_reach_selected_runtime(api):
    runtime = ScriptedRuntime(["hello"])
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    try:
        api.transcribe_clip(wav_bytes([10000] * 1600), handle, {"language": "en", "decoding_options": {"beam_size": 1, "temperature": 0.0}})
        assert runtime.settings[0][0] == "en"
        assert runtime.settings[0][1]["beam_size"] == 1
        assert runtime.settings[0][1]["temperature"] == 0.0
    finally:
        handle.close()


@pytest.mark.parametrize("config", [
    {"chunk_seconds": 0}, {"chunk_seconds": 31},
    {"silence_threshold": -0.1}, {"silence_threshold": 1.0},
    {"unknown": True}, {"overlap_seconds": 0.1},
    {"decoding_options": {"unsupported_option": True}},
])
def test_invalid_flow_settings_are_rejected_without_inference(api, config):
    runtime = ScriptedRuntime([])
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    try:
        with pytest.raises(api.ConfigurationError):
            api.transcribe_clip(wav_bytes([10000] * 1600), handle, config)
        assert runtime.audio == []
    finally:
        handle.close()


def test_malformed_audio_fails_before_inference(api):
    runtime = ScriptedRuntime([])
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    try:
        with pytest.raises(api.AudioInputError):
            api.transcribe_clip(b"not a WAV file", handle, {})
        assert runtime.audio == []
    finally:
        handle.close()
