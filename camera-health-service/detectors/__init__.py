from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DetectorResult:
    is_bad: bool
    score: float
    reason: str
    metrics: dict[str, Any] = field(default_factory=dict)
    quality_status: str | None = None


__all__ = ["DetectorResult"]
