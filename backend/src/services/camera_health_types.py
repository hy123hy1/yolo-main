from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DetectorResult:
    is_bad: bool
    score: float
    reason: str
    metrics: dict[str, Any] = field(default_factory=dict)


def empty_view_shift_metrics() -> dict[str, float | int]:
    return {
        "good_matches": 0,
        "inlier_count": 0,
        "inlier_ratio": 0.0,
        "horizontal_shift_ratio": 0.0,
        "vertical_shift_ratio": 0.0,
        "scale_change_ratio": 0.0,
        "roll_angle_deg": 0.0,
        "perspective_change": 0.0,
    }
