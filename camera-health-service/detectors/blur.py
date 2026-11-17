from __future__ import annotations

import cv2
import numpy as np

from detectors import DetectorResult


class BlurDetector:
    def __init__(self, thresholds: dict[str, float | int]):
        self.thresholds = thresholds

    def analyze(self, frame: np.ndarray | None) -> DetectorResult:
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            return DetectorResult(False, 0.0, "offline", quality_status="offline")
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        threshold = float(self.thresholds.get("min_blur_variance", 20.0))
        is_bad = variance < threshold
        return DetectorResult(
            is_bad=is_bad,
            score=min(variance / max(threshold, 1e-6), 1.0),
            reason="blur_detected" if is_bad else "normal_sharpness",
            metrics={"blur_variance": variance},
        )
