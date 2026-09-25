"""Thu âm push-to-talk và đo mức âm lượng."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Any

from ..compat import np, sd
from ..config import AUDIO_SAMPLE_RATE
from .constants import VOICE_METER_CEIL_DB, VOICE_METER_FLOOR_DB


def _audio_meter_level(audio_chunk: Any) -> float:
    if np is None or audio_chunk is None:
        return 0.0
    try:
        audio_array = np.asarray(audio_chunk, dtype=np.float32)
    except Exception:
        return 0.0
    if audio_array.size <= 0:
        return 0.0
    peak = float(np.max(np.abs(audio_array)))
    if peak <= 1e-5:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(audio_array))))
    rms_db = 20.0 * math.log10(max(rms, 1e-5))
    normalized_db = (rms_db - VOICE_METER_FLOOR_DB) / max(VOICE_METER_CEIL_DB - VOICE_METER_FLOOR_DB, 1.0)
    normalized_peak = min(1.0, peak * 1.75)
    return max(0.0, min(1.0, max(normalized_db, normalized_peak * 0.82)))


class PTTCaptureStream:
    """Keeps the microphone stream warm so PTT starts recording immediately."""

    def __init__(
        self,
        sample_rate: int = AUDIO_SAMPLE_RATE,
        chunk_duration: float = 0.05,
        preroll_ms: int = 220,
        max_record_seconds: int = 20,
    ) -> None:
        self.sample_rate = sample_rate
        self.chunk_duration = chunk_duration
        self.chunk_size = max(1, int(sample_rate * chunk_duration))
        self._max_chunks = max(1, int(max_record_seconds / chunk_duration))
        self._preroll_chunks = max(1, int(preroll_ms / (chunk_duration * 1000)))

        self._ring: deque[Any] = deque(maxlen=max(self._preroll_chunks * 4, 40))
        self._record_chunks: list[Any] = []
        self._lock = threading.Lock()
        self._stream: Any | None = None
        self._running = False
        self._recording = False
        self._meter_level = 0.0
        self._meter_updated_at = 0.0

    def start(self) -> bool:
        if np is None or sd is None:
            return False
        with self._lock:
            if self._running and self._stream is not None:
                return True

        try:
            def _on_audio(indata: Any, _frames: int, _time_info: Any, _status: Any) -> None:
                try:
                    chunk = np.array(indata, dtype=np.float32, copy=True)
                except Exception:
                    return
                live_level = _audio_meter_level(chunk)
                with self._lock:
                    if not self._running:
                        return
                    self._ring.append(chunk)
                    if live_level >= self._meter_level:
                        self._meter_level = (self._meter_level * 0.32) + (live_level * 0.68)
                    else:
                        self._meter_level = (self._meter_level * 0.84) + (live_level * 0.16)
                    self._meter_updated_at = time.perf_counter()
                    if self._recording:
                        if len(self._record_chunks) < self._max_chunks:
                            self._record_chunks.append(chunk)
                        else:
                            self._recording = False

            stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self.chunk_size,
                callback=_on_audio,
            )
            stream.start()
            with self._lock:
                self._stream = stream
                self._running = True
                self._recording = False
                self._record_chunks = []
                self._meter_level = 0.0
                self._meter_updated_at = 0.0
            return True
        except Exception:
            with self._lock:
                self._stream = None
                self._running = False
                self._recording = False
                self._record_chunks = []
                self._meter_level = 0.0
                self._meter_updated_at = 0.0
            return False

    def begin_recording(self) -> bool:
        with self._lock:
            if not self._running:
                return False
            preroll = list(self._ring)[-self._preroll_chunks:] if self._ring else []
            self._record_chunks = [chunk.copy() for chunk in preroll]
            self._recording = True
            return True

    def end_recording(self) -> Any | None:
        with self._lock:
            self._recording = False
            chunks = self._record_chunks
            self._record_chunks = []
        if not chunks:
            return None
        try:
            return np.concatenate(chunks, axis=0)
        except Exception:
            return None

    def stop(self) -> None:
        with self._lock:
            stream = self._stream
            self._stream = None
            self._running = False
            self._recording = False
            self._record_chunks = []
            self._meter_level = 0.0
            self._meter_updated_at = 0.0
            self._ring.clear()
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

    def current_level(self) -> float:
        with self._lock:
            if not self._running:
                return 0.0
            level = float(self._meter_level)
            updated_at = float(self._meter_updated_at or 0.0)
        if updated_at <= 0.0:
            return max(0.0, min(1.0, level))
        elapsed = max(0.0, time.perf_counter() - updated_at)
        decayed_level = max(0.0, level - (elapsed * 1.8))
        return max(0.0, min(1.0, decayed_level))
