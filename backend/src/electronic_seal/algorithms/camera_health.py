from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from typing import Any

import cv2
import numpy as np

from src.services.camera_health_defaults import (
    CAMERA_OCCLUSION,
    CAMERA_VIEW_SHIFT,
    load_camera_health_thresholds,
)
from src.services.camera_occlusion_detector import CameraOcclusionDetector
from src.services.camera_view_shift_detector import CameraViewShiftDetector


SUPPORTED_CAMERA_HEALTH_ALGORITHMS = frozenset({CAMERA_OCCLUSION, CAMERA_VIEW_SHIFT})


def _as_datetime(value: datetime | float | int | None) -> datetime:
    if value is None:
        return datetime.now()
    if isinstance(value, datetime):
        return value
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value))
    raise ValueError("timestamp must be a datetime or number")


def _parse_datetime(value: datetime | str | float | int | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value))
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _seconds_between(later: datetime, earlier: datetime | None) -> float:
    return max((later - earlier).total_seconds(), 0.0) if earlier else 0.0


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "unknown"


class ElectronicSealCameraHealthAdapter:
    """Run camera-health algorithms against electronic-seal-owned state."""

    def __init__(
        self,
        *,
        store: Any,
        baseline_dir: Path | str,
        thresholds: dict[str, dict[str, float | int]] | None = None,
    ) -> None:
        self.store = store
        self.baseline_dir = Path(baseline_dir)
        self.thresholds = thresholds or load_camera_health_thresholds()
        self._baselines: dict[tuple[str, str], np.ndarray | None] = {}
        self._detectors: dict[tuple[str, str], Any] = {}

    def run(
        self,
        config: dict[str, Any],
        frame: np.ndarray | None,
        now: datetime | float | int | None = None,
    ) -> dict[str, Any]:
        channel_id = str(config["channel_id"])
        algorithm_type = str(config["algorithm_type"])
        if algorithm_type not in SUPPORTED_CAMERA_HEALTH_ALGORITHMS:
            raise ValueError(f"unsupported electronic-seal camera-health algorithm: {algorithm_type}")
        timestamp = _as_datetime(now)
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            return self._save_unavailable(config, timestamp)

        baseline_created = False
        baseline = self._load_baseline(channel_id, algorithm_type)
        if baseline is None:
            self._save_baseline(channel_id, algorithm_type, frame, timestamp)
            baseline = frame.copy()
            self._baselines[(channel_id, algorithm_type)] = baseline
            baseline_created = True

        result = self._detector_for(config).analyze(frame, baseline)
        previous_state = self.store.get_state(channel_id, algorithm_type) or {}
        event_id = previous_state.get("last_event_id")
        event_created = False
        bad_since = _parse_datetime(previous_state.get("bad_since"))
        if result.is_bad:
            bad_since = bad_since or timestamp
            if not event_id:
                event = self.store.create_event(
                    {
                        "channel_id": channel_id,
                        "algorithm_type": algorithm_type,
                        "event_type": algorithm_type,
                        "started_at": bad_since,
                        "score": result.score,
                        "metrics_json": {"reason": result.reason, **result.metrics},
                    }
                )
                event_id = event["id"]
                event_created = True
            status = "alarming"
            last_good_at = None
        else:
            if event_id:
                self.store.close_event(
                    event_id,
                    timestamp,
                    _seconds_between(timestamp, bad_since),
                )
            event_id = None
            bad_since = None
            status = "normal"
            last_good_at = timestamp

        state = self.store.save_state(
            {
                "channel_id": channel_id,
                "algorithm_type": algorithm_type,
                "status": status,
                "current_score": result.score,
                "last_metrics_json": {"reason": result.reason, **result.metrics},
                "runtime_json": {
                    "baseline_created": baseline_created,
                    "baseline_source": "first_frame" if baseline_created else "persisted",
                },
                "last_run_at": timestamp,
                "bad_since": bad_since,
                "last_good_at": last_good_at,
                "last_event_id": event_id,
            }
        )
        return {
            "channel_id": channel_id,
            "algorithm_type": algorithm_type,
            "status": status,
            "ran": True,
            "event_id": event_id,
            "event_created": event_created,
            "result": result,
            "state": state,
        }

    def reset_baseline(self, algorithm_type: str, channel_id: str | None = None) -> None:
        keys = [
            key
            for key in self._baselines
            if key[1] == algorithm_type and (channel_id is None or key[0] == str(channel_id))
        ]
        for key in keys:
            self._baselines.pop(key, None)
            self._detectors.pop(key, None)

    def _detector_for(self, config: dict[str, Any]) -> Any:
        algorithm_type = str(config["algorithm_type"])
        key = (str(config["channel_id"]), algorithm_type)
        detector = self._detectors.get(key)
        if detector is not None:
            return detector
        thresholds = dict(self.thresholds.get(algorithm_type, {}))
        thresholds.update(config.get("threshold_config_json") or {})
        detector_class = (
            CameraOcclusionDetector
            if algorithm_type == CAMERA_OCCLUSION
            else CameraViewShiftDetector
        )
        detector = detector_class(thresholds)
        self._detectors[key] = detector
        return detector

    def _load_baseline(self, channel_id: str, algorithm_type: str) -> np.ndarray | None:
        key = (channel_id, algorithm_type)
        if key in self._baselines:
            return self._baselines[key]
        record = self.store.get_active_baseline(channel_id, algorithm_type)
        image = None
        if record and record.get("image_path"):
            image = cv2.imread(str(record["image_path"]), cv2.IMREAD_COLOR)
            if image is not None and image.size == 0:
                image = None
        self._baselines[key] = image
        return image

    def _save_baseline(
        self,
        channel_id: str,
        algorithm_type: str,
        frame: np.ndarray,
        captured_at: datetime,
    ) -> None:
        self.baseline_dir.mkdir(parents=True, exist_ok=True)
        path = self.baseline_dir / (
            f"baseline_{_safe_name(channel_id)}_{_safe_name(algorithm_type)}_"
            f"{int(captured_at.timestamp() * 1000)}.jpg"
        )
        if not cv2.imwrite(str(path), frame):
            raise RuntimeError(f"failed to save electronic-seal baseline: {path}")
        height, width = frame.shape[:2]
        self.store.replace_baseline(
            channel_id,
            algorithm_type,
            {
                "image_path": str(path),
                "width": width,
                "height": height,
                "quality_score": 1.0,
                "calibration_params_json": {"source": "first_frame"},
                "captured_at": captured_at,
            },
        )

    def _save_unavailable(self, config: dict[str, Any], timestamp: datetime) -> dict[str, Any]:
        channel_id = str(config["channel_id"])
        algorithm_type = str(config["algorithm_type"])
        state = self.store.save_state(
            {
                "channel_id": channel_id,
                "algorithm_type": algorithm_type,
                "status": "offline",
                "current_score": 0.0,
                "last_metrics_json": {"reason": "offline"},
                "runtime_json": {"baseline_created": False},
                "last_run_at": timestamp,
            }
        )
        return {
            "channel_id": channel_id,
            "algorithm_type": algorithm_type,
            "status": "offline",
            "ran": True,
            "state": state,
        }


__all__ = [
    "CAMERA_OCCLUSION",
    "CAMERA_VIEW_SHIFT",
    "ElectronicSealCameraHealthAdapter",
    "SUPPORTED_CAMERA_HEALTH_ALGORITHMS",
]
