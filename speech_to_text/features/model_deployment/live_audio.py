"""Bounded, transient 16 kHz mono PCM monitoring of an existing capture."""

from queue import Empty, Full, Queue
import threading

import numpy as np


class AudioMonitor:
    """Offer recent audio to at most eight listeners; never wait for playback."""

    def __init__(self, sink=None, *, max_listeners=8, max_packets=4):
        for value in (max_listeners, max_packets):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("Audio monitor capacities must be positive integers")
        self._max_listeners = max_listeners
        self._max_packets = max_packets
        self._lock = threading.Lock()
        self._listeners = set()
        self._closed = False
        self._sink = sink

    def subscribe(self):
        with self._lock:
            if self._closed or len(self._listeners) >= self._max_listeners:
                raise ValueError("Live audio is unavailable or has too many listeners")
            listener = Queue(maxsize=self._max_packets)
            self._listeners.add(listener)
            return listener

    def unsubscribe(self, listener):
        with self._lock:
            self._listeners.discard(listener)

    @staticmethod
    def offer(queue, packet):
        try:
            queue.put_nowait(packet)
        except Full:
            try:
                queue.get_nowait()
            except Empty:
                pass
            try:
                queue.put_nowait(packet)
            except Full:
                pass

    def publish(self, frames):
        with self._lock:
            if self._closed or (not self._listeners and self._sink is None):
                return
        pcm = (np.clip(np.asarray(frames), -1, 1) * 32767).astype('<i2').tobytes()
        # Bound packet size independently of the capture adapter's callback size.
        for offset in range(0, len(pcm), 3200):
            self.publish_packet(pcm[offset:offset + 3200])

    def publish_packet(self, packet):
        with self._lock:
            if self._closed:
                return
            listeners = tuple(self._listeners)
        if self._sink is not None:
            try:
                self._sink.put_nowait(packet)
            except Full:
                pass
        for listener in listeners:
            self.offer(listener, packet)

    def close(self):
        with self._lock:
            self._closed = True
            listeners, self._listeners = self._listeners, set()
        for listener in listeners:
            self.offer(listener, None)
