from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
import re
from typing import Any

import cv2
import numpy as np

from src.electronic_seal.severity import event_severity
from src.services.camera_health_defaults import (
    CAMERA_OCCLUSION,
    CAMERA_VIEW_SHIFT,
    load_camera_health_thresholds,
)
from src.services.camera_health_state_machine import CameraHealthStateMachine
from src.services.camera_health_types import DetectorResult
from src.vision_core.camera_occlusion_detector import CameraOcclusionDetector
from src.vision_core.camera_view_shift_detector import CameraViewShiftDetector


SUPPORTED_CAMERA_HEALTH_ALGORITHMS = frozenset({CAMERA_OCCLUSION, CAMERA_VIEW_SHIFT})
_DEFAULT_ALARM_HOLD_SECONDS = 10.0
_DEFAULT_RECOVERY_HOLD_SECONDS = 15.0
_DEFAULT_BASELINE_WARMUP_SECONDS = 15.0
_DEFAULT_BASELINE_MIN_VALID_FRAMES = 10
_DEFAULT_BASELINE_MAX_CALIBRATION_SECONDS = 60.0
logger = logging.getLogger("uvicorn.error")


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
        evidence_service: Any | None = None,
        baseline_warmup_seconds: float = _DEFAULT_BASELINE_WARMUP_SECONDS,
        baseline_min_valid_frames: int = _DEFAULT_BASELINE_MIN_VALID_FRAMES,
        baseline_max_calibration_seconds: float = _DEFAULT_BASELINE_MAX_CALIBRATION_SECONDS,
        runtime_owner_id: str | None = None,
    ) -> None:
        if float(baseline_warmup_seconds) < 0:
            raise ValueError("baseline_warmup_seconds must be non-negative")
        if not isinstance(baseline_min_valid_frames, int) or baseline_min_valid_frames < 1:
            raise ValueError("baseline_min_valid_frames must be a positive integer")
        if float(baseline_max_calibration_seconds) <= 0:
            raise ValueError("baseline_max_calibration_seconds must be positive")
        self.store = store
        self.baseline_dir = Path(baseline_dir)
        self.thresholds = thresholds or load_camera_health_thresholds()
        self.evidence_service = evidence_service
        self.baseline_warmup_seconds = float(baseline_warmup_seconds)
        self.baseline_min_valid_frames = int(baseline_min_valid_frames)
        self.baseline_max_calibration_seconds = float(baseline_max_calibration_seconds)
        self.runtime_owner_id = runtime_owner_id
        self._baselines: dict[tuple[str, str], np.ndarray | None] = {}
        self._detectors: dict[tuple[str, str], Any] = {}
        self._calibration_detectors: dict[tuple[str, str], Any] = {}
        self._machines: dict[tuple[str, str], CameraHealthStateMachine] = {}
        self._active_event_ids: dict[tuple[str, str], str] = {}
        self._calibration_started_at: dict[tuple[str, str], datetime] = {}
        self._calibration_samples: dict[
            tuple[str, str], list[tuple[datetime, np.ndarray, float]]
        ] = {}

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

        baseline = self._load_baseline(channel_id, algorithm_type)
        if baseline is None:
            calibration = self._calibrate_baseline(config, frame, timestamp)
            if calibration["baseline"] is None:
                return self._persist_result(
                    config,
                    timestamp,
                    calibration["result"],
                    frame,
                    baseline_created=False,
                    baseline_source="calibrating",
                    calibration_runtime=calibration["runtime"],
                )
            baseline = calibration["baseline"]
            baseline_created = True
            baseline_source = "warmup_consensus"
            result = calibration["result"]
            if algorithm_type == CAMERA_VIEW_SHIFT:
                result = self._detector_for(config).analyze(frame, baseline)
        else:
            baseline_created = False
            baseline_source = "persisted"
            result = self._detector_for(config).analyze(frame, baseline)

        return self._persist_result(
            config,
            timestamp,
            result,
            frame,
            baseline_created=baseline_created,
            baseline_source=baseline_source,
        )

    def _persist_result(
        self,
        config: dict[str, Any],
        timestamp: datetime,
        result: DetectorResult,
        frame: np.ndarray,
        *,
        baseline_created: bool,
        baseline_source: str,
        calibration_runtime: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        channel_id = str(config["channel_id"])
        algorithm_type = str(config["algorithm_type"])
        timestamp_seconds = timestamp.timestamp()
        machine = self._machine_for(config)
        previous_bad_since = machine.bad_since
        transition = machine.update(is_bad=result.is_bad, now=timestamp_seconds)
        key = (channel_id, algorithm_type)
        event_id = self._active_event_ids.get(key)
        event_created = False
        if transition.create_event:
            event = self.store.create_event(
                {
                    "channel_id": channel_id,
                    "algorithm_type": algorithm_type,
                    "event_type": algorithm_type,
                    "severity": event_severity(algorithm_type),
                    "started_at": datetime.fromtimestamp(machine.bad_since or timestamp_seconds),
                    "score": result.score,
                    "metrics_json": {
                        "reason": result.reason,
                        "state_transition": transition.reason,
                        **result.metrics,
                    },
                    "runtime_owner_id": self.runtime_owner_id,
                }
            )
            event_id = event["id"]
            self._active_event_ids[key] = event_id
            event_created = True
            self._save_event_snapshot(config, event_id, frame)

        if transition.close_event:
            event_id = self._active_event_ids.pop(key, None)
            if event_id:
                close_args = (
                    event_id,
                    timestamp,
                    _seconds_between(
                        timestamp,
                        datetime.fromtimestamp(previous_bad_since)
                        if previous_bad_since is not None
                        else None,
                    ),
                )
                if self.runtime_owner_id is None:
                    self.store.close_event(*close_args)
                else:
                    self.store.close_event(
                        *close_args,
                        runtime_owner_id=self.runtime_owner_id,
                    )
            event_id = None

        status = transition.status
        if (
            calibration_runtime is not None
            and not result.is_bad
            and transition.status == "normal"
        ):
            status = "calibrating"
        last_good_at = timestamp if status == "normal" else None

        runtime_json = {
            "baseline_created": baseline_created,
            "baseline_source": baseline_source,
            "recovery_since": machine.recovery_since,
        }
        if calibration_runtime is not None:
            runtime_json["calibration"] = calibration_runtime

        state = self.store.save_state(
            {
                "channel_id": channel_id,
                "algorithm_type": algorithm_type,
                "status": status,
                "current_score": result.score,
                "last_metrics_json": {
                    "reason": result.reason,
                    "state_transition": transition.reason,
                    **result.metrics,
                },
                "runtime_json": runtime_json,
                "last_run_at": timestamp,
                "bad_since": (
                    datetime.fromtimestamp(machine.bad_since)
                    if machine.bad_since is not None
                    else None
                ),
                "last_good_at": last_good_at,
                "last_event_id": event_id,
                "runtime_owner_id": self.runtime_owner_id,
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

    def _calibrate_baseline(
        self,
        config: dict[str, Any],
        frame: np.ndarray,
        timestamp: datetime,
    ) -> dict[str, Any]:
        channel_id = str(config["channel_id"])
        algorithm_type = str(config["algorithm_type"])
        key = (channel_id, algorithm_type)
        started_at = self._calibration_started_at.setdefault(key, timestamp)
        quality_result = self._calibration_quality(config, frame)
        samples = self._calibration_samples.setdefault(key, [])
        frame_rejected = self._calibration_frame_rejected(algorithm_type, quality_result)

        if frame_rejected:
            samples.clear()
            self._calibration_started_at[key] = timestamp
            started_at = timestamp
        else:
            samples.append((timestamp, frame.copy(), float(quality_result.score)))

        elapsed_seconds = max((timestamp - started_at).total_seconds(), 0.0)
        if elapsed_seconds >= self.baseline_max_calibration_seconds:
            samples.clear()
            self._calibration_started_at[key] = timestamp
            started_at = timestamp
            elapsed_seconds = 0.0

        runtime = {
            "status": "calibrating",
            "started_at": started_at.isoformat(),
            "sample_count": len(samples),
            "required_samples": self.baseline_min_valid_frames,
            "elapsed_seconds": elapsed_seconds,
            "warmup_seconds": self.baseline_warmup_seconds,
            "max_calibration_seconds": self.baseline_max_calibration_seconds,
            "last_frame_accepted": not frame_rejected,
            "last_quality_reason": quality_result.reason,
        }
        if (
            frame_rejected
            or len(samples) < self.baseline_min_valid_frames
            or elapsed_seconds < self.baseline_warmup_seconds
        ):
            return {"baseline": None, "result": quality_result, "runtime": runtime}

        baseline, selected_quality = self._select_baseline(samples)
        self._save_baseline(
            channel_id,
            algorithm_type,
            baseline,
            timestamp,
            quality_score=selected_quality,
            calibration_params={
                "source": "warmup_consensus",
                "sample_count": len(samples),
                "warmup_seconds": elapsed_seconds,
                "selection": "representative_frame",
            },
        )
        self._baselines[key] = baseline
        self._calibration_started_at.pop(key, None)
        self._calibration_samples.pop(key, None)
        return {"baseline": baseline, "result": quality_result, "runtime": None}

    @staticmethod
    def _calibration_frame_rejected(
        algorithm_type: str,
        result: DetectorResult,
    ) -> bool:
        if algorithm_type == CAMERA_OCCLUSION:
            return result.is_bad
        return bool(result.metrics.get("calibration_quality_bad"))

    def _calibration_quality(self, config: dict[str, Any], frame: np.ndarray) -> DetectorResult:
        algorithm_type = str(config["algorithm_type"])
        if algorithm_type == CAMERA_OCCLUSION:
            return self._detector_for(config).analyze(frame, None)

        key = (str(config["channel_id"]), algorithm_type)
        detector = self._calibration_detectors.get(key)
        if detector is None:
            detector = CameraOcclusionDetector(self.thresholds.get(CAMERA_OCCLUSION, {}))
            self._calibration_detectors[key] = detector
        quality = detector.analyze(frame, None)
        return DetectorResult(
            is_bad=False,
            score=quality.score,
            reason=(
                "calibration_frame_rejected"
                if quality.is_bad
                else "calibrating_frame_quality"
            ),
            metrics={
                "calibration_quality_reason": quality.reason,
                "calibration_quality_bad": quality.is_bad,
                **quality.metrics,
            },
        )

    @staticmethod
    def _select_baseline(
        samples: list[tuple[datetime, np.ndarray, float]],
    ) -> tuple[np.ndarray, float]:
        if not samples:
            raise ValueError("baseline calibration requires at least one sample")
        if len(samples) == 1:
            return samples[0][1].copy(), samples[0][2]

        thumbnails = [
            cv2.resize(
                cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
                (64, 36),
                interpolation=cv2.INTER_AREA,
            ).astype(np.float32)
            for _, frame, _ in samples
        ]
        distances = []
        for index, thumbnail in enumerate(thumbnails):
            distance = sum(
                float(cv2.absdiff(thumbnail, other).mean())
                for other_index, other in enumerate(thumbnails)
                if other_index != index
            ) / max(len(thumbnails) - 1, 1)
            distances.append((distance, samples[index][2], index))
        _, quality_score, selected_index = min(distances)
        return samples[selected_index][1].copy(), quality_score

    def _machine_for(self, config: dict[str, Any]) -> CameraHealthStateMachine:
        key = (str(config["channel_id"]), str(config["algorithm_type"]))
        machine = self._machines.get(key)
        if machine is not None:
            return machine
        alarm_hold_seconds = config.get("alarm_hold_seconds")
        recovery_hold_seconds = config.get("recovery_hold_seconds")
        machine = CameraHealthStateMachine(
            alarm_hold_seconds=(
                _DEFAULT_ALARM_HOLD_SECONDS
                if alarm_hold_seconds is None
                else float(alarm_hold_seconds)
            ),
            recovery_hold_seconds=(
                _DEFAULT_RECOVERY_HOLD_SECONDS
                if recovery_hold_seconds is None
                else float(recovery_hold_seconds)
            ),
        )
        state = self.store.get_state(*key) or {}
        if state.get("status") in {
            "normal",
            "suspected",
            "alarming",
            "recovering",
            "offline",
            "degraded",
        }:
            machine.status = state["status"]
        bad_since = _parse_datetime(state.get("bad_since"))
        if bad_since is not None:
            machine.bad_since = bad_since.timestamp()
        runtime = state.get("runtime_json") or {}
        recovery_since = _parse_datetime(runtime.get("recovery_since"))
        if recovery_since is not None:
            machine.recovery_since = recovery_since.timestamp()
        active_event_id = state.get("last_event_id")
        if active_event_id:
            self._active_event_ids[key] = str(active_event_id)
            # An unavailable state can be persisted across a worker restart.
            # Restore the alarm marker so the next good frame enters recovery
            # hold instead of silently closing the event in memory only.
            if machine.status in {"alarming", "recovering", "offline", "degraded"}:
                machine._alarm_was_active = True
        self._machines[key] = machine
        return machine

    def _save_event_snapshot(self, config: dict[str, Any], event_id: str, frame: np.ndarray) -> None:
        if not bool(config.get("need_snapshot", True)) or self.evidence_service is None:
            return
        try:
            snapshot_path = self.evidence_service.create_snapshot(
                channel_id=str(config["channel_id"]),
                event_id=event_id,
                frame=frame,
            )
            self.store.update_event(
                event_id,
                {"evidence_snapshot_path": snapshot_path},
            )
        except Exception:
            logger.exception(
                "[ElectronicSeal] health snapshot failed channel_id=%s algorithm_type=%s event_id=%s",
                config.get("channel_id"),
                config.get("algorithm_type"),
                event_id,
            )

    def reset_baseline(self, algorithm_type: str, channel_id: str | None = None) -> None:
        keys = {
            key
            for key in (
                set(self._baselines)
                | set(self._detectors)
                | set(self._calibration_detectors)
                | set(self._calibration_started_at)
                | set(self._calibration_samples)
            )
            if key[1] == algorithm_type and (channel_id is None or key[0] == str(channel_id))
        }
        for key in keys:
            self._baselines.pop(key, None)
            self._detectors.pop(key, None)
            self._calibration_detectors.pop(key, None)
            self._calibration_started_at.pop(key, None)
            self._calibration_samples.pop(key, None)

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
        calibration_params = (record or {}).get("calibration_params_json") or {}
        if calibration_params.get("source") == "first_frame":
            self._baselines[key] = None
            return None
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
        *,
        quality_score: float = 1.0,
        calibration_params: dict[str, Any] | None = None,
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
                "quality_score": float(quality_score),
                "calibration_params_json": calibration_params or {"source": "manual"},
                "captured_at": captured_at,
            },
        )

    def _save_unavailable(self, config: dict[str, Any], timestamp: datetime) -> dict[str, Any]:
        channel_id = str(config["channel_id"])
        algorithm_type = str(config["algorithm_type"])
        machine = self._machine_for(config)
        transition = machine.update(quality_status="offline", now=timestamp.timestamp())
        event_id = self._active_event_ids.get((channel_id, algorithm_type))
        state = self.store.save_state(
            {
                "channel_id": channel_id,
                "algorithm_type": algorithm_type,
                "status": transition.status,
                "current_score": 0.0,
                "last_metrics_json": {"reason": "offline"},
                "runtime_json": {
                    "baseline_created": False,
                    "recovery_since": machine.recovery_since,
                },
                "last_run_at": timestamp,
                "bad_since": (
                    datetime.fromtimestamp(machine.bad_since)
                    if machine.bad_since is not None
                    else None
                ),
                "last_event_id": event_id,
                "runtime_owner_id": self.runtime_owner_id,
            }
        )
        return {
            "channel_id": channel_id,
            "algorithm_type": algorithm_type,
            "status": transition.status,
            "ran": True,
            "event_id": event_id,
            "state": state,
        }


__all__ = [
    "CAMERA_OCCLUSION",
    "CAMERA_VIEW_SHIFT",
    "ElectronicSealCameraHealthAdapter",
    "SUPPORTED_CAMERA_HEALTH_ALGORITHMS",
]
