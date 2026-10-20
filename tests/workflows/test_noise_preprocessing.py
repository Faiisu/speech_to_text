"""Noise filtering snapshots are reusable across shared and spawned STT flows."""

import pickle
import shutil
import wave

import numpy as np
import pytest

from speech_to_text.workflows.transcribe_match_forward import (
    normalize_noise_filter, prepare_audio_preprocessor,
)


class SavedNoise:
    def __init__(self, files):
        self.files = files
        self.reads = []

    def copy_audio_snapshot(self, key, target):
        if key not in self.files:
            return None
        self.reads.append(key)
        shutil.copyfile(self.files[key], target)
        return {'filename': f'{key}.wav'}, 1.0


def write_noise(path, rate, frequency, *, stereo=False):
    audio = 0.1 * np.sin(2 * np.pi * frequency * np.arange(rate) / rate)
    if stereo:
        audio = np.column_stack((audio, audio))
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(2 if stereo else 1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes((audio * 32767).astype('<i2').tobytes())


def tone_amplitude(audio, frequency):
    vector = np.exp(-2j * np.pi * frequency * np.arange(len(audio)) / 16000)
    return abs(np.dot(audio, vector)) * 2 / len(audio)


def test_disabled_is_noop_and_defaults_are_independent():
    assert prepare_audio_preprocessor() is None
    first = normalize_noise_filter()
    first['noise_recording_ids'].append('changed')
    assert normalize_noise_filter()['noise_recording_ids'] == []


@pytest.mark.parametrize('config', [
    {'bandpass_enabled': 1}, {'denoise_enabled': 'yes'}, {'extra': True},
    {'low_hz': 7000, 'high_hz': 100}, {'high_hz': 8000}, {'low_hz': float('nan')},
    {'order': True}, {'order': 9}, {'denoise_enabled': True},
    {'noise_recording_ids': 'a'}, {'noise_recording_ids': ['a', 'a']},
    {'noise_recording_ids': ['']}, {'noise_recording_ids': ['x' * 129]},
])
def test_invalid_configuration_fails_before_capture(config):
    with pytest.raises(ValueError):
        prepare_audio_preprocessor(config)


def test_bandpass_reduces_outside_tones_and_handles_tiny_stop_tail():
    processor = prepare_audio_preprocessor({'bandpass_enabled': True, 'low_hz': 300, 'high_hz': 3000})
    t = np.arange(16000) / 16000
    audio = sum(0.2 * np.sin(2 * np.pi * f * t) for f in (50, 1000, 6000)).astype(np.float32)
    output = processor(audio, 16000)
    assert output.shape == audio.shape
    assert tone_amplitude(output, 50) < 0.01
    assert tone_amplitude(output, 6000) < 0.01
    assert tone_amplitude(output, 1000) > 0.18
    for size in (1, 2, 16):
        tail = processor(np.ones(size, dtype=np.float32), 16000)
        assert tail.shape == (size,) and np.isfinite(tail).all()


def test_multiple_noise_types_resample_once_and_snapshot_survives_deleted_sources(tmp_path):
    fan, machine = tmp_path / 'fan.wav', tmp_path / 'machine.wav'
    write_noise(fan, 48000, 600, stereo=True)
    write_noise(machine, 16000, 1800)
    library = SavedNoise({'fan': fan, 'machine': machine})
    processor = prepare_audio_preprocessor({'denoise_enabled': True, 'noise_recording_ids': ['fan', 'machine']}, library)
    assert processor.profile.sample_rate == 16000
    assert library.reads == ['fan', 'machine']
    fan.unlink()
    machine.unlink()
    processor = pickle.loads(pickle.dumps(processor))
    t = np.arange(32000) / 16000
    audio = (0.1 * np.sin(2 * np.pi * 600 * t) + 0.1 * np.sin(2 * np.pi * 1800 * t) + 0.3 * np.sin(2 * np.pi * 1100 * t)).astype(np.float32)
    output = processor(audio, 16000)
    assert tone_amplitude(output, 600) < 0.02
    assert tone_amplitude(output, 1800) < 0.02
    assert tone_amplitude(output, 1100) > 0.28
    assert library.reads == ['fan', 'machine']
    assert processor.noise_sources == [{'recording_id': 'fan', 'filename': 'fan.wav'}, {'recording_id': 'machine', 'filename': 'machine.wav'}]
    processor.settings['noise_recording_ids'].clear()
    assert len(processor.settings['noise_recording_ids']) == 2


def test_missing_noise_fails_without_starting_unfiltered_flow():
    with pytest.raises(ValueError, match='not found'):
        prepare_audio_preprocessor({'denoise_enabled': True, 'noise_recording_ids': ['missing']}, SavedNoise({}))


def test_service_delivers_filtered_audio_to_model_and_preserves_forwarding(monkeypatch):
    from speech_to_text.features.model_deployment import load_model
    from speech_to_text.workflows.transcribe_match_forward import TranscriptionService
    from speech_to_text.workflows.transcribe_match_forward import service as service_module
    from tests.feature_01.support import ManualAudioSource, RuntimeFactory, ScriptedRuntime

    runtime = ScriptedRuntime(['speech'])
    handle = load_model({}, runtime_factory=RuntimeFactory(runtime))
    monkeypatch.setattr(service_module, 'load_model', lambda _config: handle)
    source = ManualAudioSource()
    processor = prepare_audio_preprocessor({'bandpass_enabled': True, 'low_hz': 300, 'high_hz': 3000})
    service = TranscriptionService()
    delivered = []
    workflow = service.start_microphone(
        None, ['speech'], {'language': 'en', 'chunk_seconds': 0.1, 'silence_threshold': 0.05},
        audio_source_factory=source, audio_preprocessor=processor, forwarder=delivered.append,
    )
    try:
        t = np.arange(1600) / 16000
        audio = (0.3 * np.sin(2 * np.pi * 6000 * t) + 0.2 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
        source.push(audio)
        workflow.stop(timeout=3)
        workflow.wait(timeout=3)
        assert len(runtime.audio) == 1
        assert tone_amplitude(runtime.audio[0], 6000) < 0.01
        assert tone_amplitude(runtime.audio[0], 1000) > 0.18
        assert delivered[0]['transcript'] == 'speech'
        assert delivered[0]['matches'] == [{'keyword': 'speech', 'count': 1}]
    finally:
        service.close()
