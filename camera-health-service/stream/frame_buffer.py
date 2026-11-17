from __future__ import annotations

import threading
import time
from typing import Any


class FrameBuffer:
    """Thread-safe latest-frame buffer for a single channel."""

    def __init__(self):
        self._condition = threading.Condition()
        self._frame: Any = None
        self._captured_at: float | None = None
        self._sequence = 0

    def put(self, frame: Any, captured_at: float | None = None) -> int:
        with self._condition:
            self._frame = frame
            self._captured_at = float(captured_at or time.time())
            self._sequence += 1
            self._condition.notify_all()
            return self._sequence

    def latest(self) -> tuple[Any, float | None, int]:
        with self._condition:
            frame = self._frame.copy() if hasattr(self._frame, "copy") else self._frame
            return frame, self._captured_at, self._sequence

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        captured_frame, captured_at, sequence = self.latest()
        current_time = float(now if now is not None else time.time())
        age_seconds = (
            None
            if captured_at is None
            else max(current_time - float(captured_at), 0.0)
        )
        return {
            "frame": captured_frame,
            "captured_at": captured_at,
            "sequence": sequence,
            "age_seconds": age_seconds,
        }

    def wait_for_frame(self, timeout: float = 1.0) -> tuple[Any, float | None, int]:
        deadline = time.monotonic() + max(float(timeout), 0.0)
        with self._condition:
            while self._frame is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            return self.latest()
