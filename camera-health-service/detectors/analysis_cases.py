from __future__ import annotations

from typing import Any


def analysis_case_for(
    algorithm_type: str,
    reason: str | None,
    metrics: dict[str, Any] | None,
) -> str | None:
    """Return the four-character business code for one detector result."""

    algorithm = str(algorithm_type or "").strip().lower()
    normalized_reason = str(reason or "").strip().lower()
    values = metrics or {}

    if algorithm == "occlusion":
        return "0001"
    if algorithm == "view_shift":
        return "0002"
    if algorithm == "blur":
        return "0005"
    if algorithm == "freeze":
        return "0006"
    if algorithm == "video_loss":
        return "0007"
    if algorithm == "shake":
        return "0008"
    if algorithm == "resolution":
        return "0009"
    if algorithm == "exposure":
        if any(token in normalized_reason for token in ("under", "dark", "low")):
            return "0003"
        if any(token in normalized_reason for token in ("over", "bright", "high")):
            return "0004"
        brightness = values.get("brightness_mean")
        if brightness is not None:
            brightness = float(brightness)
            if brightness <= 12.0:
                return "0003"
            if brightness >= 245.0:
                return "0004"

    return None
