from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np


@dataclass(frozen=True)
class BaselineQualityResult:
    accepted: bool
    reason: str
    metrics: dict[str, float | int]


class BaselineQualityValidator:
    """Validate that a frame is suitable for use as a camera baseline."""

    def __init__(
        self,
        *,
        expected_width: int,
        expected_height: int,
        low_brightness: float,
        high_brightness: float,
        max_extreme_ratio: float,
        min_blur_variance: float,
        min_frame_difference: float,
        max_frame_difference: float,
    ) -> None:
        self.expected_width = int(expected_width)
        self.expected_height = int(expected_height)
        self.low_brightness = float(low_brightness)
        self.high_brightness = float(high_brightness)
        self.max_extreme_ratio = float(max_extreme_ratio)
        self.min_blur_variance = float(min_blur_variance)
        self.min_frame_difference = float(min_frame_difference)
        self.max_frame_difference = float(max_frame_difference)

    def inspect(
        self,
        frame: Any,
        *,
        previous_frame: Any | None = None,
    ) -> BaselineQualityResult:
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            return BaselineQualityResult(False, "empty_frame", {})
        height, width = frame.shape[:2]
        metrics: dict[str, float | int] = {
            "width": int(width),
            "height": int(height),
        }
        if width != self.expected_width or height != self.expected_height:
            metrics.update(
                {
                    "expected_width": self.expected_width,
                    "expected_height": self.expected_height,
                }
            )
            return BaselineQualityResult(False, "resolution_mismatch", metrics)

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        brightness_mean = float(gray.mean())
        extreme_ratio = float(
            np.logical_or(
                gray <= self.low_brightness,
                gray >= self.high_brightness,
            ).mean()
        )
        blur_variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        metrics.update(
            {
                "brightness_mean": brightness_mean,
                "extreme_ratio": extreme_ratio,
                "blur_variance": blur_variance,
            }
        )
        if (
            brightness_mean <= self.low_brightness
            and extreme_ratio >= self.max_extreme_ratio
        ):
            return BaselineQualityResult(False, "underexposed", metrics)
        if (
            brightness_mean >= self.high_brightness
            and extreme_ratio >= self.max_extreme_ratio
        ):
            return BaselineQualityResult(False, "overexposed", metrics)
        if blur_variance < self.min_blur_variance:
            return BaselineQualityResult(False, "blurred", metrics)

        if (
            previous_frame is not None
            and isinstance(previous_frame, np.ndarray)
            and previous_frame.size
        ):
            previous_gray = (
                cv2.cvtColor(previous_frame, cv2.COLOR_BGR2GRAY)
                if previous_frame.ndim == 3
                else previous_frame
            )
            if previous_gray.shape != gray.shape:
                previous_gray = cv2.resize(
                    previous_gray,
                    (gray.shape[1], gray.shape[0]),
                    interpolation=cv2.INTER_AREA,
                )
            frame_difference = float(
                cv2.absdiff(gray, previous_gray).mean() / 255.0
            )
            metrics["frame_difference"] = frame_difference
            if frame_difference <= self.min_frame_difference:
                return BaselineQualityResult(False, "frame_frozen", metrics)
            if frame_difference > self.max_frame_difference:
                return BaselineQualityResult(False, "frame_unstable", metrics)

        return BaselineQualityResult(True, "accepted", metrics)

    def select_best(self, frames: list[np.ndarray]) -> np.ndarray:
        if not frames:
            raise ValueError("at least one baseline candidate is required")
        ranked: list[tuple[float, np.ndarray]] = []
        for frame in frames:
            result = self.inspect(frame)
            if result.accepted:
                ranked.append((float(result.metrics["blur_variance"]), frame))
        if not ranked:
            raise ValueError("no acceptable baseline candidate")
        return max(ranked, key=lambda item: item[0])[1]
