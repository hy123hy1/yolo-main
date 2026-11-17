"""Pure dispatch-room vacancy detection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from numbers import Real
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


DEFAULT_PERSON_LABELS = frozenset({"person"})
DEFAULT_MAX_FRAME_GAP_SECONDS = 5.0
DETECTION_LABEL_FIELDS = ("label", "class_name", "class", "name")


@dataclass(frozen=True)
class RoomEmptyEvent:
    """A single transition into a held empty-room state."""

    empty_since: Any
    event_timestamp: Any
    region_id: str
    last_person_count: int
    configured_hold_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "empty_since": _serialize_timestamp(self.empty_since),
            "event_timestamp": _serialize_timestamp(self.event_timestamp),
            "region_id": self.region_id,
            "last_person_count": self.last_person_count,
            "configured_hold_seconds": self.configured_hold_seconds,
        }


@dataclass(frozen=True)
class RoomEmptyUpdate:
    """State and transitions produced by one detector update."""

    region_id: str
    person_count: int
    empty_since: Any | None
    active_empty: bool
    recovered: bool
    recovery_timestamp: Any | None
    events: tuple[RoomEmptyEvent, ...]
    discontinuity: bool = False

    @property
    def last_person_count(self) -> int:
        return self.person_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "person_count": self.person_count,
            "empty_since": _serialize_timestamp(self.empty_since),
            "active_empty": self.active_empty,
            "recovered": self.recovered,
            "recovery_timestamp": _serialize_timestamp(self.recovery_timestamp),
            "events": [event.to_dict() for event in self.events],
            "discontinuity": self.discontinuity,
        }


# Both names describe the same immutable result returned by ``update``.
RoomEmptyResult = RoomEmptyUpdate


class RoomEmptyDetector:
    """Detect a configured region that stays free of people for a hold period."""

    def __init__(
        self,
        *,
        region_id: str,
        polygon: Sequence[Sequence[Real]],
        hold_seconds: Real,
        person_labels: Iterable[str] | None = None,
        max_frame_gap_seconds: Real | None = DEFAULT_MAX_FRAME_GAP_SECONDS,
    ) -> None:
        if not isinstance(region_id, str) or not region_id.strip():
            raise ValueError("region_id must not be empty")
        try:
            hold_seconds = float(hold_seconds)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("hold_seconds must be a non-negative finite number") from exc
        if not math.isfinite(hold_seconds) or hold_seconds < 0:
            raise ValueError("hold_seconds must be a non-negative finite number")

        self.region_id = region_id.strip()
        self.polygon = self._validate_polygon(polygon)
        labels = DEFAULT_PERSON_LABELS if person_labels is None else person_labels
        if isinstance(labels, str):
            labels = (labels,)
        try:
            self.person_labels = frozenset(self._normalize_label(label) for label in labels)
        except TypeError as exc:
            raise ValueError("person_labels must be an iterable") from exc
        if not self.person_labels or "" in self.person_labels:
            raise ValueError("person_labels must contain non-empty labels")
        if max_frame_gap_seconds is None:
            self.max_frame_gap_seconds = None
        else:
            try:
                max_frame_gap_seconds = float(max_frame_gap_seconds)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    "max_frame_gap_seconds must be None or a non-negative finite number"
                ) from exc
            if not math.isfinite(max_frame_gap_seconds) or max_frame_gap_seconds < 0:
                raise ValueError(
                    "max_frame_gap_seconds must be None or a non-negative finite number"
                )
            self.max_frame_gap_seconds = max_frame_gap_seconds
        self.hold_seconds = hold_seconds
        self._empty_since: Any | None = None
        self._empty_since_seconds: float | None = None
        self._last_timestamp_seconds: float | None = None
        self._active_empty = False

    def filter_person_detections(
        self,
        detections: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return only configured person detections whose bottom center is in the region."""
        if detections is None or isinstance(detections, (str, bytes)):
            raise ValueError("detections must be an iterable")
        try:
            iterator = iter(detections)
        except TypeError as exc:
            raise ValueError("detections must be an iterable") from exc

        person_detections: list[dict[str, Any]] = []
        for detection in iterator:
            if not isinstance(detection, Mapping):
                raise ValueError("each detection must be dict-like")
            labels = self._labels_for(detection)
            if not any(label in self.person_labels for label in labels):
                continue
            bbox = self._validate_bbox(detection)
            if self._point_in_polygon(self._bottom_center(bbox), self.polygon):
                person_detections.append(dict(detection))
        return person_detections

    def update(
        self,
        detections: Iterable[Mapping[str, Any]],
        timestamp: Real | datetime,
    ) -> RoomEmptyUpdate:
        timestamp_seconds = self._timestamp_seconds(timestamp)
        if self._last_timestamp_seconds is not None and timestamp_seconds < self._last_timestamp_seconds:
            raise ValueError("timestamp must be monotonic")

        person_count = len(self.filter_person_detections(detections))

        discontinuity = (
            self._last_timestamp_seconds is not None
            and self.max_frame_gap_seconds is not None
            and timestamp_seconds - self._last_timestamp_seconds
            > self.max_frame_gap_seconds
        )
        self._last_timestamp_seconds = timestamp_seconds
        recovered = False
        recovery_timestamp: Any | None = None
        events: tuple[RoomEmptyEvent, ...] = ()

        # A frame gap is diagnostic information only. Keep the original empty
        # period so a recovered stream continues the same hold timer.

        if person_count > 0:
            if self._active_empty:
                recovered = True
                recovery_timestamp = timestamp
            self._empty_since = None
            self._empty_since_seconds = None
            self._active_empty = False
        else:
            if self._empty_since_seconds is None:
                self._empty_since = timestamp
                self._empty_since_seconds = timestamp_seconds
            if (
                not self._active_empty
                and timestamp_seconds - self._empty_since_seconds >= self.hold_seconds
            ):
                event = RoomEmptyEvent(
                    empty_since=self._empty_since,
                    event_timestamp=timestamp,
                    region_id=self.region_id,
                    last_person_count=person_count,
                    configured_hold_seconds=self.hold_seconds,
                )
                events = (event,)
                self._active_empty = True

        return RoomEmptyUpdate(
            region_id=self.region_id,
            person_count=person_count,
            empty_since=self._empty_since,
            active_empty=self._active_empty,
            recovered=recovered,
            recovery_timestamp=recovery_timestamp,
            events=events,
            discontinuity=discontinuity,
        )

    @staticmethod
    def _normalize_label(value: Any) -> str:
        label = str(value).strip().lower()
        if not label:
            raise ValueError("person_labels must contain non-empty labels")
        return label

    def _labels_for(self, detection: Mapping[str, Any]) -> tuple[str, ...]:
        labels: list[str] = []
        for field in DETECTION_LABEL_FIELDS:
            value = detection.get(field)
            if value is None or not str(value).strip():
                continue
            labels.append(self._normalize_label(value))
        return tuple(labels)

    @staticmethod
    def _validate_polygon(polygon: Sequence[Sequence[Real]]) -> tuple[tuple[float, float], ...]:
        if isinstance(polygon, (str, bytes)):
            raise ValueError("polygon must contain numeric [x, y] points")
        try:
            points_list: list[tuple[float, float]] = []
            for point in polygon:
                if isinstance(point, (str, bytes)) or len(point) != 2:
                    raise ValueError
                points_list.append((float(point[0]), float(point[1])))
            points = tuple(points_list)
        except (TypeError, ValueError, IndexError, OverflowError) as exc:
            raise ValueError("polygon must contain numeric [x, y] points") from exc
        if len(points) < 3:
            raise ValueError("polygon must contain at least three points")
        if any(not math.isfinite(value) for point in points for value in point):
            raise ValueError("polygon coordinates must be finite")
        area = sum(
            points[index][0] * points[(index + 1) % len(points)][1]
            - points[(index + 1) % len(points)][0] * points[index][1]
            for index in range(len(points))
        )
        if math.isclose(area, 0.0, abs_tol=1e-12):
            raise ValueError("polygon must enclose a non-zero area")
        return points

    @staticmethod
    def _validate_bbox(detection: Mapping[str, Any]) -> tuple[float, float, float, float]:
        raw_bbox = detection.get("bbox")
        try:
            if len(raw_bbox) != 4:
                raise ValueError
            bbox = tuple(float(value) for value in raw_bbox)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("detection bbox must contain four numeric values") from exc
        if any(not math.isfinite(value) for value in bbox):
            raise ValueError("detection bbox values must be finite")
        if bbox[2] < bbox[0] or bbox[3] < bbox[1]:
            raise ValueError("detection bbox must have non-negative dimensions")
        return bbox

    @staticmethod
    def _bottom_center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
        return ((bbox[0] + bbox[2]) / 2.0, bbox[3])

    @staticmethod
    def _timestamp_seconds(timestamp: Real | datetime) -> float:
        if isinstance(timestamp, datetime):
            value = timestamp.timestamp()
        elif isinstance(timestamp, Real) and not isinstance(timestamp, bool):
            value = float(timestamp)
        else:
            raise ValueError("timestamp must be a number or datetime")
        if not math.isfinite(value):
            raise ValueError("timestamp must be finite")
        return value

    @staticmethod
    def _point_in_polygon(
        point: tuple[float, float],
        polygon: tuple[tuple[float, float], ...],
    ) -> bool:
        x, y = point
        inside = False
        for index, (x1, y1) in enumerate(polygon):
            x2, y2 = polygon[(index + 1) % len(polygon)]
            scale = max(1.0, abs(x), abs(y), abs(x1), abs(y1), abs(x2), abs(y2))
            cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
            tolerance = 1e-10 * scale * max(1.0, math.hypot(x2 - x1, y2 - y1))
            if abs(cross) <= tolerance and min(x1, x2) - tolerance <= x <= max(x1, x2) + tolerance and min(y1, y2) - tolerance <= y <= max(y1, y2) + tolerance:
                return True
            if (y1 > y) != (y2 > y):
                intersection_x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                if x < intersection_x:
                    inside = not inside
        return inside


def _serialize_timestamp(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


__all__ = [
    "DEFAULT_PERSON_LABELS",
    "DEFAULT_MAX_FRAME_GAP_SECONDS",
    "RoomEmptyDetector",
    "RoomEmptyEvent",
    "RoomEmptyResult",
    "RoomEmptyUpdate",
]
