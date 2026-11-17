from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


def _copy_frame(frame: Any) -> Any:
    copier = getattr(frame, "copy", None)
    return copier() if callable(copier) else frame


def _frame_nbytes(frame: Any) -> int:
    nbytes = getattr(frame, "nbytes", None)
    if isinstance(nbytes, int) and nbytes >= 0:
        return nbytes
    if isinstance(frame, (bytes, bytearray, memoryview)):
        return len(frame)
    return 0


@dataclass(frozen=True)
class EvidenceFrame:
    timestamp: Any
    frame: Any = field(repr=False)

    @property
    def image(self) -> Any:
        return self.frame

    @property
    def captured_at(self) -> Any:
        return self.timestamp


@dataclass(frozen=True)
class EvidenceClip:
    channel_id: str
    started_at: Any
    ended_at: Any
    frames: tuple[EvidenceFrame, ...]

    def __iter__(self) -> Iterable[EvidenceFrame]:
        return iter(self.frames)

    def __len__(self) -> int:
        return len(self.frames)


class EvidenceBuffer:
    """Bounded raw-frame history for one electronic-seal channel."""

    def __init__(
        self,
        channel_id: str,
        capacity: int = 150,
        max_bytes: int = 128 * 1024 * 1024,
    ):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.channel_id = str(channel_id)
        self.capacity = int(capacity)
        self.max_bytes = int(max_bytes)
        self._frames: deque[EvidenceFrame] = deque()
        self._bytes = 0
        self._lock = threading.RLock()

    def append(self, frame: Any, timestamp: Any) -> EvidenceFrame:
        item = EvidenceFrame(timestamp=timestamp, frame=_copy_frame(frame))
        item_bytes = _frame_nbytes(item.frame)
        with self._lock:
            self._frames.append(item)
            self._bytes += item_bytes
            while self._frames and (
                len(self._frames) > self.capacity or self._bytes > self.max_bytes
            ):
                evicted = self._frames.popleft()
                self._bytes -= _frame_nbytes(evicted.frame)
        return EvidenceFrame(timestamp=item.timestamp, frame=_copy_frame(item.frame))

    def publish(self, frame: Any, timestamp: Any) -> EvidenceFrame:
        return self.append(frame, timestamp)

    def size(self) -> int:
        with self._lock:
            return len(self._frames)

    def status(self) -> dict[str, Any]:
        with self._lock:
            timestamps = [item.timestamp for item in self._frames]
            return {
                "channel_id": self.channel_id,
                "capacity": self.capacity,
                "max_bytes": self.max_bytes,
                "size": len(self._frames),
                "bytes": self._bytes,
                "oldest_timestamp": timestamps[0] if timestamps else None,
                "newest_timestamp": timestamps[-1] if timestamps else None,
            }

    def get_status(self) -> dict[str, Any]:
        return self.status()

    def build_clip(
        self,
        started_at: Any,
        ended_at: Any,
        overlay_callback: Callable[[Any, Any], Any] | None = None,
    ) -> EvidenceClip:
        if started_at > ended_at:
            raise ValueError("started_at must not be after ended_at")
        with self._lock:
            selected = [
                item
                for item in self._frames
                if started_at <= item.timestamp <= ended_at
            ]

        output: list[EvidenceFrame] = []
        for item in selected:
            frame = _copy_frame(item.frame)
            if overlay_callback is not None:
                frame = overlay_callback(frame, item.timestamp)
                if frame is None:
                    raise ValueError("overlay_callback must return a frame")
            output.append(
                EvidenceFrame(timestamp=item.timestamp, frame=_copy_frame(frame))
            )
        return EvidenceClip(
            channel_id=self.channel_id,
            started_at=started_at,
            ended_at=ended_at,
            frames=tuple(output),
        )

    def __len__(self) -> int:
        return self.size()


__all__ = ["EvidenceBuffer", "EvidenceClip", "EvidenceFrame"]
