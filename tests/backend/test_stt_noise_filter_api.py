"""Direct STT API paths deliver filtered samples to the inference boundary."""

import json
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from speech_to_text.backend.app import create_app
from speech_to_text.backend.dependencies import BackendRuntime
from speech_to_text.backend.profile_store import SQLiteProfileStore
from speech_to_text.features.model_deployment import load_model
from speech_to_text.workflows.audio_recording import RecordingService
from speech_to_text.workflows.transcribe_match_forward import TranscriptionService
from speech_to_text.workflows.transcribe_match_forward import service as service_module
from tests.feature_01.support import ManualAudioSource, RuntimeFactory, wav_bytes


class Decoder:
    def __init__(self):
        self.audio = []

    def transcribe(self, audio, **_):
        self.audio.append(audio.copy())
        return 'speech'

    def close(self):
        pass


class CaptureService(TranscriptionService):
    def __init__(self):
        super().__init__()
        self.source = ManualAudioSource()
        self.preprocessors = []

    def start_microphone(self, *args, **kwargs):
        # Only the external hardware boundary is replaced, including RTSP.
        self.source = ManualAudioSource()
        self.preprocessors.append(kwargs.get('audio_preprocessor'))
        kwargs['audio_source_factory'] = self.source
        return super().start_microphone(*args, **kwargs)


@pytest.fixture
def stt(tmp_path, monkeypatch):
    decoder = Decoder()
    monkeypatch.setattr(service_module, 'load_model', lambda config: load_model(config, runtime_factory=RuntimeFactory(decoder)))
    service = CaptureService()
    runtime = BackendRuntime(
        workflow_service=service,
        recovery_initial_delay=0.01,
        recovery_max_delay=0.05,
        profile_store=SQLiteProfileStore(tmp_path / 'profiles.sqlite3'),
        recording_service=RecordingService(output_directory=tmp_path / 'recordings'),
    )
    with TestClient(create_app(runtime=runtime)) as client:
        yield client, service, decoder


def upload_noise(client):
    t = np.arange(16000) / 16000
    noise = (0.1 * np.sin(2 * np.pi * 600 * t) * 32767).astype('<i2')
    response = client.post('/api/v1/recordings/upload', files={'file': ('fan-noise.wav', wav_bytes(noise), 'audio/wav')})
    assert response.status_code == 201, response.text
    return response.json()['recording_id']


def target_audio():
    t = np.arange(16000) / 16000
    return (0.1 * np.sin(2 * np.pi * 600 * t) + 0.3 * np.sin(2 * np.pi * 1100 * t) + 0.2 * np.sin(2 * np.pi * 6000 * t)).astype(np.float32)


def assert_cleaned(audio):
    assert len(audio) == 16000
    def amplitude(frequency):
        return abs(np.dot(audio, np.exp(-2j * np.pi * frequency * np.arange(len(audio)) / 16000))) * 2 / len(audio)
    assert amplitude(600) < 0.025
    assert amplitude(6000) < 0.01
    assert amplitude(1100) > 0.28


def await_done(client, key):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        snapshot = client.get(f'/api/v1/transcriptions/{key}').json()
        if snapshot['status'] in ('completed', 'failed', 'stopped'):
            assert snapshot['status'] != 'failed', snapshot
            return snapshot
        time.sleep(0.01)
    pytest.fail('STT workflow did not complete')


@pytest.mark.parametrize('source', ['microphones', 'rtsp'])
def test_direct_live_api_freezes_profile_and_delivers_cleaned_audio(stt, source):
    client, service, decoder = stt
    noise_id = upload_noise(client)
    config = {'bandpass_enabled': True, 'low_hz': 300, 'high_hz': 3000, 'denoise_enabled': True, 'noise_recording_ids': [noise_id]}
    body = {'keywords': ['speech'], 'language': 'en', 'noise_filter': config}
    if source == 'rtsp':
        body['rtsp_url'] = 'rtsp://camera.example/live'
    response = client.post(f'/api/v1/transcriptions/{source}', json=body)
    assert response.status_code == 202, response.text
    key = response.json()['workflow_id']
    assert client.delete(f'/api/v1/recordings/{noise_id}').status_code == 204
    service.source.push(target_audio())
    assert client.post(f'/api/v1/transcriptions/{key}/stop').status_code == 202
    snapshot = await_done(client, key)
    assert snapshot['noise_filter']['noise_sources'] == [{'recording_id': noise_id, 'filename': 'fan-noise.wav'}]
    assert snapshot['matches'] == [{'keyword': 'speech', 'count': 1}]
    assert len(decoder.audio) == 1
    assert_cleaned(decoder.audio[0])


def test_direct_clip_api_applies_optional_json_filter_before_model(stt):
    client, _, decoder = stt
    noise_id = upload_noise(client)
    config = {'bandpass_enabled': True, 'low_hz': 300, 'high_hz': 3000, 'denoise_enabled': True, 'noise_recording_ids': [noise_id]}
    response = client.post('/api/v1/transcriptions/clips', data={
        'keywords': 'speech', 'language': 'en', 'noise_filter': json.dumps(config),
    }, files={'file': ('target.wav', wav_bytes((target_audio() * 32767).astype('<i2')), 'audio/wav')})
    assert response.status_code == 202, response.text
    snapshot = await_done(client, response.json()['workflow_id'])
    assert snapshot['noise_filter']['denoise_enabled'] is True
    assert snapshot['matches'] == [{'keyword': 'speech', 'count': 1}]
    assert_cleaned(decoder.audio[0])


@pytest.mark.parametrize('config', [
    {'high_hz': 9000}, {'denoise_enabled': True},
    {'denoise_enabled': True, 'noise_recording_ids': ['missing']},
])
def test_direct_api_rejects_invalid_filter_without_starting_capture(stt, config):
    client, service, decoder = stt
    response = client.post('/api/v1/transcriptions/microphones', json={'keywords': ['speech'], 'noise_filter': config})
    assert response.status_code == 422, response.text
    assert not service.source.started
    assert decoder.audio == []


def test_capture_recovery_reuses_prepared_noise_after_source_recording_is_deleted(stt):
    from speech_to_text.features.model_deployment import AudioInputError

    client, service, decoder = stt
    noise_id = upload_noise(client)
    config = {'bandpass_enabled': True, 'low_hz': 300, 'high_hz': 3000, 'denoise_enabled': True, 'noise_recording_ids': [noise_id]}
    response = client.post('/api/v1/transcriptions/microphones', json={'keywords': ['speech'], 'language': 'en', 'noise_filter': config})
    assert response.status_code == 202, response.text
    key = response.json()['workflow_id']
    assert client.delete(f'/api/v1/recordings/{noise_id}').status_code == 204
    service.source.on_error(AudioInputError('Simulated microphone disconnect'))
    deadline = time.monotonic() + 3
    while len(service.preprocessors) < 2 or not service.source.started:
        assert time.monotonic() < deadline, 'Microphone did not recover'
        time.sleep(0.01)
    assert service.preprocessors[0] is service.preprocessors[1]
    service.source.push(target_audio())
    client.post(f'/api/v1/transcriptions/{key}/stop')
    status = await_done(client, key)
    assert status['noise_filter']['noise_recording_ids'] == [noise_id]
    assert status['matches'] == [{'keyword': 'speech', 'count': 1}]
    assert_cleaned(decoder.audio[0])
