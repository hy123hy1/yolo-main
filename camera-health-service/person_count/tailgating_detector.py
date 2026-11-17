from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from numbers import Real
from typing import Any, Mapping, Sequence

from person_count.entry_tracker import PersonEntryDetector, PersonEntryEvent


@dataclass(frozen=True, slots=True)
class TailgatingEvent:
    """One follower that entered immediately behind another person."""

    behavior_code: str
    leader_track_id: Any
    follower_track_id: Any
    confidence: float
    line_id: str
    direction: str
    event_timestamp: Any
    bbox: list[float]
    time_gap_seconds: float
    person_gap: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "behavior_code": self.behavior_code,
            "leader_track_id": self.leader_track_id,
            "follower_track_id": self.follower_track_id,
            "confidence": self.confidence,
            "line_id": self.line_id,
            "direction": self.direction,
            "event_timestamp": self.event_timestamp,
            "bbox": list(self.bbox),
            "time_gap_seconds": self.time_gap_seconds,
            "person_gap": self.person_gap,
        }


class TailgatingDetector:
    """Detect a second track passing shortly after a first track.

    The detector deliberately stays independent from persistence.  It uses the
    existing directed entry state machine for the actual crossing decision and
    only applies the tailgating time/distance rule to completed crossings.
    """

    def __init__(
        self,
        entry_detector: PersonEntryDetector,
        *,
        frame_width: int,
        frame_height: int,
        max_follow_seconds: float = 2.0,
        max_person_gap: float = 0.25,
        cooldown_seconds: float = 5.0,
    ) -> None:
        if int(frame_width) <= 0 or int(frame_height) <= 0:
            raise ValueError("frame dimensions must be greater than 0")
        self.entry_detector = entry_detector
        self.frame_width = int(frame_width)
        self.frame_height = int(frame_height)
        self.max_follow_seconds = _non_negative_float(
            max_follow_seconds,
            "max_follow_seconds",
        )
        self.max_person_gap = _non_negative_float(max_person_gap, "max_person_gap")
        self.cooldown_seconds = _non_negative_float(cooldown_seconds, "cooldown_seconds")
        self._recent_passages: list[tuple[PersonEntryEvent, float]] = []
        self._last_event_seconds: float | None = None

    @classmethod
    def from_region_config(
        cls,
        region_config: Mapping[str, Any],
        *,
        frame_width: int,
        frame_height: int,
        max_follow_seconds: float = 2.0,
        max_person_gap: float = 0.25,
        cooldown_seconds: float = 5.0,
    ) -> "TailgatingDetector":
        entry_detector = PersonEntryDetector.from_region_config(
            region_config,
            frame_width=frame_width,
            frame_height=frame_height,
            cooldown_seconds=0.0,
        )
        return cls(
            entry_detector,
            frame_width=frame_width,
            frame_height=frame_height,
            max_follow_seconds=max_follow_seconds,
            max_person_gap=max_person_gap,
            cooldown_seconds=cooldown_seconds,
        )

    @classmethod
    def from_config(
        cls,
        line_config: Mapping[str, Any],
        region_config: Mapping[str, Any] | None,
        *,
        frame_width: int,
        frame_height: int,
        direction: str | None = None,
        max_follow_seconds: float = 2.0,
        max_person_gap: float = 0.25,
        cooldown_seconds: float = 5.0,
    ) -> "TailgatingDetector":
        entry_detector = PersonEntryDetector.from_config(
            line_config,
            region_config,
            frame_width=frame_width,
            frame_height=frame_height,
            direction=direction,
            cooldown_seconds=0.0,
        )
        return cls(
            entry_detector,
            frame_width=frame_width,
            frame_height=frame_height,
            max_follow_seconds=max_follow_seconds,
            max_person_gap=max_person_gap,
            cooldown_seconds=cooldown_seconds,
        )

    def update(
        self,
        detections: Sequence[Mapping[str, Any]],
        timestamp: Real | datetime,
    ) -> list[TailgatingEvent]:
        timestamp_seconds = _timestamp_seconds(timestamp)
        cutoff = timestamp_seconds - self.max_follow_seconds
        self._recent_passages = [
            item for item in self._recent_passages if item[1] >= cutoff
        ]
        crossings = self.entry_detector.update(detections, timestamp)
        events: list[TailgatingEvent] = []
        for crossing in crossings:
            current_id = str(crossing.track_id)
            candidate: tuple[PersonEntryEvent, float] | None = None
            for previous in reversed(self._recent_passages):
                previous_event, previous_seconds = previous
                if str(previous_event.track_id) == current_id:
                    continue
                if timestamp_seconds < previous_seconds:
                    continue
                if timestamp_seconds - previous_seconds > self.max_follow_seconds:
                    break
                candidate = previous
                break

            if candidate is not None:
                previous_event, previous_seconds = candidate
                person_gap = _normalized_bbox_distance(
                    previous_event.bbox,
                    crossing.bbox,
                    self.frame_width,
                    self.frame_height,
                )
                time_gap = timestamp_seconds - previous_seconds
                allowed = (
                    person_gap <= self.max_person_gap
                    and (
                        self._last_event_seconds is None
                        or timestamp_seconds - self._last_event_seconds
                        >= self.cooldown_seconds
                    )
                )
                if allowed:
                    events.append(
                        TailgatingEvent(
                            behavior_code="tailgating",
                            leader_track_id=previous_event.track_id,
                            follower_track_id=crossing.track_id,
                            confidence=min(
                                float(previous_event.confidence),
                                float(crossing.confidence),
                            ),
                            line_id=crossing.line_id,
                            direction=crossing.direction,
                            event_timestamp=crossing.crossing_timestamp,
                            bbox=list(crossing.bbox),
                            time_gap_seconds=time_gap,
                            person_gap=person_gap,
                        )
                    )
                    self._last_event_seconds = timestamp_seconds

            self._recent_passages.append((crossing, timestamp_seconds))
        return events


def _normalized_bbox_distance(
    first: Sequence[Real],
    second: Sequence[Real],
    width: int,
    height: int,
) -> float:
    first_point = ((float(first[0]) + float(first[2])) / 2.0, float(first[3]))
    second_point = ((float(second[0]) + float(second[2])) / 2.0, float(second[3]))
    dx = (first_point[0] - second_point[0]) / float(width)
    dy = (first_point[1] - second_point[1]) / float(height)
    return math.hypot(dx, dy)


def _timestamp_seconds(value: Real | datetime) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("timestamp must be finite numeric")
    return result


def _non_negative_float(value: Any, name: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result


__all__ = ["TailgatingDetector", "TailgatingEvent"]
