from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import logging
import threading
from typing import Any, Callable

from src.db.database import SessionLocal
from src.db.models import ChannelMeta
from src.electronic_seal.detection import ElectronicSealDetectionRunner
from src.electronic_seal.defaults import configured_bindings, role_defaults, seed_defaults
from src.electronic_seal.evidence import EvidenceService
from src.electronic_seal.evidence_video import EvidenceVideoCoordinator
from src.electronic_seal.frame_broker import FrameBroker, FrameSubscription
from src.electronic_seal.inference import YoloInferenceAdapter
from src.electronic_seal.scheduler import ElectronicSealCameraHealthScheduler
from src.electronic_seal.settings import SUPPORTED_ALGORITHMS, ElectronicSealSettings
from src.electronic_seal.store import ElectronicSealStore


logger = logging.getLogger("uvicorn.error")

# When two logical channels point at the same physical RTSP source, let the
# dispatch-room worker establish the connection before the vehicle worker.
_WORKER_START_ROLE_PRIORITY = {
    "dispatch_room": 0,
    "vehicle_entry": 1,
}


def _load_source_url(channel_id: str) -> str:
    with SessionLocal() as session:
        row = session.query(ChannelMeta.source_url).filter(ChannelMeta.id == channel_id).one_or_none()
    return str(row[0] or "").strip() if row else ""


def _geometry_annotation(config: dict[str, Any], field: str, frame: Any) -> list[list[float]] | None:
    value = config.get(field) or {}
    if not isinstance(value, dict):
        return None
    raw_points = value.get("points") or value.get("polygon")
    if not isinstance(raw_points, (list, tuple)):
        return None
    try:
        height, width = frame.shape[:2]
        points = [[float(point[0]), float(point[1])] for point in raw_points]
    except (AttributeError, IndexError, TypeError, ValueError):
        return None
    coordinate_space = str(value.get("coordinate_space") or "normalized").lower()
    if coordinate_space in {"normalized", "relative", "ratio"}:
        points = [[x * width, y * height] for x, y in points]
    elif coordinate_space not in {"pixel", "pixels"}:
        return None
    return points


class ElectronicSealChannelWorker:
    """One isolated frame reader and algorithm runtime for one channel."""

    def __init__(
        self,
        channel_id: str,
        source_url: str,
        configs: list[dict[str, Any]],
        *,
        store: ElectronicSealStore,
        settings: ElectronicSealSettings,
        frame_broker: FrameBroker | None = None,
        inference: Any | None = None,
    ) -> None:
        self.channel_id = channel_id
        self.source_url = source_url
        self.configs = [dict(item) for item in configs]
        self.store = store
        self.settings = settings
        self.frame_broker = frame_broker or FrameBroker()
        self.evidence_service = EvidenceService(
            evidence_dir=settings.evidence_dir,
            mine_code=settings.mine_code,
            camera_code=settings.camera_code,
            analysis_type_code=settings.analysis_type_code,
        )
        self.evidence_video = EvidenceVideoCoordinator(
            channel_id=channel_id,
            store=store,
            evidence_service=self.evidence_service,
            before_seconds=settings.evidence_video_before_seconds,
            after_seconds=settings.evidence_video_after_seconds,
            fps=settings.evidence_video_fps,
            buffer_fps=settings.evidence_buffer_fps,
            buffer_max_bytes=settings.evidence_buffer_max_bytes,
            frame_quality=settings.evidence_buffer_jpeg_quality,
        )
        # Keep this attribute for callers that inspect the historical buffer.
        self.evidence_buffer = self.evidence_video.buffer
        self.inference = inference or YoloInferenceAdapter(
            registry_dir=settings.model_registry_dir,
            device=settings.device,
        )
        self.detection_runner = ElectronicSealDetectionRunner(
            channel_id=channel_id,
            configs=self.configs,
            inference=self.inference,
            store=store,
            evidence_service=self.evidence_service,
        )
        self.latest_frame = None
        self.health_scheduler = ElectronicSealCameraHealthScheduler(
            store=store,
            channel_id=channel_id,
            frame_provider=lambda _channel_id: self.latest_frame,
        )
        self.subscription: FrameSubscription | None = None
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.subscription = self.frame_broker.subscribe(self.channel_id, self.source_url)
        self.thread = threading.Thread(
            target=self.run,
            name=f"ElectronicSeal-{self.channel_id}",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.subscription is not None:
            self.subscription.unsubscribe()
            self.subscription = None
        if self.thread and self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=3.0)
        self.thread = None
        self.evidence_video.close()
        self.frame_broker.shutdown()

    def update_configs(self, configs: list[dict[str, Any]]) -> None:
        self.configs = [dict(item) for item in configs]
        self.detection_runner.update_configs(self.configs)

    def reset_baseline(self, algorithm_type: str) -> None:
        runner = getattr(self.health_scheduler, "runner", None)
        if runner is not None and hasattr(runner, "reset_baseline"):
            runner.reset_baseline(algorithm_type, self.channel_id)

    def run(self) -> None:
        try:
            while not self.stop_event.is_set() and self.subscription is not None:
                try:
                    envelope = self.subscription.get_latest(timeout=1.0)
                except (TimeoutError, RuntimeError):
                    continue
                timestamp = float(envelope.timestamp)
                self.latest_frame = envelope.image
                self.evidence_video.append_frame(envelope.image, timestamp)
                try:
                    result = self.detection_runner.process(envelope.image, timestamp)
                    self._schedule_detection_videos(result, envelope.image, timestamp)
                except Exception:
                    logger.exception("[ElectronicSeal] channel_id=%s detection_error", self.channel_id)
                try:
                    results = self.health_scheduler.tick()
                    self._schedule_health_videos(results, timestamp)
                except Exception:
                    logger.exception("[ElectronicSeal] channel_id=%s health_scheduler_error", self.channel_id)
        finally:
            self.evidence_video.close()

    def _config_for_algorithm(self, algorithm_type: str) -> dict[str, Any] | None:
        return next(
            (
                config
                for config in self.configs
                if str(config.get("algorithm_type") or "") == str(algorithm_type)
            ),
            None,
        )

    def _schedule_detection_videos(
        self,
        result: dict[str, Any],
        frame: Any,
        timestamp: float,
    ) -> None:
        for event in result.get("events") or []:
            algorithm_type = str(event.get("algorithm_type") or event.get("event_type") or "")
            config = self._config_for_algorithm(algorithm_type)
            if config is None:
                continue
            annotations: dict[str, Any] = {}
            if algorithm_type == "room_empty":
                region = _geometry_annotation(config, "region_config_json", frame)
                if region:
                    annotations["region"] = region
            self.evidence_video.schedule(event, config, timestamp, annotations=annotations)

    def _schedule_health_videos(self, results: list[Any], timestamp: float) -> None:
        for result in results or []:
            if not isinstance(result, dict) or not result.get("event_created"):
                continue
            event_id = result.get("event_id")
            algorithm_type = result.get("algorithm_type")
            config = self._config_for_algorithm(str(algorithm_type or ""))
            if not event_id or config is None:
                continue
            self.evidence_video.schedule(
                {"id": event_id, "algorithm_type": algorithm_type},
                config,
                timestamp,
            )

    def status(self) -> dict[str, Any]:
        broker_status = self.frame_broker.get_status(self.channel_id)
        configs = []
        state_loader = getattr(self.store, "get_state", None)
        for config in self.configs:
            item = dict(config)
            if callable(state_loader):
                item["state"] = state_loader(
                    self.channel_id,
                    str(config.get("algorithm_type") or ""),
                )
            configs.append(item)
        return {
            "channel_id": self.channel_id,
            "source_url": self.source_url,
            "running": bool(self.thread and self.thread.is_alive()),
            "worker_status": "running" if self.thread and self.thread.is_alive() else "stopped",
            "frame": broker_status,
            "algorithms": [item.get("algorithm_type") for item in self.configs],
            "configs": configs,
        }


class ElectronicSealManager:
    def __init__(
        self,
        *,
        store: Any | None = None,
        source_loader: Callable[[str], str] | None = None,
        worker_factory: Callable[[str, str, list[dict[str, Any]]], Any] | None = None,
        settings: ElectronicSealSettings | None = None,
    ) -> None:
        self.store = store or ElectronicSealStore()
        self.settings = settings or ElectronicSealSettings()
        self.source_loader = source_loader or _load_source_url
        self.worker_factory = worker_factory or (
            lambda channel_id, source_url, configs: ElectronicSealChannelWorker(
                channel_id,
                source_url,
                configs,
                store=self.store,
                settings=self.settings,
            )
        )
        self._workers: dict[str, Any] = {}
        self._pending_upload_jobs: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def reconcile(self) -> dict[str, int]:
        if not bool(self.settings.enabled):
            return self.stop_all()

        self._initialize_configured_profiles()
        profiles_loader = getattr(self.store, "list_bound_channel_profiles", None)
        desired: dict[str, list[dict[str, Any]]] = defaultdict(list)
        if callable(profiles_loader):
            profiles = sorted(
                profiles_loader(),
                key=lambda profile: (
                    _WORKER_START_ROLE_PRIORITY.get(str(profile.get("role") or ""), 99),
                    str(profile.get("channel_id") or ""),
                ),
            )
            for profile in profiles:
                channel_id = str(profile.get("channel_id") or "").strip()
                if not channel_id or not bool(profile.get("bound", True)):
                    continue
                self._seed_profile_defaults(channel_id, str(profile.get("role") or ""))
                for config in self._channel_configs(channel_id, str(profile.get("role") or "")):
                    desired[channel_id].append(dict(config))
                if not profile.get("initialized_at"):
                    profile_updater = getattr(self.store, "upsert_channel_profile", None)
                    if callable(profile_updater):
                        profile_updater(
                            channel_id,
                            {
                                "role": str(profile.get("role") or ""),
                                "initialized_at": datetime.now(),
                            },
                        )
        else:
            # Keep older isolated tests and pre-profile installations recoverable.
            for config in self.store.list_configs():
                if bool(config.get("enabled")) and config.get("algorithm_type") in SUPPORTED_ALGORITHMS:
                    desired[str(config["channel_id"])].append(dict(config))

        started = 0
        stopped = 0
        with self._lock:
            for channel_id in list(self._workers):
                source_url = self.source_loader(channel_id)
                if channel_id not in desired or not source_url:
                    self._workers.pop(channel_id).stop()
                    stopped += 1
                    continue
                worker = self._workers[channel_id]
                if worker.source_url != source_url:
                    self._workers.pop(channel_id).stop()
                    stopped += 1
                elif worker.configs != desired[channel_id]:
                    update_configs = getattr(worker, "update_configs", None)
                    if callable(update_configs):
                        update_configs(desired[channel_id])
                    else:
                        self._workers.pop(channel_id).stop()
                        stopped += 1

            for channel_id, channel_configs in desired.items():
                source_url = self.source_loader(channel_id)
                if not source_url or channel_id in self._workers:
                    continue
                worker = self.worker_factory(channel_id, source_url, channel_configs)
                worker.start()
                self._workers[channel_id] = worker
                started += 1
        return {"started": started, "stopped": stopped, "running": len(self._workers)}

    def _channel_configs(self, channel_id: str, role: str | None = None) -> list[dict[str, Any]]:
        configs = self.store.list_configs(channel_id)
        if not role:
            profile_loader = getattr(self.store, "get_channel_profile", None)
            profile = profile_loader(channel_id) if callable(profile_loader) else None
            role = profile.get("role") if profile else None
        if role not in {"vehicle_entry", "dispatch_room"}:
            return configs
        allowed_algorithms = {
            item["algorithm_type"]
            for item in role_defaults(role, self.settings.defaults_file)
        }
        return [item for item in configs if item.get("algorithm_type") in allowed_algorithms]

    def _initialize_configured_profiles(self) -> None:
        profile_loader = getattr(self.store, "list_channel_profiles", None)
        profile_updater = getattr(self.store, "upsert_channel_profile", None)
        if not callable(profile_loader) or not callable(profile_updater):
            return
        existing = {
            str(item.get("channel_id"))
            for item in profile_loader()
            if item.get("channel_id")
        }
        try:
            bindings = configured_bindings(self.settings.defaults_file)
        except (OSError, ValueError) as exc:
            logger.error("[ElectronicSeal] invalid channel defaults: %s", exc)
            return
        for binding in bindings:
            channel_id = str(binding["channel_id"])
            if channel_id in existing:
                continue
            try:
                profile_updater(channel_id, binding)
                logger.info(
                    "[ElectronicSeal] initialized channel profile channel_id=%s role=%s",
                    channel_id,
                    binding["role"],
                )
            except Exception as exc:
                # A stale binding must not prevent other channels from starting.
                logger.warning(
                    "[ElectronicSeal] skipped missing channel profile channel_id=%s error=%s",
                    channel_id,
                    exc,
                )

    def _seed_profile_defaults(self, channel_id: str, role: str) -> None:
        if role not in {"vehicle_entry", "dispatch_room"}:
            logger.error(
                "[ElectronicSeal] channel_id=%s has unsupported role=%s",
                channel_id,
                role,
            )
            return
        try:
            seed_defaults(
                self.store,
                channel_id,
                role,
                registry_dir=self.settings.model_registry_dir,
                defaults_path=self.settings.defaults_file,
            )
        except ValueError as exc:
            # Keep rows present so the API can expose waiting_config rather than
            # silently dropping a bound channel when the model is unavailable.
            ensure_defaults = getattr(self.store, "ensure_default_configs", None)
            if callable(ensure_defaults):
                try:
                    ensure_defaults(
                        channel_id,
                        role_defaults(role, self.settings.defaults_file),
                        model_id=None,
                        preserve_existing=True,
                    )
                except Exception:
                    logger.exception(
                        "[ElectronicSeal] failed to seed waiting-config rows channel_id=%s",
                        channel_id,
                    )
            logger.error(
                "[ElectronicSeal] channel_id=%s waiting for model/config: %s",
                channel_id,
                exc,
            )

    def restore_enabled_channels(self) -> dict[str, int]:
        result = self.reconcile()
        self.restore_pending_uploads()
        return result

    def restore_pending_uploads(self) -> list[dict[str, Any]]:
        loader = getattr(self.store, "list_upload_jobs", None)
        if not callable(loader):
            self._pending_upload_jobs = []
        else:
            self._pending_upload_jobs = loader({"pending", "retry", "uploading"})
        return list(self._pending_upload_jobs)

    def apply_config(self, channel_id: str, algorithm_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if algorithm_type not in SUPPORTED_ALGORITHMS:
            raise ValueError(f"unsupported electronic-seal algorithm: {algorithm_type}")
        result = self.store.upsert_config(channel_id, algorithm_type, payload)
        self.reconcile()
        return result

    def apply_profile(self, channel_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        updater = getattr(self.store, "upsert_channel_profile", None)
        if not callable(updater):
            raise RuntimeError("electronic-seal channel profiles are unavailable")
        result = updater(channel_id, payload)
        self.reconcile()
        return result

    def list_configs(self, channel_id: str | None = None) -> dict[str, Any]:
        return {
            "items": (
                self._channel_configs(channel_id)
                if channel_id
                else self.store.list_configs(channel_id)
            ),
            "profiles": self.store.list_channel_profiles(channel_id)
            if callable(getattr(self.store, "list_channel_profiles", None))
            else [],
        }

    def recalibrate(self, channel_id: str, algorithm_type: str) -> dict[str, Any]:
        if algorithm_type not in SUPPORTED_ALGORITHMS:
            raise ValueError(f"unsupported electronic-seal algorithm: {algorithm_type}")
        result = self.store.invalidate_baseline(channel_id, algorithm_type)
        worker = self.worker(channel_id)
        if worker is not None and hasattr(worker, "reset_baseline"):
            worker.reset_baseline(algorithm_type)
        return {**result, "status": "calibrating"}

    def get_status(self, channel_id: str) -> dict[str, Any]:
        worker = self.worker(channel_id)
        if worker is None:
            profile_loader = getattr(self.store, "get_channel_profile", None)
            profile = profile_loader(channel_id) if callable(profile_loader) else None
            return {
                "channel_id": channel_id,
                "running": False,
                "worker_status": "stopped",
                "source_type": "channels.source_url",
                "role": profile.get("role") if profile else None,
                "bound": bool(profile.get("bound")) if profile else False,
                "configs": self._channel_configs(channel_id),
            }
        result = worker.status()
        profile_loader = getattr(self.store, "get_channel_profile", None)
        profile = profile_loader(channel_id) if callable(profile_loader) else None
        if profile:
            result.update({"role": profile["role"], "bound": bool(profile["bound"])})
        return result

    def list_profiles(self, channel_id: str | None = None) -> dict[str, Any]:
        loader = getattr(self.store, "list_channel_profiles", None)
        return {"items": loader(channel_id) if callable(loader) else []}

    def overview(self) -> dict[str, Any]:
        profiles_loader = getattr(self.store, "list_bound_channel_profiles", None)
        profiles = profiles_loader() if callable(profiles_loader) else []
        items: list[dict[str, Any]] = []
        with SessionLocal() as session:
            for profile in profiles:
                channel_id = str(profile["channel_id"])
                row = session.query(ChannelMeta).filter(ChannelMeta.id == channel_id).one_or_none()
                worker_status = self.get_status(channel_id)
                configs = worker_status.get("configs") or self.store.list_configs(channel_id)
                algorithms = []
                for config in configs:
                    algorithm_type = str(config.get("algorithm_type") or "")
                    state = config.get("state")
                    if state is None:
                        state = self.store.get_state(channel_id, algorithm_type)
                    algorithms.append(
                        {
                            "algorithm_type": algorithm_type,
                            "category": "health" if algorithm_type.startswith("camera_") else "seal",
                            "status": (state or {}).get("status") or "starting",
                            "config": config,
                        }
                    )
                items.append(
                    {
                        "channel_id": channel_id,
                        "name": row.name if row else channel_id,
                        "role": profile.get("role"),
                        "owner": "electronic_seal",
                        "stream": worker_status,
                        "algorithms": algorithms,
                    }
                )
        return {"items": items}

    def list_events(
        self,
        channel_id,
        algorithm_type,
        page,
        page_size,
        *,
        start_time=None,
        end_time=None,
        cursor=None,
    ):
        return self.store.list_events(
            channel_id,
            algorithm_type,
            page,
            page_size,
            start_time=start_time,
            end_time=end_time,
            cursor=cursor,
        )

    def get_event(self, event_id):
        return self.store.get_event(event_id)

    def update_event(self, event_id, payload):
        return self.store.update_event(event_id, payload)

    def get_truck_count(self, channel_id):
        return self.store.get_truck_count(channel_id)

    def list_truck_departures(self, channel_id, **kwargs):
        return self.store.list_truck_departures(channel_id, **kwargs)

    def retry_upload(self, job_id):
        requeue = getattr(self.store, "requeue_upload_job", None)
        if callable(requeue):
            result = requeue(job_id)
            if result is None:
                raise ValueError(f"upload job not found: {job_id}")
            return result
        return {"job_id": job_id, "status": "queued"}

    def worker(self, channel_id: str):
        with self._lock:
            return self._workers.get(str(channel_id))

    def stop_all(self) -> dict[str, int]:
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:
            worker.stop()
        return {"stopped": len(workers), "running": 0}


electronic_seal_manager = ElectronicSealManager()


__all__ = [
    "ElectronicSealChannelWorker",
    "ElectronicSealManager",
    "electronic_seal_manager",
]
