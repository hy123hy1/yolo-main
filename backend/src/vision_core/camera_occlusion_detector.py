from __future__ import annotations

import cv2
import numpy as np

from src.services.camera_health_types import DetectorResult


class CameraOcclusionDetector:
    def __init__(self, thresholds: dict[str, float | int]):
        self.thresholds = thresholds

    @staticmethod
    def classify_no_frame() -> str:
        return "offline"

    @staticmethod
    def _entropy(gray: np.ndarray) -> float:
        histogram = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel()
        probabilities = histogram / max(float(histogram.sum()), 1.0)
        probabilities = probabilities[probabilities > 0]
        return float(-(probabilities * np.log2(probabilities)).sum())

    @staticmethod
    def _uniform_ratio(gray: np.ndarray) -> float:
        histogram = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel()
        return float(histogram.max() / max(float(gray.size), 1.0))

    @staticmethod
    def _baseline_difference(gray: np.ndarray, baseline: np.ndarray | None) -> float:
        if baseline is None:
            return 0.0
        baseline_gray = cv2.cvtColor(baseline, cv2.COLOR_BGR2GRAY) if baseline.ndim == 3 else baseline
        baseline_gray = cv2.resize(baseline_gray, (gray.shape[1], gray.shape[0]))
        difference = cv2.absdiff(gray, baseline_gray)
        return float(difference.mean() / 255.0)

    def _local_occlusion_ratio(self, gray: np.ndarray) -> float:
        rows, cols = 3, 3
        bad_cells = 0
        for row in np.array_split(gray, rows, axis=0):
            for cell in np.array_split(row, cols, axis=1):
                edge_density = float((cv2.Canny(cell, 50, 150) > 0).mean())
                uniform_ratio = self._uniform_ratio(cell)
                if edge_density < float(self.thresholds["min_edge_density"]) and uniform_ratio > 0.8:
                    bad_cells += 1
        return float(bad_cells / (rows * cols))

    def analyze(self, frame: np.ndarray, baseline: np.ndarray | None) -> DetectorResult:
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            return DetectorResult(False, 0.0, self.classify_no_frame(), {})

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        brightness_mean = float(gray.mean())
        brightness_std = float(gray.std())
        entropy = self._entropy(gray)
        edge_density = float((cv2.Canny(gray, 50, 150) > 0).mean())
        blur_variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        uniform_ratio = self._uniform_ratio(gray)
        baseline_difference = self._baseline_difference(gray, baseline)
        local_occlusion_ratio = self._local_occlusion_ratio(gray)

        signals = [
            brightness_mean < float(self.thresholds["low_brightness"])
            or brightness_mean > float(self.thresholds["high_brightness"]),
            entropy < float(self.thresholds["min_entropy"]),
            edge_density < float(self.thresholds["min_edge_density"]),
            blur_variance < float(self.thresholds["min_blur_variance"]),
            uniform_ratio > float(self.thresholds["max_uniform_ratio"]),
            baseline is not None
            and baseline_difference > float(self.thresholds["max_baseline_difference"]),
            local_occlusion_ratio > float(self.thresholds["local_occlusion_ratio"]),
        ]
        bad_signal_count = sum(bool(item) for item in signals)
        score = float(bad_signal_count / len(signals))
        is_bad = bad_signal_count >= int(self.thresholds["min_bad_signals"])
        reason = "low_texture_or_uniform_frame" if is_bad else "normal_frame_quality"

        return DetectorResult(
            is_bad=is_bad,
            score=score,
            reason=reason,
            metrics={
                "brightness_mean": brightness_mean,
                "brightness_std": brightness_std,
                "entropy": entropy,
                "edge_density": edge_density,
                "blur_variance": blur_variance,
                "uniform_ratio": uniform_ratio,
                "baseline_difference": baseline_difference,
                "local_occlusion_ratio": local_occlusion_ratio,
                "bad_signal_count": bad_signal_count,
            },
        )
