"""Live audio transport shares capture and never owns the workflow lifecycle."""

import threading
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from speech_to_text.backend.live_audio import stream_workflow_audio
from speech_to_text.features.model_deployment.live_audio import AudioMonitor


@pytest.fixture
def live():
    monitor = AudioMonitor()
    workflow = SimpleNamespace(subscribe_audio=monitor.subscribe, unsubscribe_audio=monitor.unsubscribe)
    record = SimpleNamespace(lock=threading.RLock(), kind='microphone', status='recording', workflow=workflow)
    app = FastAPI()
    app.state.backend_runtime = SimpleNamespace(get_run=lambda key: record if key == 'live' else None)
    app.add_api_websocket_route('/audio/{workflow_id}', stream_workflow_audio)
    with TestClient(app) as client:
        yield client, monitor, record
    monitor.close()


def test_audio_metadata_pcm_and_disconnect_leave_workflow_running(live):
    client, monitor, record = live
    with client.websocket_connect('/audio/live', headers={'origin': 'http://testserver'}) as ws:
        assert ws.receive_json() == {'sample_rate': 16000, 'channels': 1, 'format': 'pcm_s16le'}
        monitor.publish(np.full(160, 0.5))
        assert np.frombuffer(ws.receive_bytes(), dtype='<i2').tolist() == [16383] * 160
    assert record.status == 'recording'
    assert not monitor._listeners
    with client.websocket_connect('/audio/live', headers={'origin': 'http://testserver'}) as ws:
        ws.receive_json()
        monitor.close()
        assert ws.receive()['type'] == 'websocket.close'


@pytest.mark.parametrize('path,origin,status', [
    ('/audio/missing', 'http://testserver', 'recording'),
    ('/audio/live', 'http://attacker.example', 'recording'),
    ('/audio/live', 'http://testserver', 'stopped'),
    ('/audio/live', 'http://testserver', 'recovering'),
])
def test_unavailable_or_cross_origin_audio_is_rejected(live, path, origin, status):
    client, monitor, record = live
    record.status = status
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(path, headers={'origin': origin}):
            pass
    assert not monitor._listeners


def test_monitor_bounds_listeners_and_preserves_latest_packets():
    monitor = AudioMonitor()
    listeners = [monitor.subscribe() for _ in range(8)]
    with pytest.raises(ValueError):
        monitor.subscribe()
    for index in range(100):
        monitor.publish_packet(bytes([index]))
    assert [listeners[0].get_nowait() for _ in range(4)] == [bytes([i]) for i in range(96, 100)]
    monitor.close()
    assert listeners[0].get_nowait() is None


def test_recovery_closes_existing_subscription(live):
    client, monitor, record = live
    with client.websocket_connect('/audio/live', headers={'origin': 'http://testserver'}) as ws:
        ws.receive_json()
        record.status = 'recovering'
        assert ws.receive()['type'] == 'websocket.close'
    assert not monitor._listeners
