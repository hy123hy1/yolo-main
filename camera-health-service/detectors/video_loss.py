from __future__ import annotations

import time
from typing import Any

from detectors import DetectorResult


class VideoLossDetector:
    def __init__(self, thresholds: dict[str, float | int] | None = None):
        self.thresholds = thresholds or {}

    def analyze(
        self,
        frame: Any,
        *,
        captured_at: float | None = None,
        now: float | None = None,
    ) -> DetectorResult:
        if frame is None:
            return DetectorResult(
                is_bad=False,
                score=1.0,
                reason="video_loss",
                metrics={"stale_seconds": None},
                quality_status="offline",
            )
        stale_seconds = 0.0
        if captured_at is not None:
            stale_seconds = max(
                float(now if now is not None else time.time()) - float(captured_at),
                0.0,
            )
        threshold = float(self.thresholds.get("max_stale_seconds", 5.0))
        if stale_seconds > threshold:
            return DetectorResult(
                is_bad=False,
                score=1.0,
                reason="video_loss",
                metrics={"stale_seconds": stale_seconds},
                quality_status="offline",
            )
        return DetectorResult(
            is_bad=False,
            score=0.0,
            reason="video_available",
            metrics={"stale_seconds": stale_seconds},
        )
