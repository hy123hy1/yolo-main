from __future__ import annotations

from datetime import datetime
import math
from numbers import Real
from typing import Any

import numpy as np

from src.electronic_seal.algorithms.room_empty import (
    DEFAULT_MAX_FRAME_GAP_SECONDS,
    RoomEmptyDetector,
)
from src.electronic_seal.algorithms.truck_departure import TruckDepartureDetector
from src.electronic_seal.evidence import EvidenceService


DEFAULT_ROOM_EMPTY_HOLD_SECONDS = 600.0
RULE_ALGORITHMS = frozenset({"truck_departure", "room_empty"})


def _timestamp_seconds(value: Real | datetime) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            result = parsed.timestamp()
            if math.isfinite(result):
                return result
    if isinstance(value, Real) and not isinstance(value, bool):
        result = float(value)
        if math.isfinite(result):
            return result
    raise ValueError("timestamp must be a finite number or datetime")


def _event_datetime(value: Real | datetime) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromtimestamp(_timestamp_seconds(value))


def _config_value(config: dict[str, Any], key: str, default: float) -> float:
    value = (config.get("threshold_config_json") or {}).get(key, config.get(key, default))
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)


def _scaled_points(value: Any, *, expected: int | None = None) -> list[list[float]]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("geometry points must be a list")
    points = []
    for raw in value:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise ValueError("geometry point must contain two values")
        points.append([float(raw[0]), float(raw[1])])
    if expected is not None and len(points) != expected:
        raise ValueError(f"geometry requires {expected} points")
    return points


def _geometry_points(value: Any, width: int, height: int, *, expected: int | None = None) -> list[list[float]]:
    if not isinstance(value, dict):
        raise ValueError("geometry configuration must be an object")
    coordinate_space = str(value.get("coordinate_space") or "normalized").lower()
    raw = value.get("points") or value.get("polygon")
    if raw is None and expected == 2:
        raw = [value.get("line_start"), value.get("line_end")]
    points = _scaled_points(raw, expected=expected)
    if coordinate_space in {"normalized", "relative", "ratio"}:
        points = [[point[0] * width, point[1] * height] for point in points]
    elif coordinate_space not in {"pixel", "pixels"}:
        raise ValueError("coordinate_space must be normalized or pixel")
    return points


class ElectronicSealDetectionRunner:
    """Run shared-model inference once per frame and dispatch labels to rules."""

    def __init__(
        self,
        *,
        channel_id: str,
        configs: list[dict[str, Any]],
        inference: Any,
        store: Any,
        evidence_service: EvidenceService | None = None,
    ) -> None:
        self.channel_id = str(channel_id)
        self.inference = inference
        self.store = store
        self.evidence_service = evidence_service
        self.configs: list[dict[str, Any]] = []
        self._detectors: dict[str, Any] = {}
        self._geometry_size: tuple[int, int] | None = None
        self._configuration_error = False
        self._last_inference_seconds: float | None = None
        self.update_configs(configs)

    def update_configs(self, configs: list[dict[str, Any]]) -> None:
        self.configs = [dict(config) for config in configs]
        self._detectors = {}
        self._geometry_size = None
        self._configuration_error = False
        self._last_inference_seconds = None

    def process(self, frame: np.ndarray, timestamp: Real | datetime) -> dict[str, Any]:
        self.__frame = frame
        timestamp_seconds = _timestamp_seconds(timestamp)
        configs = [
            config
            for config in self.configs
            if str(config.get("algorithm_type")) in RULE_ALGORITHMS
        ]
        if not configs:
            return {"ran": False, "reason": "no_detection_algorithms", "events": []}
        if any(not str(config.get("model_id") or "").strip() for config in configs):
            return self._waiting_config(configs, timestamp, "model_not_configured")
        if self._last_inference_seconds is not None:
            sample_interval = min(
                max(_config_value(config, "sample_interval_ms", 1000.0), 1.0) / 1000.0
                for config in configs
            )
            if timestamp_seconds - self._last_inference_seconds < sample_interval:
                return {"ran": False, "reason": "sample_interval", "events": []}
        self._last_inference_seconds = timestamp_seconds

        height, width = frame.shape[:2]
        if self._geometry_size != (width, height):
            self._build_detectors(configs, width, height)
            self._geometry_size = (width, height)
        if self._configuration_error:
            return self._waiting_config(configs, timestamp, "geometry_not_configured")

        model_thresholds: dict[str, float] = {}
        for config in configs:
            model_id = str(config["model_id"])
            threshold = _config_value(config, "confidence_threshold", 0.25)
            model_thresholds[model_id] = min(model_thresholds.get(model_id, 1.0), threshold)
        detections_by_model = {
            model_id: self.inference.detect(
                model_id,
                frame,
                conf_threshold=threshold,
            )
            for model_id, threshold in model_thresholds.items()
        }

        persisted_events: list[dict[str, Any]] = []
        for config in configs:
            algorithm_type = str(config["algorithm_type"])
            threshold = _config_value(config, "confidence_threshold", 0.25)
            detections = [
                detection
                for detection in detections_by_model[str(config["model_id"])]
                if float(detection.get("confidence", 0.0)) >= threshold
            ]
            detector = self._detectors[algorithm_type]
            if algorithm_type == "truck_departure":
                rule_results = detector.update(detections, timestamp)
                for result in rule_results:
                    persisted_events.append(
                        self._persist_event(
                            config,
                            timestamp,
                            result.to_dict(),
                            detections,
                            event_type="truck_departure",
                            score=float(result.confidence),
                        )
                    )
                self.store.save_state(
                    {
                        "channel_id": self.channel_id,
                        "algorithm_type": algorithm_type,
                        "status": "normal",
                        "current_score": max(
                            (float(item.get("confidence", 0.0)) for item in detections),
                            default=0.0,
                        ),
                        "last_metrics_json": {"detection_count": len(detections)},
                        "runtime_json": {"model_id": config["model_id"]},
                        "last_run_at": _event_datetime(timestamp),
                    }
                )
                continue

            update = detector.update(detections, timestamp)
            state = self.store.get_state(self.channel_id, algorithm_type) or {}
            active_event_id = state.get("last_event_id")
            if update.events:
                event = update.events[0]
                persisted = self._persist_event(
                    config,
                    timestamp,
                    event.to_dict(),
                    detections,
                    event_type="room_empty",
                    score=0.0,
                )
                persisted_events.append(persisted)
                active_event_id = persisted["id"]
            if update.recovered and active_event_id:
                self.store.close_event(
                    active_event_id,
                    _event_datetime(timestamp),
                    max(
                        _timestamp_seconds(timestamp)
                        - _timestamp_seconds(state.get("bad_since") or timestamp),
                        0.0,
                    ),
                )
                active_event_id = None
            self.store.save_state(
                {
                    "channel_id": self.channel_id,
                    "algorithm_type": algorithm_type,
                    "status": "alarming" if update.active_empty else "normal",
                    "current_score": float(update.person_count),
                    "last_metrics_json": {
                        "person_count": update.person_count,
                        "detection_count": len(detections),
                    },
                    "runtime_json": {
                        "model_id": config["model_id"],
                        "empty_since": event.to_dict()["empty_since"] if update.events else None,
                    },
                    "bad_since": _event_datetime(update.empty_since) if update.active_empty and update.empty_since else None,
                    "last_event_id": active_event_id,
                    "last_run_at": _event_datetime(timestamp),
                }
            )
        return {"ran": True, "reason": None, "events": persisted_events}

    def _waiting_config(
        self,
        configs: list[dict[str, Any]],
        timestamp: Real | datetime,
        reason: str,
    ) -> dict[str, Any]:
        for config in configs:
            save_state = getattr(self.store, "save_state", None)
            if not callable(save_state):
                continue
            get_state = getattr(self.store, "get_state", None)
            previous = (
                get_state(self.channel_id, config["algorithm_type"])
                if callable(get_state)
                else None
            )
            previous_reason = ((previous or {}).get("last_metrics_json") or {}).get("reason")
            if (previous or {}).get("status") == "waiting_config" and previous_reason == reason:
                continue
            save_state(
                {
                    "channel_id": self.channel_id,
                    "algorithm_type": config["algorithm_type"],
                    "status": "waiting_config",
                    "current_score": 0.0,
                    "last_metrics_json": {"reason": reason},
                    "runtime_json": {"configuration_state": reason},
                    "last_run_at": _event_datetime(timestamp),
                }
            )
        return {"ran": False, "reason": "waiting_config", "detail": reason, "events": []}

    def _build_detectors(self, configs: list[dict[str, Any]], width: int, height: int) -> None:
        self._detectors = {}
        try:
            for config in configs:
                algorithm_type = str(config["algorithm_type"])
                if algorithm_type == "truck_departure":
                    line = _geometry_points(config.get("line_config_json"), width, height, expected=2)
                    line_config = config.get("line_config_json") or {}
                    self._detectors[algorithm_type] = TruckDepartureDetector(
                        line[0],
                        line[1],
                        line_id=str(line_config.get("line_id") or "departure-line"),
                        inside_side=str(line_config.get("inside_side") or "left"),
                        direction=line_config.get("direction") or "inside_to_outside",
                        cooldown_seconds=_config_value(config, "cooldown_seconds", 0.0),
                    )
                elif algorithm_type == "room_empty":
                    region = config.get("region_config_json") or {}
                    polygon = _geometry_points(region, width, height)
                    hold_seconds = float(
                        region.get(
                            "hold_seconds",
                            _config_value(config, "empty_hold_seconds", DEFAULT_ROOM_EMPTY_HOLD_SECONDS),
                        )
                    )
                    self._detectors[algorithm_type] = RoomEmptyDetector(
                        region_id=str(region.get("region_id") or "dispatch-room"),
                        polygon=polygon,
                        hold_seconds=hold_seconds,
                        max_frame_gap_seconds=_config_value(
                            config,
                            "max_frame_gap_seconds",
                            DEFAULT_MAX_FRAME_GAP_SECONDS,
                        ),
                    )
        except (TypeError, ValueError, KeyError):
            self._detectors = {}
            self._configuration_error = True

    def _persist_event(
        self,
        config: dict[str, Any],
        timestamp: Real | datetime,
        rule_event: dict[str, Any],
        detections: list[dict[str, Any]],
        *,
        event_type: str,
        score: float,
    ) -> dict[str, Any]:
        event = self.store.create_event(
            {
                "channel_id": self.channel_id,
                "algorithm_type": event_type,
                "event_type": event_type,
                "severity": "alarm",
                "started_at": _event_datetime(timestamp),
                "score": score,
                "enqueue_upload": event_type == "truck_departure",
                "metrics_json": {
                    "model_id": config.get("model_id"),
                    "rule": rule_event,
                    "detections": detections,
                },
            }
        )
        if (
            self.evidence_service is not None
            and bool(config.get("need_snapshot", True))
        ):
            try:
                snapshot = self.evidence_service.create_snapshot(
                    channel_id=self.channel_id,
                    event_id=event["id"],
                    frame=self._current_frame,
                    annotations={
                        "detections": detections,
                        "line": self._annotation_geometry(config, "line_config_json", expected=2),
                        "region": self._annotation_geometry(config, "region_config_json"),
                    },
                )
                updater = getattr(self.store, "update_event", None)
                if callable(updater):
                    event = updater(event["id"], {"evidence_snapshot_path": snapshot}) or event
            except Exception:
                # Evidence failure must not discard the alarm event.
                pass
        return event

    def _annotation_geometry(
        self,
        config: dict[str, Any],
        field: str,
        *,
        expected: int | None = None,
    ) -> Any:
        value = config.get(field) or {}
        if not value:
            return None
        frame = self._current_frame
        height, width = frame.shape[:2]
        try:
            return _geometry_points(value, width, height, expected=expected)
        except (TypeError, ValueError, KeyError):
            return None

    @property
    def _current_frame(self) -> np.ndarray:
        frame = getattr(self, "_ElectronicSealDetectionRunner__frame", None)
        if frame is None:
            raise RuntimeError("current frame is unavailable")
        return frame
