"""Conservative, per-task person tracking and ordered two-boundary counting."""

from math import hypot, isfinite


FLOW_REGION_TYPE = "person_flow"


def is_person_count_algorithm(algorithm: dict) -> bool:
    return (
        str(algorithm.get("rule_type") or algorithm.get("algorithm_type") or "").lower() == "count"
        and set(algorithm.get("labels") or []) == {"person"}
        and not algorithm.get("detect_all_classes", False)
    )


def flow_regions(algorithm: dict) -> list[dict]:
    if not is_person_count_algorithm(algorithm):
        return []
    return [region for region in algorithm.get("regions", [])
            if region.get("type") == FLOW_REGION_TYPE and region.get("enabled", True)]


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _sub(a, b):
    return a[0] - b[0], a[1] - b[1]


def validate_flow_region(region: dict):
    points = region.get("coordinates") or []
    if len(points) != 4 or any(
        not isinstance(point, (list, tuple)) or len(point) != 2
        or any(not isinstance(value, (int, float)) or not isfinite(value) or value < 0 for value in point)
        for point in points
    ):
        raise ValueError("person_flow requires exactly 4 ordered, finite, nonnegative points")
    turns = [_cross(_sub(points[(i + 1) % 4], points[i]),
                    _sub(points[(i + 2) % 4], points[(i + 1) % 4])) for i in range(4)]
    if not (all(turn > 1e-6 for turn in turns) or all(turn < -1e-6 for turn in turns)):
        raise ValueError("person_flow requires a nondegenerate convex quadrilateral in drawing order")


def validate_flow_algorithm(algorithm: dict, regions: list[dict]):
    marked = [region for region in regions if region.get("type") == FLOW_REGION_TYPE]
    if marked and not is_person_count_algorithm(algorithm):
        raise ValueError("person_flow is only supported by person-only count algorithms")
    for region in marked:
        validate_flow_region(region)
    if any(region.get("enabled", True) for region in marked) and any(
        region.get("enabled", True) and region.get("type") != FLOW_REGION_TYPE for region in regions
    ):
        raise ValueError("person_flow and ordinary regions cannot be mixed in the same enabled algorithm")


def _iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0


class PersonFlowCounter:
    # These are tracking safeguards, not new user-facing algorithm settings.
    MAX_GAP_SECONDS = 2.0
    LINE_DEADBAND_PIXELS = 2.0

    def __init__(self):
        self._tracks = {}
        self._next_id = 1
        self._geometry = None
        self._last_time = None

    def break_continuity(self):
        self._tracks.clear()
        self._last_time = None

    def update(self, detections: list[dict], regions: list[dict], now_ts: float) -> list[dict]:
        geometry = tuple((str(region.get("id")), tuple(tuple(p) for p in region["coordinates"])) for region in regions)
        if geometry != self._geometry or (
            self._last_time is not None and not 0 < now_ts - self._last_time <= self.MAX_GAP_SECONDS
        ):
            self.break_continuity()
        self._geometry, self._last_time = geometry, now_ts
        boxes = []
        for detection in detections:
            if detection.get("class_name") != "person":
                continue
            box = tuple(float(detection[key]) for key in ("x1", "y1", "x2", "y2"))
            if all(isfinite(value) for value in box) and box[2] > box[0] and box[3] > box[1]:
                boxes.append(box)

        candidates = {}
        for track_id, track in self._tracks.items():
            old = track["box"]
            for index, box in enumerate(boxes):
                scale = max(old[3] - old[1], box[3] - box[1], 1)
                distance = hypot((old[0] + old[2] - box[0] - box[2]) / 2,
                                 (old[1] + old[3] - box[1] - box[3]) / 2) / scale
                ratio = (box[2] - box[0]) * (box[3] - box[1]) / ((old[2] - old[0]) * (old[3] - old[1]))
                if distance <= 0.75 and 0.4 <= ratio <= 2.5:
                    candidates[track_id, index] = _iou(old, box) + (1 - distance) * 0.3

        # Mutual, unambiguous matches only. Lost/ambiguous identities cannot complete a crossing.
        matches = {}
        for track_id in self._tracks:
            ranked = sorted(((score, index) for (tid, index), score in candidates.items() if tid == track_id), reverse=True)
            if not ranked or (len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.1):
                continue
            score, index = ranked[0]
            competing = sorted(((value, tid) for (tid, idx), value in candidates.items() if idx == index), reverse=True)
            if competing[0][1] == track_id and (len(competing) == 1 or score - competing[1][0] >= 0.1):
                matches[index] = track_id

        next_tracks, crossings = {}, []
        for index, box in enumerate(boxes):
            track_id = matches.get(index)
            if track_id is None:
                track_id = self._next_id
                self._next_id += 1
                track = {"box": box, "regions": {}}
            else:
                track = self._tracks[track_id]
            point = ((box[0] + box[2]) / 2, box[3])
            for region in regions:
                state = track["regions"].setdefault(region["id"], {"point": None, "start": None})
                delta = self._advance(state, point, region["coordinates"])
                if delta:
                    crossings.append({"region_id": region["id"], "region_name": region.get("name"),
                                      "track_id": track_id, "delta": delta,
                                      "direction": "entry" if delta > 0 else "exit"})
            track["box"] = box
            next_tracks[track_id] = track
        self._tracks = next_tracks
        return crossings

    def _advance(self, state, point, points):
        center = (sum(p[0] for p in points) / 4, sum(p[1] for p in points) / 4)
        distances = []
        for index, a in enumerate(points):
            edge = _sub(points[(index + 1) % 4], a)
            length = hypot(*edge)
            sign = 1 if _cross(edge, _sub(center, a)) > 0 else -1
            distances.append(sign * _cross(edge, _sub(point, a)) / length)
        if any(abs(distances[index]) < self.LINE_DEADBAND_PIXELS for index in (1, 3)):
            state["point"], state["start"] = None, None
            return 0
        if any(abs(distances[index]) < self.LINE_DEADBAND_PIXELS for index in (0, 2)):
            return 0
        previous, state["point"] = state["point"], point
        if previous is None:
            return 0
        movement = _sub(point, previous)
        hits = []
        for index, a in enumerate(points):
            edge = _sub(points[(index + 1) % 4], a)
            denominator = _cross(movement, edge)
            if abs(denominator) < 1e-9:
                continue
            offset = _sub(a, previous)
            t, u = _cross(offset, edge) / denominator, _cross(offset, movement) / denominator
            if 0 < t <= 1 and 0 <= u <= 1:
                # Corner crossings do not establish an unambiguous entrance/exit.
                if min(u, 1 - u) < 1e-6:
                    state["start"] = None
                    return 0
                interior = _cross(edge, _sub(center, a))
                entering = _cross(edge, movement) * interior > 0
                hits.append((t, index, entering))
        delta = 0
        for _, edge, entering in sorted(hits):
            if edge in (1, 3):
                state["start"] = None
            elif entering:
                state["start"] = edge
            else:
                if state["start"] is not None and state["start"] != edge:
                    delta = 1 if state["start"] == 0 else -1
                state["start"] = None
        return delta
