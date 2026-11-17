from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from collections.abc import Iterable, Mapping, Sequence
from numbers import Real
from typing import Any


@dataclass(frozen=True, slots=True)
class PersonEntryEvent:
    track_id: Any
    confidence: float
    line_id: str
    direction: str
    crossing_timestamp: Any
    bbox: list[float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "confidence": self.confidence,
            "line_id": self.line_id,
            "direction": self.direction,
            "crossing_timestamp": self.crossing_timestamp,
            "bbox": list(self.bbox),
        }


@dataclass
class _Track:
    track_id: str
    centroid: tuple[float, float]
    bbox: list[float]
    missed_frames: int = 0


class NearestCentroidTracker:
    """Fallback tracker for detections that do not include an upstream ID.

    Normal production inference uses Ultralytics ByteTrack IDs. This tracker
    keeps the electronic-seal-compatible contract and only supplies IDs when a
    custom detector or a model result does not provide them.
    """

    def __init__(
        self,
        *,
        max_match_distance: float = 120.0,
        max_missed_frames: int = 5,
        id_prefix: str = "track",
    ) -> None:
        if not math.isfinite(float(max_match_distance)) or max_match_distance <= 0:
            raise ValueError("max_match_distance must be a positive finite number")
        if not isinstance(max_missed_frames, int) or max_missed_frames < 0:
            raise ValueError("max_missed_frames must be a non-negative integer")
        if not str(id_prefix).strip():
            raise ValueError("id_prefix must not be empty")
        self.max_match_distance = float(max_match_distance)
        self.max_missed_frames = max_missed_frames
        self.id_prefix = str(id_prefix).strip()
        self._tracks: dict[str, _Track] = {}
        self._next_id = 1

    @property
    def active_track_ids(self) -> frozenset[str]:
        return frozenset(self._tracks)

    def update(self, detections: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if detections is None:
            raise ValueError("detections must be an iterable")

        normalized: list[tuple[dict[str, Any], list[float], tuple[float, float]]] = []
        for raw in detections:
            if not hasattr(raw, "get"):
                raise ValueError("each detection must be dict-like")
            detection = dict(raw)
            bbox = _validate_bbox(detection)
            confidence = _finite_float(detection.get("confidence", 1.0), "confidence")
            if not 0.0 <= confidence <= 1.0:
                raise ValueError("confidence must be between 0 and 1")
            detection["bbox"] = bbox
            detection["confidence"] = confidence
            normalized.append((detection, bbox, _bbox_centroid(bbox)))

        results: list[dict[str, Any] | None] = [None] * len(normalized)
        used_track_ids: set[str] = set()
        pending: list[int] = []

        for index, (detection, bbox, centroid) in enumerate(normalized):
            external_id = detection.get("track_id")
            if external_id is None:
                pending.append(index)
                continue
            track_id = str(external_id)
            if track_id in used_track_ids:
                raise ValueError("duplicate track_id values in one update")
            track = self._tracks.get(track_id)
            if track is None:
                track = _Track(track_id, centroid, bbox)
                self._tracks[track_id] = track
            track.centroid = centroid
            track.bbox = bbox
            track.missed_frames = 0
            used_track_ids.add(track_id)
            detection["track_id"] = track_id
            results[index] = detection

        candidates = [
            track
            for track_id, track in self._tracks.items()
            if track_id not in used_track_ids
            and track.missed_frames <= self.max_missed_frames
        ]
        candidates.sort(key=lambda item: item.track_id)
        matched_indexes: set[int] = set()
        for track in candidates:
            choices = [
                (index, math.dist(normalized[index][2], track.centroid))
                for index in pending
                if index not in matched_indexes
            ]
            choices = [item for item in choices if item[1] <= self.max_match_distance]
            if not choices:
                continue
            index, _distance = min(choices, key=lambda item: (item[1], item[0]))
            detection, bbox, centroid = normalized[index]
            track.centroid = centroid
            track.bbox = bbox
            track.missed_frames = 0
            used_track_ids.add(track.track_id)
            matched_indexes.add(index)
            detection["track_id"] = track.track_id
            results[index] = detection

        for index in pending:
            if index in matched_indexes:
                continue
            track_id = self._new_track_id()
            detection, bbox, centroid = normalized[index]
            self._tracks[track_id] = _Track(track_id, centroid, bbox)
            used_track_ids.add(track_id)
            detection["track_id"] = track_id
            results[index] = detection

        for track_id, track in self._tracks.items():
            if track_id not in used_track_ids:
                track.missed_frames += 1
        self._tracks = {
            track_id: track
            for track_id, track in self._tracks.items()
            if track.missed_frames <= self.max_missed_frames
        }
        return [item for item in results if item is not None]

    def _new_track_id(self) -> str:
        while True:
            track_id = f"{self.id_prefix}-{self._next_id}"
            self._next_id += 1
            if track_id not in self._tracks:
                return track_id


@dataclass
class _CrossingState:
    side: int
    point: tuple[float, float]
    last_event_seconds: float | None = None


@dataclass
class _RegionCrossingState:
    phase: str = "unknown"
    source_frames: int = 0
    target_frames: int = 0
    cross_seconds: float | None = None
    last_seen_seconds: float | None = None
    previous_progress: float | None = None


class PersonEntryDetector:
    """Detect directed outside-to-inside crossings for one camera."""

    def __init__(
        self,
        line_start: Sequence[Real],
        line_end: Sequence[Real],
        *,
        inside_side: str = "right",
        inside_polygon: Sequence[Sequence[Real]] | None = None,
        line_id: str = "entry-line",
        direction: str | Sequence[Real] = "outside_to_inside",
        cooldown_seconds: float = 1.0,
        tracker: NearestCentroidTracker | Any | None = None,
        max_match_distance: float = 120.0,
        max_missed_frames: int = 5,
        line_hysteresis: float = 2.0,
        direction_cosine_threshold: float = 0.5,
    ) -> None:
        self.line_start = _point(line_start, "line_start")
        self.line_end = _point(line_end, "line_end")
        if self.line_start == self.line_end:
            raise ValueError("line_start and line_end must be different points")
        self._line_length = math.dist(self.line_start, self.line_end)
        self.inside_side = str(inside_side).strip().lower()
        if self.inside_side not in {"left", "right"}:
            raise ValueError("inside_side must be 'left' or 'right'")
        self.line_id = str(line_id).strip()
        if not self.line_id:
            raise ValueError("line_id must not be empty")
        self.direction, self._expected_vector = _parse_direction(direction)
        self.cooldown_seconds = _non_negative_float(cooldown_seconds, "cooldown_seconds")
        self.line_hysteresis = _non_negative_float(line_hysteresis, "line_hysteresis")
        self.direction_cosine_threshold = _bounded_float(
            direction_cosine_threshold,
            "direction_cosine_threshold",
            upper=1.0,
        )
        self.inside_polygon = _polygon(inside_polygon) if inside_polygon is not None else None
        self.tracker = tracker or NearestCentroidTracker(
            max_match_distance=max_match_distance,
            max_missed_frames=max_missed_frames,
        )
        self._tracks: dict[str, _CrossingState] = {}
        self._region_mode = False
        self._region_points: list[tuple[float, float]] | None = None
        self._region_line: list[list[float]] | None = None
        self._region_tracks: dict[str, _RegionCrossingState] = {}

    @classmethod
    def from_region_config(
        cls,
        region_config: Mapping[str, Any],
        *,
        frame_width: int,
        frame_height: int,
        **kwargs: Any,
    ) -> "PersonEntryDetector":
        normalized = _region_points(region_config)
        points = [
            [point[0] * frame_width, point[1] * frame_height]
            for point in normalized
        ]
        line = _region_line(points)
        detector = cls(
            line[0],
            line[1],
            direction="toward_first_edge",
            line_id="region-line",
            inside_polygon=points,
            **kwargs,
        )
        detector._region_mode = True
        detector._region_points = [tuple(point) for point in points]
        detector._region_line = [
            [float(value) for value in point]
            for point in line
        ]
        return detector

    @classmethod
    def from_config(
        cls,
        line_config: Mapping[str, Any],
        region_config: Mapping[str, Any] | None,
        *,
        frame_width: int,
        frame_height: int,
        direction: str | None = None,
        **kwargs: Any,
    ) -> "PersonEntryDetector":
        line = _geometry_points(line_config, frame_width, frame_height, expected=2)
        polygon = None
        if region_config:
            polygon = _geometry_points(region_config, frame_width, frame_height)
        config = dict(line_config)
        return cls(
            line[0],
            line[1],
            inside_polygon=polygon,
            inside_side=str(config.get("inside_side") or "right"),
            line_id=str(config.get("line_id") or "entry-line"),
            direction=direction or config.get("direction") or "outside_to_inside",
            cooldown_seconds=float(config.get("cooldown_seconds", 1.0)),
            line_hysteresis=float(config.get("line_hysteresis", 2.0)),
            direction_cosine_threshold=float(
                config.get("direction_cosine_threshold", 0.5)
            ),
            **kwargs,
        )

    def update(
        self,
        detections: Iterable[Mapping[str, Any]],
        timestamp: Real | datetime,
    ) -> list[PersonEntryEvent]:
        timestamp_seconds = _timestamp_seconds(timestamp)
        people = []
        for raw in detections:
            label = str(raw.get("label") or raw.get("class") or "").strip().lower()
            if label != "person":
                continue
            detection = dict(raw)
            detection["label"] = label
            people.append(detection)

        if self._region_mode:
            return self._update_region(people, timestamp, timestamp_seconds)

        tracked = self.tracker.update(people)
        active_ids = set(self.tracker.active_track_ids)
        self._tracks = {
            track_id: state
            for track_id, state in self._tracks.items()
            if track_id in active_ids
        }
        events: list[PersonEntryEvent] = []
        for detection in tracked:
            track_id = str(detection["track_id"])
            point = _bottom_center(detection["bbox"])
            side = self._side(point)
            if side == 0:
                continue
            previous = self._tracks.get(track_id)
            if (
                previous is not None
                and previous.side == self._source_side
                and side == self._target_side
                and (
                    self.inside_polygon is None
                    or _point_in_polygon(
                        point if self.direction == "outside_to_inside" else previous.point,
                        self.inside_polygon,
                    )
                )
            ):
                movement = (point[0] - previous.point[0], point[1] - previous.point[1])
                if self._direction_matches(movement):
                    allowed = (
                        previous.last_event_seconds is None
                        or timestamp_seconds - previous.last_event_seconds >= self.cooldown_seconds
                    )
                    last_event_seconds = previous.last_event_seconds
                    if allowed:
                        events.append(
                            PersonEntryEvent(
                                track_id=track_id,
                                confidence=float(detection["confidence"]),
                                line_id=self.line_id,
                                direction=self.direction,
                                crossing_timestamp=timestamp,
                                bbox=list(detection["bbox"]),
                            )
                        )
                        last_event_seconds = timestamp_seconds
                    self._tracks[track_id] = _CrossingState(
                        side=side,
                        point=point,
                        last_event_seconds=last_event_seconds,
                    )
                    continue
            self._tracks[track_id] = _CrossingState(
                side=side,
                point=point,
                last_event_seconds=(previous.last_event_seconds if previous else None),
            )
        return events

    def _update_region(
        self,
        people: list[dict[str, Any]],
        timestamp: Real | datetime,
        timestamp_seconds: float,
    ) -> list[PersonEntryEvent]:
        tracked = self.tracker.update(people)
        active_ids = set(self.tracker.active_track_ids)
        self._region_tracks = {
            track_id: state
            for track_id, state in self._region_tracks.items()
            if track_id in active_ids
            and (
                state.last_seen_seconds is None
                or timestamp_seconds - state.last_seen_seconds <= 2.0
            )
        }
        events: list[PersonEntryEvent] = []
        for detection in tracked:
            track_id = str(detection["track_id"])
            point = _bottom_center(detection["bbox"])
            progress = self._region_progress(point)
            state = self._region_tracks.setdefault(track_id, _RegionCrossingState())
            state.last_seen_seconds = timestamp_seconds
            previous_progress = state.previous_progress
            state.previous_progress = progress
            source = self._region_source(progress, point)
            target = self._region_target(progress, point)

            if state.phase == "locked":
                if source:
                    state.source_frames += 1
                    if state.source_frames >= 3:
                        state.phase = "ready"
                        state.target_frames = 0
                        state.cross_seconds = None
                else:
                    state.source_frames = 0
                continue

            if state.phase == "unknown":
                state.source_frames = state.source_frames + 1 if source else 0
                if state.source_frames >= 3:
                    state.phase = "ready"
                continue

            if state.phase == "ready":
                if self._crossed_region_line(previous_progress, progress):
                    state.phase = "crossed"
                    state.cross_seconds = timestamp_seconds
                    state.target_frames = 1 if target else 0
                elif not source:
                    state.source_frames = 0
                    state.phase = "unknown"
                continue

            if state.cross_seconds is not None and timestamp_seconds - state.cross_seconds > 3.0:
                state.phase = "ready" if source else "unknown"
                state.source_frames = 3 if source else 0
                state.target_frames = 0
                state.cross_seconds = None
                continue

            if state.phase == "crossed":
                if target:
                    state.target_frames += 1
                    if state.target_frames >= 3:
                        events.append(
                            PersonEntryEvent(
                                track_id=track_id,
                                confidence=float(detection["confidence"]),
                                line_id=self.line_id,
                                direction=self.direction,
                                crossing_timestamp=timestamp,
                                bbox=list(detection["bbox"]),
                            )
                        )
                        state.phase = "locked"
                        state.source_frames = 0
                        state.target_frames = 0
                        state.cross_seconds = None
                elif source:
                    state.phase = "ready"
                    state.target_frames = 0
                    state.cross_seconds = None
                else:
                    state.target_frames = 0
        return events

    def _region_progress(self, point: tuple[float, float]) -> float:
        assert self._region_points is not None
        assert self._region_line is not None
        source = (
            (self._region_points[2][0] + self._region_points[3][0]) / 2.0,
            (self._region_points[2][1] + self._region_points[3][1]) / 2.0,
        )
        line_start = self._region_line[0]
        line_end = self._region_line[1]
        dx = line_end[0] - line_start[0]
        dy = line_end[1] - line_start[1]
        length = math.hypot(dx, dy)
        point_cross = dx * (point[1] - line_start[1]) - dy * (
            point[0] - line_start[0]
        )
        source_cross = dx * (source[1] - line_start[1]) - dy * (
            source[0] - line_start[0]
        )
        source_sign = 1.0 if source_cross > 0 else -1.0
        return point_cross * source_sign / length

    def _region_source(self, progress: float, point: tuple[float, float]) -> bool:
        return (
            progress > 0
            and self._region_points is not None
            and _point_in_polygon(point, self._region_points)
        )

    def _region_target(self, progress: float, point: tuple[float, float]) -> bool:
        return (
            progress <= 0
            and self._region_points is not None
            and _point_in_polygon(point, self._region_points)
        )

    @staticmethod
    def _crossed_region_line(previous: float | None, current: float) -> bool:
        if previous is None:
            return False
        return previous > 0 and current <= 0

    @property
    def _inside_sign(self) -> int:
        return -1 if self.inside_side == "left" else 1

    @property
    def _source_side(self) -> int:
        return (
            -self._inside_sign
            if self.direction != "inside_to_outside"
            else self._inside_sign
        )

    @property
    def _target_side(self) -> int:
        return -self._source_side

    def _side(self, point: tuple[float, float]) -> int:
        dx = self.line_end[0] - self.line_start[0]
        dy = self.line_end[1] - self.line_start[1]
        cross = dx * (point[1] - self.line_start[1]) - dy * (point[0] - self.line_start[0])
        distance = cross / self._line_length
        if abs(distance) <= self.line_hysteresis:
            return 0
        return 1 if distance > 0 else -1

    def _direction_matches(self, movement: tuple[float, float]) -> bool:
        if self._expected_vector is None:
            return True
        length = math.hypot(*movement)
        expected_length = math.hypot(*self._expected_vector)
        if length == 0 or expected_length == 0:
            return False
        dot = (
            movement[0] * self._expected_vector[0]
            + movement[1] * self._expected_vector[1]
        ) / (length * expected_length)
        return dot >= self.direction_cosine_threshold


def _validate_bbox(detection: Mapping[str, Any]) -> list[float]:
    raw = detection.get("bbox")
    if raw is None or len(raw) != 4:
        raise ValueError("detection bbox must contain four numeric values")
    values = [_finite_float(item, "bbox") for item in raw]
    if values[2] < values[0] or values[3] < values[1]:
        raise ValueError("detection bbox must satisfy x2 >= x1 and y2 >= y1")
    return values


def _bbox_centroid(bbox: list[float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def _bottom_center(bbox: list[float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0, bbox[3])


def _point(value: Sequence[Real], name: str) -> tuple[float, float]:
    if len(value) != 2:
        raise ValueError(f"{name} must contain two numeric values")
    point = (_finite_float(value[0], name), _finite_float(value[1], name))
    return point


def _polygon(value: Sequence[Sequence[Real]]) -> list[tuple[float, float]]:
    if len(value) < 3:
        raise ValueError("inside_polygon must contain at least three points")
    return [_point(item, "inside_polygon") for item in value]


def _region_points(config: Mapping[str, Any]) -> list[tuple[float, float]]:
    if not isinstance(config, Mapping):
        raise ValueError("region config must be an object")
    raw = config.get("points")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise ValueError("region config must contain exactly four points")
    points = [_point(item, "region") for item in raw]
    if any(not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0) for x, y in points):
        raise ValueError("region coordinates must be between 0 and 1")
    area = abs(
        sum(
            points[index][0] * points[(index + 1) % 4][1]
            - points[(index + 1) % 4][0] * points[index][1]
            for index in range(4)
        )
        / 2.0
    )
    if area <= 1e-9:
        raise ValueError("region config must have a non-zero area")
    if _segments_intersect(points[0], points[1], points[2], points[3]) or _segments_intersect(
        points[1], points[2], points[3], points[0]
    ):
        raise ValueError("region config must not self-intersect")
    return points


def generate_region_line(points: Sequence[Sequence[Real]]) -> list[list[float]]:
    normalized = _region_points({"points": points})
    line = _region_line([[x, y] for x, y in normalized])
    return [[float(value) for value in point] for point in line]


def _region_line(points: Sequence[Sequence[Real]]) -> list[list[float]]:
    p0, p1, p2, p3 = [[float(value) for value in point] for point in points]

    def interpolate(start: list[float], end: list[float], fraction: float) -> list[float]:
        return [
            start[index] + (end[index] - start[index]) * fraction
            for index in range(2)
        ]

    return [interpolate(p0, p3, 1 / 3), interpolate(p1, p2, 1 / 3)]


def _segments_intersect(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
) -> bool:
    def orientation(
        a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
    ) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    epsilon = 1e-9
    first = orientation(first_start, first_end, second_start)
    second = orientation(first_start, first_end, second_end)
    third = orientation(second_start, second_end, first_start)
    fourth = orientation(second_start, second_end, first_end)
    return (first * second < -epsilon) and (third * fourth < -epsilon)


def _geometry_points(
    config: Mapping[str, Any],
    width: int,
    height: int,
    *,
    expected: int | None = None,
) -> list[list[float]]:
    raw = config.get("points") or config.get("polygon")
    if raw is None and expected == 2:
        raw = [config.get("line_start"), config.get("line_end")]
    if not isinstance(raw, (list, tuple)):
        raise ValueError("geometry points must be a list")
    if expected is not None and len(raw) != expected:
        raise ValueError(f"geometry requires {expected} points")
    points = [[_finite_float(item[0], "geometry"), _finite_float(item[1], "geometry")] for item in raw]
    coordinate_space = str(config.get("coordinate_space") or "normalized").strip().lower()
    if coordinate_space in {"normalized", "relative", "ratio"}:
        return [[point[0] * width, point[1] * height] for point in points]
    if coordinate_space in {"pixel", "pixels"}:
        return points
    raise ValueError("coordinate_space must be normalized or pixel")


def _point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
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


def _parse_direction(direction: str | Sequence[Real]) -> tuple[str, tuple[float, float] | None]:
    if isinstance(direction, str):
        value = direction.strip().lower()
        if value in {"outside_to_inside", "entry", "inbound"}:
            return "outside_to_inside", None
        if value in {"inside_to_outside", "exit", "outbound"}:
            return "inside_to_outside", None
        if value == "toward_first_edge":
            return value, None
        vectors = {
            "left_to_right": (1.0, 0.0),
            "right_to_left": (-1.0, 0.0),
            "top_to_bottom": (0.0, 1.0),
            "bottom_to_top": (0.0, -1.0),
        }
        if value in vectors:
            return value, vectors[value]
        raise ValueError("unsupported person entry direction")
    if len(direction) != 2:
        raise ValueError("direction vector must contain two values")
    vector = (_finite_float(direction[0], "direction"), _finite_float(direction[1], "direction"))
    if vector == (0.0, 0.0):
        raise ValueError("direction vector must be non-zero")
    return "custom", vector


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


def _non_negative_float(value: Any, name: str) -> float:
    result = _finite_float(value, name)
    if result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result


def _bounded_float(value: Any, name: str, *, upper: float) -> float:
    result = _finite_float(value, name)
    if not 0.0 <= result < upper:
        raise ValueError(f"{name} must satisfy 0 <= value < {upper}")
    return result


__all__ = [
    "NearestCentroidTracker",
    "PersonEntryDetector",
    "PersonEntryEvent",
    "generate_region_line",
]
