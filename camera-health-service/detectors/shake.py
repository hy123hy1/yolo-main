from __future__ import annotations

from collections import deque
from typing import Any

import cv2
import numpy as np

from detectors import DetectorResult


class ShakeDetector:
    """Detect abrupt global frame movement between consecutive samples."""

    def __init__(self, thresholds: dict[str, float | int]):
        self.thresholds = thresholds
        self._previous_gray: np.ndarray | None = None
        self._window_size = max(int(thresholds.get("window_size", 5)), 1)
        self._min_bad_samples = max(
            int(thresholds.get("min_bad_samples", 3)),
            1,
        )
        self._bad_samples: deque[bool] = deque(maxlen=self._window_size)

    def reset(self) -> None:
        self._previous_gray = None
        self._bad_samples.clear()

    def analyze(self, frame: Any) -> DetectorResult:
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            self.reset()
            return DetectorResult(False, 0.0, "offline", quality_status="offline")

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        gray = cv2.resize(gray, (320, 180), interpolation=cv2.INTER_AREA)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        if self._previous_gray is None:
            self._previous_gray = gray.copy()
            return DetectorResult(
                False,
                0.0,
                "shake_reference_started",
                metrics={"shift_x": 0.0, "shift_y": 0.0, "shift_ratio": 0.0},
            )

        shift, response = cv2.phaseCorrelate(
            self._previous_gray.astype(np.float32),
            gray.astype(np.float32),
        )
        self._previous_gray = gray.copy()
        shift_x, shift_y = float(shift[0]), float(shift[1])
        diagonal = max(float(np.hypot(gray.shape[1], gray.shape[0])), 1.0)
        shift_ratio = float(np.hypot(shift_x, shift_y) / diagonal)
        min_response = float(self.thresholds.get("min_response", 0.1))
        max_shift_ratio = float(self.thresholds.get("max_shift_ratio", 0.02))
        sample_is_bad = response >= min_response and shift_ratio >= max_shift_ratio
        self._bad_samples.append(sample_is_bad)
        bad_sample_count = sum(self._bad_samples)
        is_bad = (
            len(self._bad_samples) >= self._window_size
            and bad_sample_count >= self._min_bad_samples
        )
        return DetectorResult(
            is_bad=is_bad,
            score=min(
                bad_sample_count / max(self._min_bad_samples, 1),
                1.0,
            ),
            reason="shake_detected" if is_bad else "stable_camera_motion",
            metrics={
                "shift_x": shift_x,
                "shift_y": shift_y,
                "shift_ratio": shift_ratio,
                "phase_response": float(response),
                "bad_sample_count": bad_sample_count,
                "sample_count": len(self._bad_samples),
            },
        )
