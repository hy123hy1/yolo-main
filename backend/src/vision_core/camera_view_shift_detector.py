from __future__ import annotations

import math

import cv2
import numpy as np

from src.services.camera_health_types import DetectorResult, empty_view_shift_metrics


class CameraViewShiftDetector:
    def __init__(self, thresholds: dict[str, float | int]):
        self.thresholds = thresholds
        self._orb = cv2.ORB_create(nfeatures=1200)

    def analyze(self, frame: np.ndarray, baseline: np.ndarray | None) -> DetectorResult:
        metrics = empty_view_shift_metrics()
        if frame is None or baseline is None or frame.size == 0 or baseline.size == 0:
            return DetectorResult(False, 0.0, "insufficient_feature_matches", metrics)

        current_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        baseline_gray = cv2.cvtColor(baseline, cv2.COLOR_BGR2GRAY) if baseline.ndim == 3 else baseline
        baseline_gray = cv2.resize(baseline_gray, (current_gray.shape[1], current_gray.shape[0]))
        current_keypoints, current_descriptors = self._orb.detectAndCompute(current_gray, None)
        baseline_keypoints, baseline_descriptors = self._orb.detectAndCompute(baseline_gray, None)
        if current_descriptors is None or baseline_descriptors is None:
            return DetectorResult(False, 0.0, "insufficient_feature_matches", metrics)

        matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        pairs = matcher.knnMatch(current_descriptors, baseline_descriptors, k=2)
        ratio = float(self.thresholds["ratio_test"])
        good_matches = [first for first, second in pairs if first.distance < ratio * second.distance]
        metrics["good_matches"] = len(good_matches)
        minimum_matches = int(self.thresholds["min_good_matches"])
        if len(good_matches) < minimum_matches:
            return DetectorResult(False, 0.0, "insufficient_feature_matches", metrics)

        current_points = np.float32([current_keypoints[item.queryIdx].pt for item in good_matches])
        baseline_points = np.float32([baseline_keypoints[item.trainIdx].pt for item in good_matches])
        homography, mask = cv2.findHomography(
            current_points,
            baseline_points,
            cv2.RANSAC,
            float(self.thresholds["ransac_reproj_threshold"]),
        )
        if homography is None or mask is None:
            return DetectorResult(False, 0.0, "insufficient_geometric_inliers", metrics)

        inlier_count = int(mask.ravel().sum())
        inlier_ratio = float(inlier_count / max(len(good_matches), 1))
        metrics["inlier_count"] = inlier_count
        metrics["inlier_ratio"] = inlier_ratio
        width = max(float(current_gray.shape[1]), 1.0)
        height = max(float(current_gray.shape[0]), 1.0)
        matrix = homography / homography[2, 2] if abs(float(homography[2, 2])) > 1e-9 else homography
        metrics["horizontal_shift_ratio"] = abs(float(matrix[0, 2])) / width
        metrics["vertical_shift_ratio"] = abs(float(matrix[1, 2])) / height
        linear = matrix[:2, :2]
        determinant = max(abs(float(np.linalg.det(linear))), 1e-9)
        scale = math.sqrt(determinant)
        metrics["scale_change_ratio"] = abs(scale - 1.0)
        metrics["roll_angle_deg"] = math.degrees(math.atan2(float(linear[1, 0]), float(linear[0, 0])))
        metrics["perspective_change"] = min(
            1.0,
            abs(float(matrix[2, 0])) * width + abs(float(matrix[2, 1])) * height,
        )

        reliable = inlier_ratio >= float(self.thresholds["min_inlier_ratio"])
        if not reliable:
            return DetectorResult(False, 0.0, "insufficient_geometric_inliers", metrics)

        bad_components = [
            metrics["horizontal_shift_ratio"] > float(self.thresholds["max_horizontal_shift_ratio"]),
            metrics["vertical_shift_ratio"] > float(self.thresholds["max_vertical_shift_ratio"]),
            metrics["scale_change_ratio"] > float(self.thresholds["max_scale_change_ratio"]),
            abs(metrics["roll_angle_deg"]) > float(self.thresholds["roll_angle_deg"]),
            metrics["perspective_change"] > float(self.thresholds["max_perspective_change"]),
        ]
        bad_count = sum(bool(item) for item in bad_components)
        score = float(bad_count / len(bad_components))
        return DetectorResult(
            is_bad=bad_count > 0,
            score=score,
            reason="view_shift_detected" if bad_count > 0 else "normal_view",
            metrics=metrics,
        )
