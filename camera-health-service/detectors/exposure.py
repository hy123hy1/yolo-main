from __future__ import annotations

import cv2
import numpy as np

from detectors import DetectorResult


class ExposureDetector:
    def __init__(self, thresholds: dict[str, float | int]):
        self.thresholds = thresholds

    def analyze(self, frame: np.ndarray | None) -> DetectorResult:
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            return DetectorResult(False, 0.0, "offline", quality_status="offline")
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        mean = float(gray.mean())
        low = float(self.thresholds.get("low_brightness", 12.0))
        high = float(self.thresholds.get("high_brightness", 245.0))
        extreme_ratio = float(
            (np.logical_or(gray <= low, gray >= high)).mean()
        )
        min_ratio = float(self.thresholds.get("min_extreme_ratio", 0.7))
        is_bad = (mean <= low or mean >= high) and extreme_ratio >= min_ratio
        reason = "underexposed" if mean <= low else "overexposed" if mean >= high else "normal_exposure"
        return DetectorResult(
            is_bad=is_bad,
            score=extreme_ratio,
            reason=reason if is_bad else "normal_exposure",
            metrics={
                "brightness_mean": mean,
                "extreme_ratio": extreme_ratio,
                "low_brightness": low,
                "high_brightness": high,
            },
        )
