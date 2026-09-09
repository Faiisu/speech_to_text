"""Drive stations from recorded media instead of microphones.

Two jobs, one mechanism:

  * verifying the service on a machine with no Intel iGPU and no three
    microphones plugged in, and
  * the parallel benchmark, which needs N streams that are identical between
    runs — two live microphones are never the same input twice.

A replayed station is deliberately the *same* station as a live one from the
engine's point of view: same chunking, same silence gate, same queue, same
backpressure. A benchmark that bypassed the queue would measure something the
product never does.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

from stations.config import Station
from transcribe import SAMPLE_RATE, load_audio

# Containers soundfile can't read but ffmpeg can. Video is the point: the
# benchmark material is a folder of recordings, and those are usually .mp4.
FFMPEG_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4a", ".aac", ".wmv", ".mpg"}


class MediaError(RuntimeError):
    """A clip that can't be turned into 16kHz mono audio."""


def load_media(path: str | Path) -> np.ndarray:
    """Read any audio or video file as 16kHz mono float32.

    Plain 16kHz audio goes through the PoC's load_audio unchanged. Anything
    else — a video, a 44.1kHz wav, an mp3 — is converted by ffmpeg, because
    load_audio deliberately refuses to resample silently.
    """
    path = Path(path)
    if not path.exists():
        raise MediaError(f"No such file: {path}")
    if path.suffix.lower() not in FFMPEG_EXTENSIONS:
        try:
            return load_audio(str(path))
        except ValueError:
            pass  # wrong sample rate — fall through to ffmpeg
    return _extract_with_ffmpeg(path)


def _extract_with_ffmpeg(path: Path) -> np.ndarray:
    if not shutil.which("ffmpeg"):
        raise MediaError(
            f"{path.name} needs ffmpeg to decode (video, or not already 16kHz mono), "
            "and ffmpeg isn't on PATH. Install it, or convert the file first."
        )
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path),
             "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "wav", str(wav)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not wav.exists():
            raise MediaError(f"ffmpeg could not extract audio from {path.name}: {result.stderr.strip()}")
        return load_audio(str(wav))


class ReplayCapture:
    """Feeds a clip through the same chunking a live station uses.

    Interchangeable with StationCapture: same start/stop/running surface, so
    the supervisor and engine cannot tell the difference.
    """

    def __init__(
        self,
        station: Station,
        audio: np.ndarray,
        sink,
        *,
        realtime: bool = True,
        loop: bool = False,
        on_error=None,
        on_finish=None,
    ) -> None:
        self.station = station
        self._audio = audio
        self._sink = sink
        # Real time is what a microphone does, and the only pacing under which
        # "does it keep up?" means anything. Unpaced is for measuring raw
        # throughput, where the answer is allowed to be "no".
        self._realtime = realtime
        self._loop = loop
        self._on_error = on_error
        self._on_finish = on_finish
        self._chunking = station.chunking
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.chunks_emitted = 0
        self.stream_errors = 0
        self.started_at: float | None = None

    def _run(self) -> None:
        chunk_samples = self._chunking.chunk_samples
        step_samples = self._chunking.step_samples
        step_seconds = self._chunking.step_seconds
        try:
            self.started_at = time.perf_counter()
            offset = 0
            elapsed = 0.0
            while not self._stop.is_set():
                if offset + chunk_samples > self._audio.size:
                    if not self._loop:
                        break
                    offset = 0
                if self._realtime:
                    # A microphone can't hand over a chunk before the audio has
                    # happened. Wait until it would have.
                    due = self.started_at + elapsed + self._chunking.chunk_seconds
                    delay = due - time.perf_counter()
                    if delay > 0 and self._stop.wait(delay):
                        break
                chunk = self._audio[offset : offset + chunk_samples]
                self._sink(self.station, chunk, elapsed)
                self.chunks_emitted += 1
                offset += step_samples
                elapsed += step_seconds
        except Exception as exc:  # noqa: BLE001 - matches StationCapture
            if self._on_error:
                self._on_error(self.station, exc)
        finally:
            if self._on_finish:
                self._on_finish(self.station)

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"replay-{self.station.id}", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def buffered_seconds(self) -> float:
        return 0.0  # a file has no capture backlog
