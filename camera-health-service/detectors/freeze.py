from __future__ import annotations

import cv2
import numpy as np

from detectors import DetectorResult


class FreezeDetector:
    def __init__(self, thresholds: dict[str, float | int]):
        self.thresholds = thresholds
        self._previous_gray: np.ndarray | None = None

    def reset(self) -> None:
        self._previous_gray = None

    def analyze(self, frame: np.ndarray | None) -> DetectorResult:
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            return DetectorResult(False, 0.0, "offline", quality_status="offline")
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        gray = cv2.resize(gray, (320, 180), interpolation=cv2.INTER_AREA)
        if self._previous_gray is None:
            self._previous_gray = gray.copy()
            return DetectorResult(False, 0.0, "freeze_reference_started")
        difference = float(cv2.absdiff(gray, self._previous_gray).mean() / 255.0)
        self._previous_gray = gray.copy()
        threshold = float(self.thresholds.get("max_difference", 0.001))
        is_bad = difference <= threshold
        return DetectorResult(
            is_bad=is_bad,
            score=max(0.0, 1.0 - difference / max(threshold, 1e-6)),
            reason="video_frozen" if is_bad else "video_changed",
            metrics={"frame_difference": difference},
        )
