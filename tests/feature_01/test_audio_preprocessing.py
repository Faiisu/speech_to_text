"""Caller-owned preprocessing runs on complete chunks before silence gating."""

import numpy as np
import pytest

from .support import ManualAudioSource, RuntimeFactory, ScriptedRuntime, collect_until_completed, stop_and_close, texts, wav_bytes


class ScaleAudio:
    def __init__(self, gain):
        self.gain = gain

    def __call__(self, audio, rate):
        assert rate == 16000
        return audio * self.gain


class InvalidAudio:
    def __call__(self, audio, rate):
        return np.zeros(len(audio) + 1)


@pytest.mark.parametrize('gain', [0.0, 0.5])
def test_clip_filters_before_gate_and_model_without_changing_frames(api, gain):
    runtime = ScriptedRuntime(['filtered'])
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    try:
        text = api.transcribe_clip(wav_bytes([10000] * 1600), handle, {'chunk_seconds': 0.1}, audio_preprocessor=ScaleAudio(gain))
        if gain == 0:
            assert text == '' and runtime.audio == []
        else:
            assert text == 'filtered'
            np.testing.assert_allclose(runtime.audio[0], 10000 / 32768 * gain, atol=1e-6)
            assert len(runtime.audio[0]) == 1600
    finally:
        handle.close()


@pytest.mark.parametrize('gain', [0.0, 0.5])
def test_shared_capture_filters_before_gate_while_monitor_stays_raw(api, gain):
    source = ManualAudioSource()
    runtime = ScriptedRuntime(['filtered'])
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    session = api.start_microphone_flow(model_handle=handle, flow_config={'chunk_seconds': 0.1}, audio_source_factory=source, audio_preprocessor=ScaleAudio(gain))
    listener = session.audio_monitor.subscribe()
    try:
        source.push(np.full(1600, 0.3, dtype=np.float32))
        np.testing.assert_allclose(np.frombuffer(listener.get(timeout=1), dtype='<i2') / 32767, 0.3, atol=1e-4)
        session.stop(timeout=3)
        assert texts(collect_until_completed(session)) == ([] if gain == 0 else ['filtered'])
        if gain:
            np.testing.assert_allclose(runtime.audio[0], 0.3 * gain, atol=1e-6)
    finally:
        stop_and_close(handle, session)


@pytest.mark.parametrize('frames', [400, 1600])
def test_invalid_processor_output_fails_capture_without_sending_raw_audio(api, frames):
    source = ManualAudioSource()
    runtime = ScriptedRuntime(['must not infer'])
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    session = api.start_microphone_flow(model_handle=handle, flow_config={'chunk_seconds': 0.1}, audio_source_factory=source, audio_preprocessor=InvalidAudio())
    try:
        source.push(np.full(frames, 0.3, dtype=np.float32))
        if frames < 1600:
            session.stop(timeout=3)
        events = collect_until_completed(session)
        assert any(event.get('code') == 'AUDIO_PREPROCESSING_FAILED' for event in events)
        assert not runtime.audio
        assert source.stopped
    finally:
        stop_and_close(handle, session)


class MeterRuntime:
    def transcribe(self, audio, **_):
        return f'{float(audio.mean()):.3f}:{len(audio)}'

    def close(self):
        pass


class MeterFactory:
    def __call__(self, _config):
        return MeterRuntime()


class ToneSource:
    sample_rate = 16000
    channels = 1

    def __init__(self, *, on_audio, **_):
        self.on_audio = on_audio

    def start(self):
        self.on_audio(np.full(2000, 0.3, dtype=np.float32))

    def stop(self):
        pass


@pytest.mark.parametrize('topology', ['shared-model', 'per-input-model'])
@pytest.mark.parametrize('gain', [0.0, 0.5])
def test_spawned_preprocessing_applies_before_gate_and_preserves_partial_tail(api, topology, gain):
    group = api.start_multiprocess_microphone_flows(
        ['test-source'], flow_config={'chunk_seconds': 0.1}, topology=topology,
        runtime_factory=MeterFactory(), audio_source_factory=ToneSource, audio_preprocessor=ScaleAudio(gain),
    )
    try:
        group.stop(timeout=10)
        events = collect_until_completed(group.sessions[0])
        assert texts(events) == ([] if gain == 0 else ['0.150:1600', '0.150:400'])
    finally:
        group.abort()
