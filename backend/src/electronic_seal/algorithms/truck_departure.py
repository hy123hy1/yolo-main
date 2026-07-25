"""Virtual-line truck departure detector.

The point used for line crossing is the bottom-center of each vehicle bbox.
Coordinates follow the usual image convention where y increases downward.
For a line directed from ``line_start`` to ``line_end``, the negative signed
side is its visual left-hand side. ``inside_side`` selects the side a vehicle
must occupy before a departure. ``direction`` is either
``inside_to_outside`` (the normal derived from the configured side), one of
the image-space compass directions, or a two-component expected movement
vector.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from numbers import Real
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..tracking import NearestCentroidTracker, validate_bbox


DEFAULT_VEHICLE_LABELS = frozenset(
    {
        "truck",
        "car",
        "bus",
        "van",
        "vehicle",
        "automobile",
        "pickup",
        "lorry",
        "trailer",
        "minibus",
        "suv",
        "motor_vehicle",
    }
)

_DIRECTION_VECTORS = {
    "left_to_right": (1.0, 0.0),
    "right_to_left": (-1.0, 0.0),
    "top_to_bottom": (0.0, 1.0),
    "bottom_to_top": (0.0, -1.0),
}
_AUTO_DIRECTIONS = frozenset({"inside_to_outside", "outbound", "departure"})


@dataclass(frozen=True)
class TruckDepartureEvent:
    track_id: Any
    label: str
    confidence: float
    line_id: str
    direction: str
    crossing_timestamp: Any
    bbox: list[float]

    @property
    def class_name(self) -> str:
        return self.label

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "class": self.label,
            "label": self.label,
            "confidence": self.confidence,
            "line_id": self.line_id,
            "direction": self.direction,
            "crossing_timestamp": self.crossing_timestamp,
            "bbox": list(self.bbox),
        }


@dataclass
class _TrackCrossingState:
    side: int
    point: tuple[float, float]
    last_event_seconds: float | None = None


class TruckDepartureDetector:
    """Detect directed inside-to-outside crossings of a virtual line.

    Duplicate observations of one crossing are suppressed. After a vehicle
    re-enters, a later directed crossing is a new departure event once the
    configured cooldown has elapsed.
    """

    def __init__(
        self,
        line_start: Sequence[Real],
        line_end: Sequence[Real],
        *,
        line_id: str = "departure-line",
        inside_side: str = "left",
        direction: str | Sequence[Real] = "inside_to_outside",
        allowed_labels: Iterable[str] | None = None,
        cooldown_seconds: float = 0.0,
        tracker: NearestCentroidTracker | None = None,
        max_match_distance: float = 120.0,
        max_missed_frames: int = 1,
        side_epsilon: float = 1e-6,
        line_hysteresis: float = 2.0,
        direction_cosine_threshold: float = 0.5,
    ) -> None:
        self.line_start = self._point(line_start, "line_start")
        self.line_end = self._point(line_end, "line_end")
        if self.line_start == self.line_end:
            raise ValueError("line_start and line_end must be different points")
        self._line_length = math.dist(self.line_start, self.line_end)
        if not line_id:
            raise ValueError("line_id must not be empty")
        inside_side = str(inside_side).lower()
        if inside_side not in {"left", "right"}:
            raise ValueError("inside_side must be 'left' or 'right'")
        if not math.isfinite(float(cooldown_seconds)) or cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be a non-negative finite number")
        if not math.isfinite(float(side_epsilon)) or side_epsilon < 0:
            raise ValueError("side_epsilon must be a non-negative finite number")
        if not math.isfinite(float(line_hysteresis)) or line_hysteresis < 0:
            raise ValueError("line_hysteresis must be a non-negative finite number")
        try:
            direction_cosine_threshold = float(direction_cosine_threshold)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("direction_cosine_threshold must satisfy 0 <= threshold < 1") from exc
        if not math.isfinite(direction_cosine_threshold) or not 0.0 <= direction_cosine_threshold < 1.0:
            raise ValueError("direction_cosine_threshold must satisfy 0 <= threshold < 1")

        self.line_id = str(line_id)
        self.inside_side = inside_side
        self.direction, self._expected_vector = self._parse_direction(direction)
        self.cooldown_seconds = float(cooldown_seconds)
        self.side_epsilon = float(side_epsilon)
        self.line_hysteresis = float(line_hysteresis)
        self.direction_cosine_threshold = direction_cosine_threshold
        labels = DEFAULT_VEHICLE_LABELS if allowed_labels is None else allowed_labels
        self.allowed_labels = frozenset(self._normalize_label(label) for label in labels)
        if not self.allowed_labels:
            raise ValueError("allowed_labels must contain at least one label")
        self.tracker = tracker or NearestCentroidTracker(
            max_match_distance=max_match_distance,
            max_missed_frames=max_missed_frames,
        )
        self._tracks: dict[Any, _TrackCrossingState] = {}

    def update(
        self,
        detections: Iterable[Mapping[str, Any]],
        timestamp: Real | datetime,
    ) -> list[TruckDepartureEvent]:
        timestamp_seconds = self._timestamp_seconds(timestamp)
        if detections is None:
            raise ValueError("detections must be an iterable")
        vehicle_detections: list[dict[str, Any]] = []
        for raw_detection in detections:
            if not hasattr(raw_detection, "get"):
                raise ValueError("each detection must be dict-like")
            label = self._label_for(raw_detection)
            if label not in self.allowed_labels:
                continue
            bbox = validate_bbox(raw_detection)
            detection = dict(raw_detection)
            detection["bbox"] = bbox
            detection["label"] = label
            try:
                confidence = float(detection.get("confidence", 1.0))
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("detection confidence must be numeric") from exc
            if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
                raise ValueError("detection confidence must be between 0 and 1")
            detection["confidence"] = confidence
            vehicle_detections.append(detection)

        tracked = self.tracker.update(vehicle_detections)
        active_track_ids = self.tracker.active_track_ids
        self._tracks = {
            track_id: state
            for track_id, state in self._tracks.items()
            if track_id in active_track_ids
        }
        events: list[TruckDepartureEvent] = []
        for detection in tracked:
            track_id = detection["track_id"]
            point = self._bottom_center(detection["bbox"])
            side = self._side(point)
            if side == 0:
                continue

            previous = self._tracks.get(track_id)
            if previous is not None and previous.side == self._inside_sign and side == -self._inside_sign:
                movement = (point[0] - previous.point[0], point[1] - previous.point[1])
                if self._direction_matches(movement):
                    if previous.last_event_seconds is None or timestamp_seconds - previous.last_event_seconds >= self.cooldown_seconds:
                        events.append(
                            TruckDepartureEvent(
                                track_id=track_id,
                                label=detection["label"],
                                confidence=detection["confidence"],
                                line_id=self.line_id,
                                direction=self.direction,
                                crossing_timestamp=timestamp,
                                bbox=list(detection["bbox"]),
                            )
                        )
                        last_event_seconds = timestamp_seconds
                    else:
                        last_event_seconds = previous.last_event_seconds
                else:
                    last_event_seconds = previous.last_event_seconds
            else:
                last_event_seconds = previous.last_event_seconds if previous is not None else None
            self._tracks[track_id] = _TrackCrossingState(side, point, last_event_seconds)

        return events

    @property
    def _inside_sign(self) -> int:
        # The screen-coordinate y axis points down, so visual left is the
        # negative side of the directed 2-D cross product.
        return -1 if self.inside_side == "left" else 1

    @staticmethod
    def _point(value: Sequence[Real], name: str) -> tuple[float, float]:
        try:
            if len(value) != 2:
                raise ValueError
            point = (float(value[0]), float(value[1]))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must contain two numeric values") from exc
        if not all(math.isfinite(item) for item in point):
            raise ValueError(f"{name} values must be finite")
        return point

    @staticmethod
    def _normalize_label(value: Any) -> str:
        return str(value).strip().lower()

    def _label_for(self, detection: Mapping[str, Any]) -> str:
        value = detection.get("label")
        if value is None or not str(value).strip():
            value = detection.get("class")
        if value is None or not str(value).strip():
            return ""
        return self._normalize_label(value)

    def _side(self, point: tuple[float, float]) -> int:
        dx = self.line_end[0] - self.line_start[0]
        dy = self.line_end[1] - self.line_start[1]
        cross = dx * (point[1] - self.line_start[1]) - dy * (point[0] - self.line_start[0])
        perpendicular_distance = cross / self._line_length
        hysteresis = max(self.line_hysteresis, self.side_epsilon / self._line_length)
        if abs(perpendicular_distance) <= hysteresis:
            return 0
        return 1 if perpendicular_distance > 0 else -1

    @staticmethod
    def _bottom_center(bbox: list[float]) -> tuple[float, float]:
        return ((bbox[0] + bbox[2]) / 2.0, bbox[3])

    @staticmethod
    def _timestamp_seconds(timestamp: Real | datetime) -> float:
        if isinstance(timestamp, datetime):
            return timestamp.timestamp()
        if not isinstance(timestamp, Real):
            raise ValueError("timestamp must be a number or datetime")
        value = float(timestamp)
        if not math.isfinite(value):
            raise ValueError("timestamp must be finite")
        return value

    @staticmethod
    def _parse_direction(direction: str | Sequence[Real]) -> tuple[str, tuple[float, float] | None]:
        if isinstance(direction, str):
            normalized = direction.strip().lower()
            if normalized in _AUTO_DIRECTIONS:
                return normalized, None
            if normalized in _DIRECTION_VECTORS:
                return normalized, _DIRECTION_VECTORS[normalized]
            raise ValueError(
                "direction must be inside_to_outside, a compass direction, or a two-value vector"
            )
        try:
            if len(direction) != 2:
                raise ValueError
            vector = (float(direction[0]), float(direction[1]))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("direction vector must contain two numeric values") from exc
        if not all(math.isfinite(item) for item in vector) or vector == (0.0, 0.0):
            raise ValueError("direction vector must be finite and non-zero")
        return "custom", vector

    def _direction_matches(self, movement: tuple[float, float]) -> bool:
        if self._expected_vector is None:
            return True
        length = math.hypot(*movement)
        if length == 0:
            return False
        expected = self._expected_vector
        expected_length = math.hypot(*expected)
        dot = (movement[0] * expected[0] + movement[1] * expected[1]) / (length * expected_length)
        return dot >= self.direction_cosine_threshold
