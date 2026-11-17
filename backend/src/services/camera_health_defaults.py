from __future__ import annotations

import tomllib
from pathlib import Path

from src.core.settings import settings


CAMERA_OCCLUSION = "camera_occlusion"
CAMERA_VIEW_SHIFT = "camera_view_shift"

_REQUIRED_KEYS = {
    CAMERA_OCCLUSION: {
        "low_brightness",
        "high_brightness",
        "min_entropy",
        "min_edge_density",
        "min_blur_variance",
        "max_uniform_ratio",
        "max_baseline_difference",
        "min_bad_signals",
        "local_occlusion_ratio",
    },
    CAMERA_VIEW_SHIFT: {
        "ratio_test",
        "min_good_matches",
        "ransac_reproj_threshold",
        "min_inlier_ratio",
        "max_horizontal_shift_ratio",
        "max_vertical_shift_ratio",
        "roll_angle_deg",
        "max_scale_change_ratio",
        "max_perspective_change",
    },
}


def _as_float(algorithm_type: str, key: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{algorithm_type}.{key} must be numeric")
    number = float(value)
    if number < 0:
        raise ValueError(f"{algorithm_type}.{key} must be >= 0")
    return number


def _validate_thresholds(raw: dict) -> dict[str, dict[str, float | int]]:
    unknown_algorithms = set(raw) - set(_REQUIRED_KEYS)
    if unknown_algorithms:
        name = sorted(unknown_algorithms)[0]
        raise ValueError(f"unknown algorithm in camera health thresholds: {name}")

    result: dict[str, dict[str, float | int]] = {}
    for algorithm_type, required_keys in _REQUIRED_KEYS.items():
        section = raw.get(algorithm_type)
        if not isinstance(section, dict):
            raise ValueError(f"missing threshold section: {algorithm_type}")
        missing = required_keys - set(section)
        if missing:
            raise ValueError(f"missing threshold {algorithm_type}.{sorted(missing)[0]}")
        unknown_keys = set(section) - required_keys
        if unknown_keys:
            raise ValueError(f"unknown threshold {algorithm_type}.{sorted(unknown_keys)[0]}")

        normalized: dict[str, float | int] = {}
        for key in required_keys:
            normalized[key] = _as_float(algorithm_type, key, section[key])

        if algorithm_type == CAMERA_OCCLUSION:
            if normalized["low_brightness"] >= normalized["high_brightness"]:
                raise ValueError("camera_occlusion.low_brightness must be < high_brightness")
            if normalized["min_bad_signals"] < 1 or normalized["min_bad_signals"] > 4:
                raise ValueError("camera_occlusion.min_bad_signals must be between 1 and 4")
            normalized["min_bad_signals"] = int(normalized["min_bad_signals"])
        else:
            if normalized["ratio_test"] <= 0 or normalized["ratio_test"] > 1:
                raise ValueError("camera_view_shift.ratio_test must be > 0 and <= 1")
            if normalized["min_good_matches"] < 4:
                raise ValueError("camera_view_shift.min_good_matches must be >= 4")
            if normalized["min_inlier_ratio"] > 1:
                raise ValueError("camera_view_shift.min_inlier_ratio must be <= 1")

        result[algorithm_type] = normalized
    return result


def load_camera_health_thresholds(path: Path | str | None = None) -> dict[str, dict[str, float | int]]:
    config_path = Path(path or settings.CAMERA_HEALTH_THRESHOLDS_FILE)
    if not config_path.is_absolute():
        config_path = settings.BASE_DIR / config_path
    if not config_path.exists():
        raise FileNotFoundError(f"camera health threshold file not found: {config_path}")
    with config_path.open("rb") as fp:
        raw = tomllib.load(fp)
    return _validate_thresholds(raw)
