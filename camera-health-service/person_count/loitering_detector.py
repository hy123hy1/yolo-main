from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from numbers import Real
from typing import Any, Iterable, Mapping, Sequence

from person_count.entry_tracker import NearestCentroidTracker, generate_region_line


@dataclass(frozen=True, slots=True)
class LoiteringEvent:
    behavior_code: str
    track_id: Any
    confidence: float
    event_timestamp: Any
    bbox: list[float]
    dwell_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "behavior_code": self.behavior_code,
            "track_id": self.track_id,
            "confidence": self.confidence,
            "event_timestamp": self.event_timestamp,
            "bbox": list(self.bbox),
            "dwell_seconds": self.dwell_seconds,
        }


@dataclass(slots=True)
class _LoiteringState:
    entered_seconds: float
    last_seen_seconds: float
    outside_started_seconds: float | None = None
    alarmed: bool = False


class LoiteringDetector:
    """Emit one event when a tracked person remains in a region long enough."""

    def __init__(
        self,
        region_points: Sequence[Sequence[Real]],
        *,
        dwell_seconds: float = 30.0,
        max_missing_seconds: float = 2.0,
        exit_confirm_seconds: float = 2.0,
        tracker: NearestCentroidTracker | Any | None = None,
    ) -> None:
        self.region_points = _polygon(region_points)
        self.dwell_seconds = _positive_float(dwell_seconds, "dwell_seconds")
        self.max_missing_seconds = _non_negative_float(
            max_missing_seconds,
            "max_missing_seconds",
        )
        self.exit_confirm_seconds = _non_negative_float(
            exit_confirm_seconds,
            "exit_confirm_seconds",
        )
        self.tracker = tracker or NearestCentroidTracker()
        self._tracks: dict[str, _LoiteringState] = {}

    @classmethod
    def from_region_config(
        cls,
        region_config: Mapping[str, Any],
        *,
        frame_width: int,
        frame_height: int,
        dwell_seconds: float = 30.0,
    ) -> "LoiteringDetector":
        if int(frame_width) <= 0 or int(frame_height) <= 0:
            raise ValueError("frame dimensions must be greater than 0")
        if not isinstance(region_config, Mapping):
            raise ValueError("region config must be an object")
        raw_points = region_config.get("points")
        if not isinstance(raw_points, (list, tuple)):
            raise ValueError("region config must contain exactly four points")
        generate_region_line(raw_points)
        points = [
            [float(point[0]) * int(frame_width), float(point[1]) * int(frame_height)]
            for point in raw_points
        ]
        return cls(points, dwell_seconds=dwell_seconds)

    def update(
        self,
        detections: Iterable[Mapping[str, Any]],
        timestamp: Real | datetime,
    ) -> list[LoiteringEvent]:
        timestamp_seconds = _timestamp_seconds(timestamp)
        people: list[dict[str, Any]] = []
        for raw in detections:
            label = str(raw.get("label") or raw.get("class") or "").strip().lower()
            if label == "person":
                people.append(dict(raw))

        tracked = self.tracker.update(people)
        seen_ids: set[str] = set()
        events: list[LoiteringEvent] = []
        for detection in tracked:
            track_id = str(detection["track_id"])
            seen_ids.add(track_id)
            state = self._tracks.get(track_id)
            if (
                state is not None
                and timestamp_seconds - state.last_seen_seconds > self.max_missing_seconds
            ):
                self._tracks.pop(track_id, None)
                state = None

            bbox = [float(value) for value in detection["bbox"]]
            inside = _point_in_polygon(_bottom_center(bbox), self.region_points)
            if state is None:
                if inside:
                    self._tracks[track_id] = _LoiteringState(
                        entered_seconds=timestamp_seconds,
                        last_seen_seconds=timestamp_seconds,
                    )
                continue

            state.last_seen_seconds = timestamp_seconds
            if not inside:
                if state.outside_started_seconds is None:
                    state.outside_started_seconds = timestamp_seconds
                elif (
                    timestamp_seconds - state.outside_started_seconds
                    >= self.exit_confirm_seconds
                ):
                    self._tracks.pop(track_id, None)
                continue

            state.outside_started_seconds = None
            dwell = timestamp_seconds - state.entered_seconds
            if not state.alarmed and dwell >= self.dwell_seconds:
                events.append(
                    LoiteringEvent(
                        behavior_code="loitering",
                        track_id=track_id,
                        confidence=float(detection.get("confidence", 1.0)),
                        event_timestamp=timestamp,
                        bbox=bbox,
                        dwell_seconds=dwell,
                    )
                )
                state.alarmed = True

        for track_id, state in list(self._tracks.items()):
            if (
                track_id not in seen_ids
                and timestamp_seconds - state.last_seen_seconds > self.max_missing_seconds
            ):
                self._tracks.pop(track_id, None)
        return events


def _polygon(points: Sequence[Sequence[Real]]) -> list[tuple[float, float]]:
    if len(points) != 4:
        raise ValueError("loitering region must contain exactly four points")
    result: list[tuple[float, float]] = []
    for point in points:
        if len(point) != 2:
            raise ValueError("loitering region point must contain two values")
        result.append(
            (
                _finite_float(point[0], "region coordinate"),
                _finite_float(point[1], "region coordinate"),
            )
        )
    return result


def _bottom_center(bbox: Sequence[Real]) -> tuple[float, float]:
    if len(bbox) != 4:
        raise ValueError("detection bbox must contain four values")
    return ((float(bbox[0]) + float(bbox[2])) / 2.0, float(bbox[3]))


def _point_in_polygon(
    point: tuple[float, float],
    polygon: Sequence[tuple[float, float]],
) -> bool:
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = current
        x2, y2 = previous
        if ((y1 > y) != (y2 > y)) and (
            x < (x2 - x1) * (y - y1) / (y2 - y1) + x1
        ):
            inside = not inside
        previous = current
    return inside


def _timestamp_seconds(value: Real | datetime) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    return _finite_float(value, "timestamp")


def _finite_float(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite numeric")
    return result


def _positive_float(value: Any, name: str) -> float:
    result = _finite_float(value, name)
    if result <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return result


def _non_negative_float(value: Any, name: str) -> float:
    result = _finite_float(value, name)
    if result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result


__all__ = ["LoiteringDetector", "LoiteringEvent"]
