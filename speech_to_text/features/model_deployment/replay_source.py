"""Paced PCM file source used through the microphone process boundary."""

from __future__ import annotations

import math
import threading
import time

from .audio import TARGET_RATE, read_clip


class PacedWavSource:
    """Replay a WAV through the streaming capture callback at audio pace.

    The topology starts the source after every input process is ready by calling
    ``start_stream``. The source emits float32 mono samples at ``TARGET_RATE``
    and exposes ``finished`` after its final callback has returned.
    """

    sample_rate = TARGET_RATE
    channels = 1

    def __init__(
        self,
        *,
        device=None,
        on_audio,
        on_error,
        path,
        loops=2,
        block_seconds=0.1,
    ):
        if device is not None:
            raise ValueError("PacedWavSource does not accept a microphone device")
        if isinstance(loops, bool) or not isinstance(loops, int) or loops < 1:
            raise ValueError("loops must be a positive integer")
        if (
            isinstance(block_seconds, bool)
            or not isinstance(block_seconds, (int, float))
            or not math.isfinite(block_seconds)
            or block_seconds <= 0
        ):
            raise ValueError("block_seconds must be finite and positive")
        self._on_audio = on_audio
        self._on_error = on_error
        self._audio = read_clip(path)
        self._loops = loops
        self._block_frames = max(1, int(round(block_seconds * TARGET_RATE)))
        self._begin = threading.Event()
        self._stop = threading.Event()
        self._finished = threading.Event()
        self._thread = None

    @property
    def finished(self):
        return self._finished.is_set()

    def start(self):
        if self._thread is not None:
            raise RuntimeError("PacedWavSource can only be started once")
        self._thread = threading.Thread(
            target=self._run, name="stt-paced-wav", daemon=True
        )
        self._thread.start()

    def start_stream(self):
        self._begin.set()

    def _run(self):
        try:
            if not self._begin.wait():
                return
            audio = self._audio
            started = time.perf_counter()
            emitted_frames = 0
            total_frames = audio.size * self._loops
            for _ in range(self._loops):
                for offset in range(0, audio.size, self._block_frames):
                    if self._stop.is_set():
                        return
                    chunk = audio[offset : offset + self._block_frames]
                    self._on_audio(chunk.copy())
                    emitted_frames += chunk.size
                    deadline = started + emitted_frames / TARGET_RATE
                    delay = deadline - time.perf_counter()
                    if delay > 0:
                        self._stop.wait(delay)
                # The loop boundary is intentionally not signaled to capture:
                # chunk assembly continues over the next loop without a flush.
            if emitted_frames < total_frames and not self._stop.is_set():
                raise RuntimeError("WAV replay ended before both loops were emitted")
        except Exception as exc:
            self._on_error(exc)
        finally:
            self._finished.set()

    def stop(self):
        self._stop.set()
        self._begin.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
