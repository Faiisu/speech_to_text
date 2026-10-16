"""Deterministic contracts for the audio-only RTSP source adapter."""

from __future__ import annotations

import pickle
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from speech_to_text.features.model_deployment import (
    AudioInputError,
    ConfigurationError,
    create_rtsp_audio_source_factory,
)


class _Frame:
    def __init__(self, values):
        self.values = values

    def to_ndarray(self):
        return self.values


class _Container:
    def __init__(self, stream, frames):
        self.streams = [SimpleNamespace(type="video"), stream]
        self.frames = frames
        self.decoded_stream = None
        self.closed = False

    def decode(self, stream):
        self.decoded_stream = stream
        yield from self.frames

    def close(self):
        self.closed = True


def test_rtsp_source_is_picklable_and_decodes_first_audio_stream(monkeypatch):
    stream = SimpleNamespace(type="audio")
    container = _Container(stream, [_Frame(np.array([[0.25, -0.5]], dtype=np.float32))])
    calls = []
    fake_av = SimpleNamespace(
        open=lambda *args, **kwargs: calls.append((args, kwargs)) or container,
        AudioResampler=lambda **kwargs: SimpleNamespace(
            resample=lambda frame: [frame]
        ),
    )
    monkeypatch.setitem(__import__("sys").modules, "av", fake_av)
    url = "rtsp://camera.example:554/live"
    factory = create_rtsp_audio_source_factory(url)
    assert pickle.loads(pickle.dumps(factory)) == factory
    assert url not in repr(factory)

    received = []
    errors = []
    finished = threading.Event()
    source = factory(
        device=None,
        on_audio=lambda frames: (received.append(frames), finished.set()),
        on_error=lambda error: (errors.append(error), finished.set()),
    )
    source.start()

    assert finished.wait(2)
    source._thread.join(2)
    assert source.sample_rate == 16000
    assert source.channels == 1
    np.testing.assert_array_equal(received[0], np.array([0.25, -0.5], dtype=np.float32))
    assert container.decoded_stream is stream
    assert container.closed
    assert [str(error) for error in errors] == ["RTSP audio stream ended"]
    assert calls[0][1]["options"] == {"rtsp_transport": "tcp"}
    assert calls[0][1]["timeout"] == (5.0, 5.0)


def test_rtsp_source_sanitizes_decode_errors_and_closes_container(monkeypatch):
    secret = "rtsp://user:pass@camera.example:554/live?token=private"
    container = _Container(SimpleNamespace(type="audio"), [])

    def fail_open(*_args, **_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setitem(
        __import__("sys").modules,
        "av",
        SimpleNamespace(open=fail_open, AudioResampler=lambda **_kwargs: None),
    )
    factory = create_rtsp_audio_source_factory("rtsp://camera.example:554/live")
    errors = []
    done = threading.Event()
    source = factory(
        device=None,
        on_audio=lambda _frames: None,
        on_error=lambda error: (errors.append(error), done.set()),
    )
    with pytest.raises(AudioInputError) as captured:
        source.start()
    source._thread.join(2)
    assert str(captured.value) == "Unable to decode RTSP audio stream"
    assert secret not in str(captured.value)
    assert "camera.example" not in repr(source)


def test_rtsp_source_reports_missing_audio_and_stop_closes_container(monkeypatch):
    container = _Container(None, [])
    container.streams = [SimpleNamespace(type="video")]
    monkeypatch.setitem(
        __import__("sys").modules,
        "av",
        SimpleNamespace(
            open=lambda *_args, **_kwargs: container,
            AudioResampler=lambda **_kwargs: None,
        ),
    )
    source = create_rtsp_audio_source_factory("rtsps://camera.example:7443/live")(
        device=None, on_audio=lambda _frames: None, on_error=lambda _error: None
    )
    with pytest.raises(AudioInputError, match="no audio track"):
        source.start()
    source._thread.join(2)
    assert source.finished
    assert container.closed


@pytest.mark.parametrize(
    "url",
    [
        "https://camera.example:554/live",
        "rtsp:///live",
        "rtsp://user:pass@camera.example:554/live",
        "rtsp://camera.example:70000/live",
        "rtsp://camera.example:554/live#fragment",
    ],
)
def test_direct_rtsp_url_rejects_unsupported_or_credentialed_urls(url):
    with pytest.raises(ConfigurationError):
        create_rtsp_audio_source_factory(url)
