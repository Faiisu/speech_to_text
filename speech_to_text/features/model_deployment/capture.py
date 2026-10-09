"""Real default-device and stable-name microphone capture adapter."""

from __future__ import annotations

import threading

from .errors import AudioInputError


class SoundDeviceSource:
    def __init__(self, *, device, on_audio, on_error):
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise AudioInputError("Microphone capture requires sounddevice and PortAudio") from exc
        self._sd = sd
        self._on_audio = on_audio
        self._on_error = on_error
        self._stream = None
        self._supervisor = None
        self.started = False
        try:
            if device is None:
                info = sd.query_devices(kind="input")
                self._device = None
            elif isinstance(device, str) and device.strip():
                matches = [(index, info) for index, info in enumerate(sd.query_devices())
                           if info.get("max_input_channels", 0) > 0 and info.get("name") == device]
                if not matches:
                    raise AudioInputError(f"No input microphone has the exact device name {device!r}")
                if len(matches) > 1:
                    raise AudioInputError(f"Microphone device name {device!r} is ambiguous; device names must uniquely identify the input")
                self._device, info = matches[0]
            else:
                raise AudioInputError("device must be omitted or a stable microphone device name")
            self.sample_rate = int(round(info["default_samplerate"]))
            self.channels = 1
            if self.sample_rate < 8000 or self.sample_rate > 48000:
                raise AudioInputError(f"Microphone default rate {self.sample_rate} is outside the supported 8–48 kHz range")
        except AudioInputError:
            raise
        except Exception as exc:
            raise AudioInputError(f"Unable to resolve microphone device {device!r}: {exc}") from exc

    def _callback(self, frames, frame_count, time_info, status):
        if status:
            self._notify_error(AudioInputError(str(status)))
            return
        try:
            self._on_audio(frames.copy())
        except Exception as exc:
            self._notify_error(exc)

    def _notify_error(self, exc):
        if self._supervisor is None or not self._supervisor.is_alive():
            self._supervisor = threading.Thread(target=self._on_error, args=(exc,), daemon=True)
            self._supervisor.start()

    def start(self):
        try:
            self._stream = self._sd.InputStream(device=self._device, samplerate=self.sample_rate,
                channels=self.channels, dtype="float32", callback=self._callback, blocksize=0)
            self._stream.start()
            self.started = True
        except Exception as exc:
            raise AudioInputError(f"Unable to start microphone capture: {exc}") from exc

    def stop(self):
        stream, self._stream = self._stream, None
        self.started = False
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
