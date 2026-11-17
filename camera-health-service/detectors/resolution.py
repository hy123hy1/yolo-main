from __future__ import annotations

from typing import Any

import numpy as np

from detectors import DetectorResult


class ResolutionDetector:
    """Check decoded frame dimensions against the configured stream dimensions."""

    def __init__(self, thresholds: dict[str, float | int]):
        self.thresholds = thresholds

    def analyze(self, frame: Any) -> DetectorResult:
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            return DetectorResult(False, 0.0, "offline", quality_status="offline")

        actual_height, actual_width = frame.shape[:2]
        expected_width = int(self.thresholds.get("expected_width", 0))
        expected_height = int(self.thresholds.get("expected_height", 0))
        tolerance = int(self.thresholds.get("tolerance_pixels", 0))
        width_bad = expected_width > 0 and abs(actual_width - expected_width) > tolerance
        height_bad = expected_height > 0 and abs(actual_height - expected_height) > tolerance
        is_bad = width_bad or height_bad
        expected = (
            f"{expected_width}x{expected_height}"
            if expected_width > 0 and expected_height > 0
            else None
        )
        return DetectorResult(
            is_bad=is_bad,
            score=1.0 if is_bad else 0.0,
            reason="resolution_mismatch" if is_bad else "resolution_normal",
            metrics={
                "actual_width": actual_width,
                "actual_height": actual_height,
                "expected_resolution": expected,
                "tolerance_pixels": tolerance,
            },
        )
