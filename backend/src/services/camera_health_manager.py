from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from queue import Empty
from typing import Callable

import cv2
import numpy as np

from src.core.settings import settings
from src.db.database import SessionLocal
from src.db.models import ChannelMeta
from src.services.camera_health_defaults import (
    CAMERA_OCCLUSION,
    CAMERA_VIEW_SHIFT,
    load_camera_health_thresholds,
)
from src.services.camera_health_state_machine import CameraHealthStateMachine
from src.services.camera_health_store import CameraHealthStore
from src.services.camera_occlusion_detector import CameraOcclusionDetector
from src.services.camera_view_shift_detector import CameraViewShiftDetector
from src.services.channel_frame_hub import ChannelFrameHub, ChannelFrameSubscription, channel_frame_hub


logger = logging.getLogger("uvicorn.error")
SUPPORTED_ALGORITHMS = {CAMERA_OCCLUSION, CAMERA_VIEW_SHIFT}


def _load_channel_source(channel_id: str) -> str:
    with SessionLocal() as session:
        row = session.query(ChannelMeta.source_url).filter(ChannelMeta.id == channel_id).one_or_none()
    return str(row[0] or "").strip() if row else ""


class CameraHealthWorker:
    def __init__(
        self,
        channel_id: str,
        source_url: str,
        configs: list[dict],
        *,
        store: CameraHealthStore,
        frame_hub: ChannelFrameHub,
        thresholds: dict[str, dict],
    ):
        self.channel_id = channel_id
        self.source_url = source_url
        self.configs = {item["algorithm_type"]: dict(item) for item in configs}
        self.store = store
        self.frame_hub = frame_hub
        self.thresholds = thresholds
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.subscription: ChannelFrameSubscription | None = None
        self._machines: dict[str, CameraHealthStateMachine] = {}
        self._active_event_ids: dict[str, str] = {}
        self._baselines: dict[str, np.ndarray | None] = {}
        self._started_at = 0.0

    @property
    def algorithm_types(self) -> set[str]:
        return set(self.configs)

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self.run,
            daemon=True,
            name=f"CameraHealth-{self.channel_id}",
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.subscription is not None:
            self.subscription.close()
            self.subscription = None
        if self.thread and self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=3.0)
        self.thread = None

    def update_configs(self, configs: list[dict]) -> None:
        self.configs = {item["algorithm_type"]: dict(item) for item in configs}

    def reset_baseline(self, algorithm_type: str) -> None:
        self._baselines.pop(algorithm_type, None)
        self._machines.pop(algorithm_type, None)

    def _thresholds_for(self, algorithm_type: str, config: dict) -> dict:
        values = dict(self.thresholds[algorithm_type])
        values.update(config.get("threshold_config_json") or {})
        return values

    def _machine_for(self, algorithm_type: str, config: dict) -> CameraHealthStateMachine:
        machine = self._machines.get(algorithm_type)
        if machine is not None:
            return machine
        machine = CameraHealthStateMachine(
            alarm_hold_seconds=int(
                config["alarm_hold_seconds"]
                if config.get("alarm_hold_seconds") is not None
                else settings.CAMERA_HEALTH_ALARM_HOLD_SECONDS
            ),
            recovery_hold_seconds=int(
                config["recovery_hold_seconds"]
                if config.get("recovery_hold_seconds") is not None
                else settings.CAMERA_HEALTH_RECOVERY_HOLD_SECONDS
            ),
        )
        state = self.store.get_state(self.channel_id, algorithm_type)
        if state and state.get("status") in {"normal", "suspected", "alarming", "recovering", "offline", "degraded"}:
            machine.status = state["status"]
        self._machines[algorithm_type] = machine
        if state and state.get("last_event_id"):
            self._active_event_ids[algorithm_type] = state["last_event_id"]
        return machine

    @staticmethod
    def _image_from_path(path_value: str | None) -> np.ndarray | None:
        if not path_value:
            return None
        try:
            image = cv2.imread(str(path_value), cv2.IMREAD_COLOR)
            return image if image is not None and image.size else None
        except Exception:
            return None

    def _load_baseline(self, algorithm_type: str) -> np.ndarray | None:
        if algorithm_type in self._baselines:
            return self._baselines[algorithm_type]
        baseline = self.store.get_active_baseline(self.channel_id, algorithm_type)
        image = self._image_from_path(baseline.get("image_path") if baseline else None)
        self._baselines[algorithm_type] = image
        return image

    def _save_baseline(self, algorithm_type: str, frame: np.ndarray) -> None:
        settings.CAMERA_HEALTH_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time() * 1000)
        path = settings.CAMERA_HEALTH_SNAPSHOT_DIR / f"baseline_{self.channel_id}_{algorithm_type}_{timestamp}.jpg"
        if not cv2.imwrite(str(path), frame):
            raise RuntimeError(f"failed to save camera health baseline: {path}")
        height, width = frame.shape[:2]
        self.store.replace_baseline(
            self.channel_id,
            algorithm_type,
            {
                "image_path": str(path),
                "width": width,
                "height": height,
                "quality_score": 1.0,
                "calibration_params_json": {"source": "first_stable_frame"},
            },
        )
        self._baselines[algorithm_type] = frame.copy()

    def _save_snapshot(self, algorithm_type: str, frame: np.ndarray) -> str:
        settings.CAMERA_HEALTH_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = settings.CAMERA_HEALTH_SNAPSHOT_DIR / f"event_{self.channel_id}_{algorithm_type}_{int(time.time() * 1000)}.jpg"
        if not cv2.imwrite(str(path), frame):
            raise RuntimeError(f"failed to save camera health snapshot: {path}")
        return str(path)

    def _persist_state(
        self,
        algorithm_type: str,
        result,
        transition,
        now: float,
    ) -> None:
        machine = self._machines[algorithm_type]
        payload = {
            "channel_id": self.channel_id,
            "algorithm_type": algorithm_type,
            "status": transition.status,
            "current_score": result.score,
            "last_metrics_json": {
                **result.metrics,
                "reason": result.reason,
            },
            "last_good_at": datetime.fromtimestamp(now) if not result.is_bad else None,
            "last_event_id": self._active_event_ids.get(algorithm_type),
        }
        if machine.bad_since is not None:
            payload["bad_since"] = datetime.fromtimestamp(machine.bad_since)
        try:
            self.store.save_state(payload)
        except Exception as exc:
            logger.exception(
                "[CameraHealth] channel_id=%s algorithm_type=%s persist_state_error=%s",
                self.channel_id,
                algorithm_type,
                exc,
            )

    def _apply_result(self, algorithm_type: str, result, frame: np.ndarray, now: float) -> None:
        config = self.configs[algorithm_type]
        machine = self._machine_for(algorithm_type, config)
        transition = machine.update(is_bad=result.is_bad, now=now)
        if transition.create_event:
            snapshot_path = self._save_snapshot(algorithm_type, frame) if config.get("need_snapshot", True) else None
            event = self.store.create_event(
                {
                    "channel_id": self.channel_id,
                    "algorithm_type": algorithm_type,
                    "event_type": algorithm_type,
                    "started_at": datetime.fromtimestamp(now),
                    "score": result.score,
                    "metrics_json": result.metrics,
                    "snapshot_path": snapshot_path,
                }
            )
            self._active_event_ids[algorithm_type] = event["id"]
            logger.warning(
                "[CameraHealth] channel_id=%s algorithm_type=%s status=alarming score=%.3f event_id=%s",
                self.channel_id,
                algorithm_type,
                result.score,
                event["id"],
            )
        if transition.close_event:
            event_id = self._active_event_ids.pop(algorithm_type, None)
            if event_id:
                self.store.close_event(
                    event_id,
                    datetime.fromtimestamp(now),
                    max(now - (machine.bad_since or now), 0.0),
                )
        self._persist_state(algorithm_type, result, transition, now)

    def _persist_offline(self) -> None:
        for algorithm_type, config in self.configs.items():
            machine = self._machine_for(algorithm_type, config)
            transition = machine.update(quality_status="offline", now=time.time())
            result = type("OfflineResult", (), {"score": 0.0, "is_bad": False, "reason": "offline", "metrics": {}})()
            self._persist_state(algorithm_type, result, transition, time.time())

    def _process_frame(self, frame: np.ndarray, now: float) -> None:
        warmup_seconds = max(
            [
                int(
                    item["warmup_seconds"]
                    if item.get("warmup_seconds") is not None
                    else settings.CAMERA_HEALTH_WARMUP_SECONDS
                )
                for item in self.configs.values()
            ]
            or [settings.CAMERA_HEALTH_WARMUP_SECONDS]
        )
        if now - self._started_at < warmup_seconds:
            for algorithm_type, config in self.configs.items():
                machine = self._machine_for(algorithm_type, config)
                result = type("CalibrationResult", (), {"score": 0.0, "is_bad": False, "reason": "calibrating", "metrics": {}})()
                transition = type("CalibrationTransition", (), {"status": "calibrating"})()
                self.store.save_state(
                    {
                        "channel_id": self.channel_id,
                        "algorithm_type": algorithm_type,
                        "status": transition.status,
                        "current_score": result.score,
                        "last_metrics_json": result.metrics,
                    }
                )
            return

        for algorithm_type in self.configs:
            if self._load_baseline(algorithm_type) is None:
                self._save_baseline(algorithm_type, frame)

        occlusion_result = None
        if CAMERA_OCCLUSION in self.configs:
            detector = CameraOcclusionDetector(self._thresholds_for(CAMERA_OCCLUSION, self.configs[CAMERA_OCCLUSION]))
            occlusion_result = detector.analyze(frame, self._load_baseline(CAMERA_OCCLUSION))
            self._apply_result(CAMERA_OCCLUSION, occlusion_result, frame, now)

        if CAMERA_VIEW_SHIFT in self.configs:
            if occlusion_result is not None and occlusion_result.is_bad:
                machine = self._machine_for(CAMERA_VIEW_SHIFT, self.configs[CAMERA_VIEW_SHIFT])
                transition = machine.update(quality_status="degraded", now=now)
                suppressed = type(
                    "SuppressedResult",
                    (),
                    {
                        "score": 0.0,
                        "is_bad": False,
                        "reason": "suppressed_by_occlusion",
                        "metrics": {"suppressed_by": CAMERA_OCCLUSION},
                    },
                )()
                self._persist_state(CAMERA_VIEW_SHIFT, suppressed, transition, now)
            else:
                detector = CameraViewShiftDetector(
                    self._thresholds_for(CAMERA_VIEW_SHIFT, self.configs[CAMERA_VIEW_SHIFT])
                )
                result = detector.analyze(frame, self._load_baseline(CAMERA_VIEW_SHIFT))
                self._apply_result(CAMERA_VIEW_SHIFT, result, frame, now)

    def run(self) -> None:
        self._started_at = time.time()
        if not self.source_url:
            self._persist_offline()
            return
        try:
            self.subscription = self.frame_hub.subscribe(self.channel_id, self.source_url)
            while not self.stop_event.is_set():
                try:
                    envelope = self.subscription.get_latest(timeout=1.0)
                except Empty:
                    status = self.frame_hub.get_status(self.channel_id)
                    if status.get("status") == "offline":
                        self._persist_offline()
                    continue
                now = time.time()
                self._process_frame(envelope.image, now)
        except Exception as exc:
            logger.exception("[CameraHealth] channel_id=%s worker_error=%s", self.channel_id, exc)
            self._persist_offline()
        finally:
            if self.subscription is not None:
                self.subscription.close()
                self.subscription = None


class CameraHealthManager:
    def __init__(
        self,
        *,
        store: CameraHealthStore | None = None,
        frame_hub: ChannelFrameHub | None = None,
        channel_source_loader: Callable[[str], str] | None = None,
        thresholds: dict[str, dict] | None = None,
        worker_factory: Callable[..., CameraHealthWorker] | None = None,
    ):
        self.store = store or CameraHealthStore()
        self.frame_hub = frame_hub or channel_frame_hub
        self.channel_source_loader = channel_source_loader or _load_channel_source
        self.thresholds = thresholds or load_camera_health_thresholds()
        self.worker_factory = worker_factory or CameraHealthWorker
        self._lock = threading.RLock()
        self._workers: dict[str, CameraHealthWorker] = {}

    def restore_enabled_channels(self) -> None:
        if not settings.CAMERA_HEALTH_ENABLED:
            return
        channel_ids = {
            item["channel_id"]
            for item in self.store.list_configs()
            if item.get("enabled") and item.get("algorithm_type") in SUPPORTED_ALGORITHMS
        }
        for channel_id in sorted(channel_ids):
            self.reconcile_channel(channel_id)

    def reconcile_channel(self, channel_id: str) -> None:
        configs = [
            item
            for item in self.store.list_configs(channel_id)
            if item.get("enabled") and item.get("algorithm_type") in SUPPORTED_ALGORITHMS
        ]
        with self._lock:
            worker = self._workers.get(channel_id)
            if not configs:
                if worker:
                    worker.stop()
                    self._workers.pop(channel_id, None)
                return
            source_url = self.channel_source_loader(channel_id)
            if not source_url:
                if worker:
                    worker.stop()
                    self._workers.pop(channel_id, None)
                for config in configs:
                    try:
                        self.store.save_state(
                            {
                                "channel_id": channel_id,
                                "algorithm_type": config["algorithm_type"],
                                "status": "offline",
                                "current_score": 0.0,
                                "last_metrics_json": {"reason": "missing_source_url"},
                            }
                        )
                    except Exception:
                        logger.exception("[CameraHealth] channel_id=%s offline_state_error", channel_id)
                return
            if worker and worker.source_url == source_url:
                worker.update_configs(configs)
                return
            if worker:
                worker.stop()
            worker = self.worker_factory(
                channel_id,
                source_url,
                configs,
                store=self.store,
                frame_hub=self.frame_hub,
                thresholds=self.thresholds,
            )
            self._workers[channel_id] = worker
            worker.start()
            logger.info(
                "[CameraHealth] channel_id=%s worker_started algorithms=%s source_type=channel.source_url",
                channel_id,
                sorted(item["algorithm_type"] for item in configs),
            )

    def apply_config(self, channel_id: str, algorithm_type: str, payload: dict) -> dict:
        result = self.store.upsert_config(channel_id, algorithm_type, payload)
        self.reconcile_channel(channel_id)
        return result

    def recalibrate(self, channel_id: str, algorithm_type: str) -> dict:
        if algorithm_type not in SUPPORTED_ALGORITHMS:
            raise ValueError(f"Unsupported camera health algorithm: {algorithm_type}")
        result = self.store.invalidate_baseline(channel_id, algorithm_type)
        self.store.save_state(
            {
                "channel_id": channel_id,
                "algorithm_type": algorithm_type,
                "status": "calibrating",
                "current_score": 0.0,
                "last_metrics_json": {"reason": "manual_recalibration"},
                "last_event_id": None,
            }
        )
        with self._lock:
            worker = self._workers.get(channel_id)
            if worker:
                worker.reset_baseline(algorithm_type)
        return {
            **result,
            "status": "calibrating",
        }

    def worker_count(self) -> int:
        with self._lock:
            return len(self._workers)

    def worker(self, channel_id: str):
        with self._lock:
            return self._workers.get(channel_id)

    def get_status(self, channel_id: str) -> dict:
        with self._lock:
            worker = self._workers.get(channel_id)
        configs = self.store.list_configs(channel_id)
        states = []
        for config in configs:
            state = self.store.get_state(channel_id, config["algorithm_type"])
            states.append(state or {
                "channel_id": channel_id,
                "algorithm_type": config["algorithm_type"],
                "status": "disabled" if not config.get("enabled") else "offline",
            })
        return {
            "channel_id": channel_id,
            "running": bool(worker and worker.thread and worker.thread.is_alive()),
            "source_type": "channel.source_url",
            "worker_status": "running" if worker else "stopped",
            "algorithms": states,
            "frame": self.frame_hub.get_status(channel_id),
        }

    def stop_all(self) -> dict:
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:
            worker.stop()
        return {"stopped": len(workers), "remaining": 0}


camera_health_manager = CameraHealthManager()
