"""External runtime/audio doubles; no system chunking or scheduling is implemented here."""

from collections import deque
from io import BytesIO
from queue import Empty
from threading import Event
import wave

import numpy as np


def wav_bytes(samples, *, sample_rate=16000, channels=1):
    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(np.asarray(samples, dtype="<i2").tobytes())
    return output.getvalue()


class ScriptedRuntime:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.audio = []
        self.settings = []
        self.closed = False
        self.entered = Event()
        self.release = Event()
        self.release.set()

    def transcribe(self, audio, *, language, decoding_options):
        self.audio.append(np.asarray(audio).copy())
        self.settings.append((language, dict(decoding_options)))
        self.entered.set()
        if not self.release.wait(5):
            raise TimeoutError("Test runtime was not released")
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


class RuntimeFactory:
    def __init__(self, runtime):
        self.runtime = runtime
        self.configs = []

    def __call__(self, config):
        self.configs.append(dict(config))
        return self.runtime


class ManualAudioSource:
    sample_rate = 16000
    channels = 1

    def __init__(self):
        self.device = None
        self.started = False
        self.stopped = False

    def __call__(self, *, device, on_audio, on_error):
        self.device = device
        self.on_audio = on_audio
        self.on_error = on_error
        return self

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def push(self, samples):
        assert self.started and not self.stopped
        self.on_audio(np.asarray(samples, dtype=np.float32))


def collect_until_completed(session, *, timeout=3):
    events = []
    while True:
        try:
            event = session.result_queue.get(timeout=timeout)
        except Empty:
            raise AssertionError("Session did not emit its terminal completed event") from None
        assert isinstance(event, dict)
        assert isinstance(event["source_id"], str)
        assert event["type"] in {"transcript", "error", "completed"}
        if event["type"] == "transcript":
            assert isinstance(event["sequence"], int)
            assert isinstance(event["text"], str)
        elif event["type"] == "error":
            assert {"code", "message", "sequence", "fatal"} <= event.keys()
            assert isinstance(event["fatal"], bool)
        else:
            assert "last_sequence" in event
            assert event["status"] in {"completed", "stopped", "failed"}
        events.append(event)
        if event["type"] == "completed":
            return events


def texts(events):
    return [event["text"] for event in events if event["type"] == "transcript"]


def stop_and_close(handle, *sessions):
    for session in sessions:
        session.stop(timeout=3)
    handle.close()
