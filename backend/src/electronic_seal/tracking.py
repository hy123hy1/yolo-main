"""Small, independent tracking helpers for electronic-seal algorithms."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
from collections.abc import Iterable, Mapping
from numbers import Real
from typing import Any


_EXACT_ASSIGNMENT_LIMIT = 10


def _copy_detection(detection: Mapping[str, Any] | Any) -> dict[str, Any]:
    if not hasattr(detection, "get"):
        raise ValueError("each detection must be dict-like")
    try:
        return dict(detection)
    except (TypeError, ValueError) as exc:
        raise ValueError("each detection must be dict-like") from exc


def validate_bbox(detection: Mapping[str, Any]) -> list[float]:
    """Return a validated ``[x1, y1, x2, y2]`` bounding box."""

    try:
        raw_bbox = detection.get("bbox")
        if raw_bbox is None or len(raw_bbox) != 4:
            raise ValueError
        bbox = [float(value) for value in raw_bbox]
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("detection bbox must contain four numeric values") from exc

    if not all(math.isfinite(value) for value in bbox):
        raise ValueError("detection bbox values must be finite")
    if bbox[2] < bbox[0] or bbox[3] < bbox[1]:
        raise ValueError("detection bbox must satisfy x2 >= x1 and y2 >= y1")
    return bbox


def bbox_centroid(bbox: list[float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def _stable_value_key(value: Any) -> tuple[str, str]:
    value_type = type(value)
    return (
        f"{value_type.__module__}.{value_type.__qualname__}",
        repr(value),
    )


def _pending_geometry_key(
    pending_item: tuple[int, dict[str, Any], list[float], tuple[float, float]],
) -> tuple[Any, ...]:
    _index, detection, bbox, centroid = pending_item
    return (
        tuple(bbox),
        tuple(centroid),
        _stable_value_key(detection.get("label")),
        _stable_value_key(detection.get("confidence")),
    )


def _assignment_is_better(
    candidate: tuple[int, float, tuple[int, ...]],
    current: tuple[int, float, tuple[int, ...]],
) -> bool:
    candidate_rank = (-candidate[0], candidate[1], candidate[2])
    current_rank = (-current[0], current[1], current[2])
    return candidate_rank < current_rank


def _exact_anonymous_assignment(
    candidate_tracks: list[tuple[Any, _Track]],
    pending: list[tuple[int, dict[str, Any], list[float], tuple[float, float]]],
    max_match_distance: float,
) -> list[tuple[Any, tuple[int, dict[str, Any], list[float], tuple[float, float]]]]:
    """Find a deterministic maximum-cardinality, minimum-distance assignment."""

    tracks = sorted(candidate_tracks, key=lambda item: _stable_value_key(item[0]))
    detections = sorted(pending, key=_pending_geometry_key)
    unmatched_rank = len(detections)

    @lru_cache(maxsize=None)
    def best(track_position: int, used_detection_mask: int) -> tuple[int, float, tuple[int, ...]]:
        if track_position == len(tracks):
            return (0, 0.0, ())

        skipped = best(track_position + 1, used_detection_mask)
        result = (skipped[0], skipped[1], (unmatched_rank,) + skipped[2])
        _track_id, track = tracks[track_position]

        for detection_position, pending_item in enumerate(detections):
            if used_detection_mask & (1 << detection_position):
                continue
            distance = math.dist(pending_item[3], track.centroid)
            if distance > max_match_distance:
                continue
            remainder = best(
                track_position + 1,
                used_detection_mask | (1 << detection_position),
            )
            candidate = (
                remainder[0] + 1,
                remainder[1] + distance,
                (detection_position,) + remainder[2],
            )
            if _assignment_is_better(candidate, result):
                result = candidate
        return result

    assignment = best(0, 0)[2]
    return [
        (track_id, detections[detection_position])
        for (track_id, _track), detection_position in zip(tracks, assignment)
        if detection_position != unmatched_rank
    ]


def _polynomial_anonymous_assignment(
    candidate_tracks: list[tuple[Any, _Track]],
    pending: list[tuple[int, dict[str, Any], list[float], tuple[float, float]]],
    max_match_distance: float,
) -> list[tuple[Any, tuple[int, dict[str, Any], list[float], tuple[float, float]]]]:
    """Use a bounded polynomial assignment for larger anonymous frames."""

    tracks = sorted(candidate_tracks, key=lambda item: _stable_value_key(item[0]))
    detections = sorted(pending, key=_pending_geometry_key)
    track_count = len(tracks)
    detection_count = len(detections)
    if not track_count or not detection_count:
        return []

    unmatched_cost = max(max_match_distance, 1.0) + 1.0
    invalid_cost = unmatched_cost * 3.0
    matrix_size = track_count + detection_count
    costs = [[invalid_cost for _ in range(matrix_size)] for _ in range(matrix_size)]

    for track_position, (_track_id, track) in enumerate(tracks):
        for detection_position, pending_item in enumerate(detections):
            distance = math.dist(pending_item[3], track.centroid)
            costs[track_position][detection_position] = (
                distance if distance <= max_match_distance else invalid_cost
            )
        for dummy_position in range(detection_count, matrix_size):
            costs[track_position][dummy_position] = unmatched_cost

    for dummy_track_position in range(track_count, matrix_size):
        for detection_position in range(detection_count):
            costs[dummy_track_position][detection_position] = 0.0
        for dummy_position in range(detection_count, matrix_size):
            costs[dummy_track_position][dummy_position] = 0.0

    assignment = _hungarian_min_cost_assignment(costs)
    matches = []
    for track_position, detection_position in enumerate(assignment[:track_count]):
        if detection_position >= detection_count:
            continue
        track_id, track = tracks[track_position]
        pending_item = detections[detection_position]
        if math.dist(pending_item[3], track.centroid) <= max_match_distance:
            matches.append((track_id, pending_item))
    return matches


def _hungarian_min_cost_assignment(costs: list[list[float]]) -> list[int]:
    """Return one deterministic minimum-cost column for each matrix row."""

    size = len(costs)
    if size == 0 or any(len(row) != size for row in costs):
        raise ValueError("assignment cost matrix must be non-empty and square")

    potentials_rows = [0.0] * (size + 1)
    potentials_columns = [0.0] * (size + 1)
    matching = [0] * (size + 1)
    previous_column = [0] * (size + 1)

    for row in range(1, size + 1):
        matching[0] = row
        column = 0
        minimums = [math.inf] * (size + 1)
        used = [False] * (size + 1)
        while True:
            used[column] = True
            matched_row = matching[column]
            delta = math.inf
            next_column = 0
            for candidate in range(1, size + 1):
                if used[candidate]:
                    continue
                reduced_cost = (
                    costs[matched_row - 1][candidate - 1]
                    - potentials_rows[matched_row]
                    - potentials_columns[candidate]
                )
                if reduced_cost < minimums[candidate]:
                    minimums[candidate] = reduced_cost
                    previous_column[candidate] = column
                if minimums[candidate] < delta:
                    delta = minimums[candidate]
                    next_column = candidate
            for candidate in range(size + 1):
                if used[candidate]:
                    potentials_rows[matching[candidate]] += delta
                    potentials_columns[candidate] -= delta
                else:
                    minimums[candidate] -= delta
            column = next_column
            if matching[column] == 0:
                break
        while True:
            previous = previous_column[column]
            matching[column] = matching[previous]
            column = previous
            if column == 0:
                break

    assignment = [-1] * size
    for column in range(1, size + 1):
        if matching[column] > 0:
            assignment[matching[column] - 1] = column - 1
    return assignment


def _anonymous_assignment(
    candidate_tracks: list[tuple[Any, _Track]],
    pending: list[tuple[int, dict[str, Any], list[float], tuple[float, float]]],
    max_match_distance: float,
) -> list[tuple[Any, tuple[int, dict[str, Any], list[float], tuple[float, float]]]]:
    if max(len(candidate_tracks), len(pending)) <= _EXACT_ASSIGNMENT_LIMIT:
        return _exact_anonymous_assignment(candidate_tracks, pending, max_match_distance)
    return _polynomial_anonymous_assignment(candidate_tracks, pending, max_match_distance)


@dataclass
class _Track:
    track_id: Any
    centroid: tuple[float, float]
    bbox: list[float]
    missed_frames: int = 0


class NearestCentroidTracker:
    """Assign stable IDs with bounded nearest-centroid matching.

    Tracks survive ``max_missed_frames`` calls that contain no matching
    detection. Existing upstream IDs are preserved when present; this class
    does not read or mutate any AI-task tracker state.
    """

    def __init__(
        self,
        *,
        max_match_distance: float = 120.0,
        max_missed_frames: int = 1,
        id_prefix: str = "track",
    ) -> None:
        if not math.isfinite(float(max_match_distance)) or max_match_distance <= 0:
            raise ValueError("max_match_distance must be a positive finite number")
        if not isinstance(max_missed_frames, int) or max_missed_frames < 0:
            raise ValueError("max_missed_frames must be a non-negative integer")
        if not id_prefix:
            raise ValueError("id_prefix must not be empty")

        self.max_match_distance = float(max_match_distance)
        self.max_missed_frames = max_missed_frames
        self.id_prefix = str(id_prefix)
        self._tracks: dict[Any, _Track] = {}
        self._next_id = 1

    @property
    def active_track_ids(self) -> frozenset[Any]:
        """Return a read-only snapshot of tracks retained after the last update."""

        return frozenset(self._tracks)

    def update(self, detections: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if detections is None:
            raise ValueError("detections must be an iterable")

        normalized: list[tuple[dict[str, Any], list[float], tuple[float, float], Any]] = []
        for raw_detection in detections:
            detection = _copy_detection(raw_detection)
            bbox = validate_bbox(detection)
            detection["bbox"] = bbox
            centroid = bbox_centroid(bbox)
            external_id = detection.get("track_id")
            if external_id is not None:
                try:
                    hash(external_id)
                except TypeError as exc:
                    raise ValueError("track_id must be hashable") from exc
                if isinstance(external_id, Real):
                    try:
                        if not math.isfinite(float(external_id)):
                            raise ValueError("track_id numeric values must be finite")
                    except (TypeError, ValueError, OverflowError) as exc:
                        raise ValueError("track_id numeric values must be finite") from exc
            normalized.append((detection, bbox, centroid, external_id))

        explicit_ids = [external_id for *_, external_id in normalized if external_id is not None]
        if len(explicit_ids) != len(set(explicit_ids)):
            raise ValueError("duplicate track_id values in one update")

        results_by_index: dict[int, dict[str, Any]] = {}
        used_ids: set[Any] = set()
        pending: list[tuple[int, dict[str, Any], list[float], tuple[float, float]]] = []

        # Explicit IDs take precedence, so an upstream tracker can be used
        # without allowing a nearby anonymous detection to steal its track.
        for index, (detection, bbox, centroid, external_id) in enumerate(normalized):
            if external_id is None:
                pending.append((index, detection, bbox, centroid))
                continue
            track = self._tracks.get(external_id)
            if track is None:
                track = _Track(external_id, centroid, bbox)
                self._tracks[external_id] = track
            track.centroid = centroid
            track.bbox = bbox
            track.missed_frames = 0
            used_ids.add(external_id)
            detection["track_id"] = external_id
            results_by_index[index] = detection

        candidate_tracks = [
            (track_id, track)
            for track_id, track in self._tracks.items()
            if track_id not in used_ids and track.missed_frames <= self.max_missed_frames
        ]
        matched_pending: set[int] = set()
        matched_tracks: set[Any] = set()
        for track_id, pending_item in _anonymous_assignment(
            candidate_tracks,
            pending,
            self.max_match_distance,
        ):
            _index, detection, bbox, centroid = pending_item
            if _index in matched_pending or track_id in matched_tracks:
                continue
            track = self._tracks[track_id]
            track.centroid = centroid
            track.bbox = bbox
            track.missed_frames = 0
            detection["track_id"] = track_id
            results_by_index[_index] = detection
            matched_pending.add(_index)
            matched_tracks.add(track_id)
            used_ids.add(track_id)

        for index, detection, bbox, centroid in pending:
            if index in matched_pending:
                continue
            track_id = self._new_track_id()
            self._tracks[track_id] = _Track(track_id, centroid, bbox)
            used_ids.add(track_id)
            detection["track_id"] = track_id
            results_by_index[index] = detection

        for track_id, track in self._tracks.items():
            if track_id not in used_ids:
                track.missed_frames += 1

        self._tracks = {
            track_id: track
            for track_id, track in self._tracks.items()
            if track.missed_frames <= self.max_missed_frames
        }

        # Preserve input order after processing explicit and anonymous IDs.
        return [results_by_index[index] for index in range(len(normalized))]

    def _new_track_id(self) -> str:
        while True:
            track_id = f"{self.id_prefix}-{self._next_id}"
            self._next_id += 1
            if track_id not in self._tracks:
                return track_id
