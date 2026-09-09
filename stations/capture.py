"""Per-station audio capture with constant memory.

The PoC's run_recording_session keeps every sample of a session
(transcribe.py) and rebuilds the whole buffer on each chunk. That is fine for
a recording that lasts minutes and ends with a full-clip reference transcript.
A station runs for weeks and never wants the full recording, so this keeps
only the audio still needed to cut the next chunk: memory is a function of the
chunk length, not of uptime.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from collections import deque
from contextlib import contextmanager
from urllib.parse import urlparse

import numpy as np
import sounddevice as sd

from stations.config import Station
from transcribe import SAMPLE_RATE

# Network sources a station can read from instead of a local microphone.
# RTSP is what CCTV speaks; the rest come free with the same ffmpeg pipe.
STREAM_SCHEMES = ("rtsp", "rtsps", "rtmp", "rtmps", "http", "https", "srt", "udp")

# How much audio to pull from the pipe at a time. Small enough that stopping
# is prompt, large enough not to syscall per sample.
STREAM_READ_SAMPLES = 4096

# Socket timeout handed to ffmpeg, in microseconds. Without it an unreachable
# camera leaves ffmpeg waiting indefinitely: a measurement hangs, and a live
# station sits with a thread alive and no audio, which the watchdog reads as
# healthy. Making ffmpeg give up turns both into an error that gets reported
# and retried.
STREAM_TIMEOUT_MICROSECONDS = 8_000_000


def is_stream_url(device: str) -> bool:
    """Is this station reading from the network rather than a sound card?"""
    return urlparse(device.strip()).scheme in STREAM_SCHEMES


class DeviceError(RuntimeError):
    """A configured microphone isn't there, or its name is ambiguous."""


def input_devices() -> list[dict]:
    """Every device that can capture, as {index, name, channels}."""
    return [
        {"index": i, "name": d["name"], "channels": d["max_input_channels"]}
        for i, d in enumerate(sd.query_devices())
        if d["max_input_channels"] > 0
    ]


def resolve_device(name: str) -> int:
    """Find the input device index for a configured device name.

    Exact match wins; otherwise a case-insensitive substring match, which must
    be unambiguous. An ambiguous name is an error rather than a first-match
    guess: picking the wrong one of two identical USB mics would label every
    detection with the wrong station, and nothing downstream could tell.
    """
    devices = input_devices()
    if not devices:
        raise DeviceError("No audio input devices at all — is a microphone connected?")

    exact = [d for d in devices if d["name"] == name]
    if len(exact) == 1:
        return exact[0]["index"]

    matches = [d for d in devices if name.lower() in d["name"].lower()]
    if len(matches) == 1:
        return matches[0]["index"]
    if not matches:
        available = ", ".join(repr(d["name"]) for d in devices)
        raise DeviceError(f"No input device matching {name!r}. Available: {available}")
    ambiguous = ", ".join(repr(d["name"]) for d in matches)
    raise DeviceError(
        f"Device name {name!r} matches {len(matches)} inputs ({ambiguous}). "
        "Use the full device name so the station can't bind to the wrong microphone."
    )


def _input_options(url: str) -> list[str]:
    """ffmpeg input options for this URL's scheme.

    -rtsp_transport belongs to the RTSP demuxer and ffmpeg *rejects the whole
    command* if it is passed for an http:// or srt:// input, so it cannot be
    applied unconditionally.
    """
    options = ["-timeout", str(STREAM_TIMEOUT_MICROSECONDS)]
    if urlparse(url.strip()).scheme in ("rtsp", "rtsps"):
        # TCP rather than the default UDP: cameras are usually across a switch
        # or a VPN, and UDP RTSP loses packets silently, which arrives as audio
        # that is subtly wrong rather than as an error.
        options = ["-rtsp_transport", "tcp", *options]
    return options


def _sample_stream(url: str, seconds: float) -> np.ndarray:
    """Grab a few seconds from a network stream, for measuring it.

    -t bounds it inside ffmpeg rather than relying on us to stop reading, so a
    camera that never stops talking cannot hang the request.
    """
    check_device(url)
    try:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error",
             *_input_options(url), "-t", str(seconds), "-i", url,
             "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
            capture_output=True,
            # ffmpeg's own timeout should fire first; this is the backstop for
            # the case where it doesn't, and it must not surface as a raw
            # TimeoutExpired traceback.
            timeout=seconds + STREAM_TIMEOUT_MICROSECONDS / 1e6 + 10,
        )
    except subprocess.TimeoutExpired:
        raise DeviceError(
            f"{url} did not respond. Check the address, that the camera is reachable "
            "from this machine, and that the credentials in the URL are right."
        ) from None
    if not result.stdout:
        detail = (result.stderr or b"").decode(errors="replace").strip()
        raise DeviceError(
            f"No audio from {url}: {detail.splitlines()[-1] if detail else 'stream gave nothing'}. "
            "Many cameras ship with audio disabled, or have no microphone at all."
        )
    return np.frombuffer(result.stdout, dtype="float32").copy()


def measure_noise(device: str, seconds: float = 5.0) -> dict:
    """Record a short sample and report how loud this microphone's room is.

    The silence gate compares a whole chunk's mean RMS against a threshold, so
    the useful question is "what does this microphone read when nobody is
    talking" — that is the number the threshold has to sit above. Per-frame
    percentiles come back too, because a steady hum and an occasional door
    slam need different thresholds and one overall average hides which you
    have.
    """
    frames = int(seconds * SAMPLE_RATE)
    if is_stream_url(device):
        audio = _sample_stream(device, seconds)
    else:
        index = resolve_device(device)
        recording = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1,
                           dtype="float32", device=index)
        sd.wait()
        audio = recording.reshape(-1)

    def dbfs(value: float) -> float | None:
        return round(float(20 * np.log10(value)), 1) if value > 0 else None

    overall = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
    # 100ms frames: short enough to separate a hum from a bang, long enough
    # that one sample of noise doesn't dominate.
    frame = SAMPLE_RATE // 10
    usable = audio[: audio.size - audio.size % frame] if audio.size >= frame else audio
    if usable.size >= frame:
        blocks = usable.reshape(-1, frame)
        levels = np.sqrt(np.mean(np.square(blocks), axis=1))
    else:
        levels = np.array([overall], dtype="float32")

    median = float(np.median(levels))
    p90 = float(np.percentile(levels, 90))
    loudest = float(levels.max())

    # Three times the measured floor is about +10dB of headroom: high enough
    # that room noise doesn't wake the model, low enough that quiet speech
    # still gets through.
    suggested = round(max(overall, median) * 3, 4)

    return {
        "device": device,
        "seconds": seconds,
        "rms": round(overall, 5),
        "dbfs": dbfs(overall),
        "median_rms": round(median, 5),
        "median_dbfs": dbfs(median),
        "p90_dbfs": dbfs(p90),
        "loudest_dbfs": dbfs(loudest),
        "peak_dbfs": dbfs(float(np.abs(audio).max()) if audio.size else 0.0),
        "suggested_threshold": suggested,
        "suggested_dbfs": dbfs(suggested),
        # A floor this high is not a threshold problem, and saying so beats
        # letting the operator raise the gate until real speech is dropped too.
        "noisy": overall > 0.05,
    }


def check_device(device: str) -> None:
    """Validate a station's source without opening it for real.

    Both kinds of device get checked the same way at the same points — saving
    config, starting a station, and the watchdog deciding whether to retry —
    so a network station is not quietly exempt from the checks a microphone
    gets.
    """
    if is_stream_url(device):
        if not shutil.which("ffmpeg"):
            raise DeviceError(
                f"{device} is a network stream, which needs ffmpeg to read. "
                "Install it:  sudo apt-get install -y ffmpeg"
            )
        return
    resolve_device(device)


class _RingBuffer:
    """Holds just enough recent audio to cut the next chunk.

    Blocks arrive from the PortAudio callback and are dropped from the front as
    soon as they're past the current chunk's start. Total retained samples stay
    below one chunk plus one in-flight block, whatever the uptime.
    """

    def __init__(self, chunk_samples: int) -> None:
        self._blocks: deque[np.ndarray] = deque()
        self._samples = 0  # retained samples across all blocks, minus _offset
        self._offset = 0  # consumed samples at the head of the first block
        self._chunk_samples = chunk_samples
        self._lock = threading.Lock()

    @property
    def available(self) -> int:
        with self._lock:
            return self._samples

    def add(self, block: np.ndarray) -> None:
        with self._lock:
            self._blocks.append(block)
            self._samples += block.size

    def take_chunk(self) -> np.ndarray | None:
        """Return the next full chunk without consuming it, or None."""
        with self._lock:
            if self._samples < self._chunk_samples:
                return None
            out = np.empty(self._chunk_samples, dtype="float32")
            filled = 0
            offset = self._offset
            for block in self._blocks:
                take = min(block.size - offset, self._chunk_samples - filled)
                out[filled : filled + take] = block[offset : offset + take]
                filled += take
                offset = 0
                if filled == self._chunk_samples:
                    break
            return out

    def advance(self, samples: int) -> None:
        """Discard `samples` from the front — this is what bounds memory."""
        with self._lock:
            self._samples -= samples
            remaining = samples
            while remaining and self._blocks:
                head = self._blocks[0]
                in_head = head.size - self._offset
                if in_head > remaining:
                    self._offset += remaining
                    remaining = 0
                else:
                    self._blocks.popleft()
                    self._offset = 0
                    remaining -= in_head


class _Capture:
    """Cuts a source's audio into chunks and hands them to a sink.

    The chunking lives here, once, so a microphone station and a network
    station cannot drift into behaving differently — the same failure the
    Chunking dataclass exists to prevent. Subclasses only supply audio: they
    open a source in `_source()` and push blocks into `self._buffer`.
    """

    def __init__(self, station: Station, sink, on_error=None) -> None:
        self.station = station
        self._sink = sink
        self._on_error = on_error
        self._chunking = station.chunking
        self._buffer = _RingBuffer(self._chunking.chunk_samples)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.started_at: float | None = None
        self.stream_errors = 0

    @contextmanager
    def _source(self):
        raise NotImplementedError

    def _source_alive(self) -> bool:
        """Is the source still capable of producing audio?

        A microphone stream raises when it fails; a subprocess just stops
        writing. Without this the chunk loop would wait for a chunk that can
        never arrive, with the thread alive — which the watchdog reads as a
        healthy station.
        """
        return True

    def _run(self) -> None:
        step_samples = self._chunking.step_samples
        elapsed_samples = 0
        try:
            with self._source():
                self.started_at = time.time()
                while not self._stop.is_set() and self._source_alive():
                    chunk = self._buffer.take_chunk()
                    if chunk is None:
                        # Sleep well under one step so a chunk is picked up
                        # promptly, without spinning a core per station.
                        self._stop.wait(0.05)
                        continue
                    self._sink(self.station, chunk, elapsed_samples / SAMPLE_RATE)
                    self._buffer.advance(step_samples)
                    elapsed_samples += step_samples
        except Exception as exc:  # noqa: BLE001 - one station's failure is not the service's
            if self._on_error:
                self._on_error(self.station, exc)

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"capture-{self.station.id}", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def buffered_seconds(self) -> float:
        return self._buffer.available / SAMPLE_RATE


class StationCapture(_Capture):
    """A station reading from a local microphone."""

    def __init__(self, station: Station, sink, on_error=None) -> None:
        super().__init__(station, sink, on_error)
        self._device_index: int | None = None

    # PortAudio calls this on its own high-priority thread: do as little as
    # possible here. Anything slow risks an input overflow and lost audio.
    def _callback(self, indata, frame_count, time_info, status) -> None:
        if status:
            self.stream_errors += 1
        self._buffer.add(indata.copy().reshape(-1))

    @contextmanager
    def _source(self):
        self._device_index = resolve_device(self.station.device)
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=self._callback,
            device=self._device_index,
        ):
            yield


class StreamCapture(_Capture):
    """A station reading from a network stream — a CCTV camera's RTSP feed.

    ffmpeg does the work: it speaks RTSP, decodes whatever codec the camera
    uses, downmixes to mono and resamples to the rate Whisper wants, and hands
    back raw float32 on a pipe. A dying pipe raises, which the supervisor's
    watchdog turns into a reconnect — the same path an unplugged USB mic takes.
    """

    def __init__(self, station: Station, sink, on_error=None) -> None:
        super().__init__(station, sink, on_error)
        self._process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None

    def _command(self) -> list[str]:
        return [
            "ffmpeg", "-nostdin", "-loglevel", "error",
            *_input_options(self.station.device),
            "-i", self.station.device,
            "-vn",                      # the video is not our business
            "-ac", "1",
            "-ar", str(SAMPLE_RATE),
            "-f", "f32le",              # the format _RingBuffer already holds
            "-",
        ]

    def _source_alive(self) -> bool:
        # Whatever is still buffered is worth cutting into chunks first; only
        # then is the dead process a reason to stop.
        if self._process is None or self._process.poll() is None:
            return True
        return self._buffer.available >= self._chunking.chunk_samples

    def _pump(self) -> None:
        """Move audio from the pipe into the ring buffer."""
        assert self._process and self._process.stdout
        width = np.dtype("float32").itemsize
        want = STREAM_READ_SAMPLES * width
        while not self._stop.is_set():
            data = self._process.stdout.read(want)
            if not data:
                break  # ffmpeg exited; _source() reports why
            self._buffer.add(np.frombuffer(data, dtype="float32").copy())

    @contextmanager
    def _source(self):
        check_device(self.station.device)
        self._process = subprocess.Popen(
            self._command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self._reader = threading.Thread(
            target=self._pump, name=f"rtsp-{self.station.id}", daemon=True
        )
        self._reader.start()
        try:
            yield
            # Falling out while the pipe is dead means ffmpeg quit on its own.
            # Its stderr is the only thing that says why, so it is the error.
            if self._process.poll() is not None and not self._stop.is_set():
                self.stream_errors += 1
                detail = (self._process.stderr.read() or b"").decode(errors="replace").strip()
                raise DeviceError(
                    f"stream ended: {detail.splitlines()[-1] if detail else 'ffmpeg exited'}"
                )
        finally:
            process, self._process = self._process, None
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            for pipe in (process.stdout, process.stderr) if process else ():
                if pipe:
                    pipe.close()
            if self._reader:
                self._reader.join(timeout=2)


def open_capture(station: Station, sink, on_error=None) -> _Capture:
    """The right capture for this station's source."""
    kind = StreamCapture if is_stream_url(station.device) else StationCapture
    return kind(station, sink, on_error)
