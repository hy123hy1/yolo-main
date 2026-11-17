from __future__ import annotations

from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta
import logging
import queue
from secrets import token_urlsafe
import threading
from typing import Any, Callable

from src.db.database import SessionLocal
from src.db.models import ElectronicSealChannelMeta
from src.electronic_seal.detection import ElectronicSealDetectionRunner
from src.electronic_seal.defaults import configured_bindings, role_defaults, seed_defaults
from src.electronic_seal.contracts import (
    ElectronicSealStoreProtocol,
    ElectronicSealWorkerFactory,
    ElectronicSealWorkerProtocol,
)
from src.electronic_seal.evidence import EvidenceService
from src.electronic_seal.evidence_video import EvidenceVideoCoordinator
from src.electronic_seal.frame_broker import FrameBroker, FrameEnvelope, FrameSubscription
from src.electronic_seal.inference import YoloInferenceAdapter
from src.electronic_seal.scheduler import (
    ElectronicSealCameraHealthScheduler,
    ElectronicSealOnlineStatusScheduler,
)
from src.electronic_seal.settings import SUPPORTED_ALGORITHMS, ElectronicSealSettings
from src.electronic_seal.snapshot import ElectronicSealSnapshotService
from src.electronic_seal.store import ElectronicSealStore
from src.services.channel_service import ChannelService
from src.services.wvp_service import WVPService


logger = logging.getLogger("uvicorn.error")

# When two logical channels point at the same physical RTSP source, let the
# dispatch-room worker establish the connection before the vehicle worker.
_WORKER_START_ROLE_PRIORITY = {
    "dispatch_room": 0,
    "vehicle_entry": 1,
}

_ROLE_SPECIFIC_ALGORITHMS = {
    "dispatch_room": "room_empty",
    "vehicle_entry": "truck_departure",
}

_RUNTIME_CONFIG_IGNORED_FIELDS = frozenset({"created_at", "updated_at"})


def _runtime_configs(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [
            {
                key: value
                for key, value in config.items()
                if key not in _RUNTIME_CONFIG_IGNORED_FIELDS
            }
            for config in configs
        ],
        key=lambda config: str(config.get("algorithm_type") or ""),
    )


def _load_source_url(channel_id: str) -> str:
    with SessionLocal() as session:
        row = session.query(
            ElectronicSealChannelMeta.flv,
            ElectronicSealChannelMeta.source_url,
        ).filter(
            ElectronicSealChannelMeta.id == channel_id
        ).one_or_none()
    if not row:
        return ""
    return str(row[1] or row[0] or "").strip()


def _load_fallback_source_url(channel_id: str) -> str:
    with SessionLocal() as session:
        row = session.query(
            ElectronicSealChannelMeta.source_url,
            ElectronicSealChannelMeta.flv,
        ).filter(
            ElectronicSealChannelMeta.id == channel_id
        ).one_or_none()
    if not row:
        return ""
    primary = str(row[0] or "").strip()
    fallback = str(row[1] or "").strip()
    if not primary.lower().startswith("rtsp://") or fallback == primary:
        return ""
    return fallback


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
        fallback_source_url: str = "",
    ) -> None:
        self.channel_id = channel_id
        self.source_url = source_url
        self.fallback_source_url = str(fallback_source_url or "").strip()
        self.configs = [dict(item) for item in configs]
        self.store = store
        self.settings = settings
        self.runtime_owner_id = token_urlsafe(32)
        self.frame_broker = frame_broker or FrameBroker(
            first_frame_grace_seconds=settings.frame_first_frame_grace_seconds,
            fallback_grace_seconds=5.0,
        )
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
            runtime_owner_id=self.runtime_owner_id,
        )
        # Keep this attribute for callers that inspect the historical buffer.
        self.evidence_buffer = self.evidence_video.buffer
        if inference is None:
            self.inference = YoloInferenceAdapter(
                registry_dir=settings.model_registry_dir,
                device=settings.device,
                metadata_loader=self.store.get_model,
            )
        else:
            self.inference = inference
        self.detection_runner = ElectronicSealDetectionRunner(
            channel_id=channel_id,
            configs=self.configs,
            inference=self.inference,
            store=store,
            evidence_service=self.evidence_service,
            runtime_owner_id=self.runtime_owner_id,
        )
        self.latest_frame = None
        self._latest_frame_lock = threading.Lock()
        self.health_scheduler = ElectronicSealCameraHealthScheduler(
            store=store,
            channel_id=channel_id,
            frame_provider=self._latest_frame_for_health,
            evidence_service=self.evidence_service,
            result_callback=self._handle_health_results,
            runtime_owner_id=self.runtime_owner_id,
        )
        self.subscription: FrameSubscription | None = None
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._detection_thread: threading.Thread | None = None
        self._detection_queue: queue.Queue[FrameEnvelope] = queue.Queue(maxsize=1)

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.store.claim_runtime(self.channel_id, self.runtime_owner_id)
        self._detection_queue = queue.Queue(maxsize=1)
        if self.fallback_source_url:
            self.subscription = self.frame_broker.subscribe(
                self.channel_id,
                self.source_url,
                fallback_source_url=self.fallback_source_url,
            )
        else:
            self.subscription = self.frame_broker.subscribe(
                self.channel_id,
                self.source_url,
            )
        self._detection_thread = threading.Thread(
            target=self._run_detection_loop,
            name=f"ElectronicSealDetection-{self.channel_id}",
            daemon=True,
        )
        self._detection_thread.start()
        self.health_scheduler.start()
        self.thread = threading.Thread(
            target=self.run,
            name=f"ElectronicSeal-{self.channel_id}",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        self.request_stop()
        self.wait_and_close()

    def request_stop(self) -> None:
        self.stop_event.set()
        if self.subscription is not None:
            self.subscription.unsubscribe()
            self.subscription = None
        self.health_scheduler.request_stop()

    def wait_and_close(self) -> None:
        self.health_scheduler.stop()
        if (
            self._detection_thread
            and self._detection_thread.is_alive()
            and self._detection_thread is not threading.current_thread()
        ):
            self._detection_thread.join(timeout=3.0)
        if self.thread and self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=3.0)
        self._detection_thread = None
        self.thread = None
        self.evidence_video.close()
        self.frame_broker.shutdown()

    def update_configs(self, configs: list[dict[str, Any]]) -> None:
        self.configs = [dict(item) for item in configs]
        self.detection_runner.update_configs(self.configs)

    def reset_baseline(self, algorithm_type: str) -> None:
        self.health_scheduler.runner.reset_baseline(algorithm_type, self.channel_id)

    def run(self) -> None:
        try:
            while not self.stop_event.is_set() and self.subscription is not None:
                try:
                    envelope = self.subscription.get_latest(timeout=1.0)
                except (TimeoutError, RuntimeError):
                    continue
                timestamp = float(envelope.timestamp)
                try:
                    with self._latest_frame_lock:
                        self.latest_frame = envelope.image
                except Exception:
                    logger.exception("[ElectronicSeal] channel_id=%s frame_state_error", self.channel_id)
                try:
                    self.evidence_video.append_frame(envelope.image, timestamp)
                except Exception:
                    logger.exception("[ElectronicSeal] channel_id=%s evidence_buffer_error", self.channel_id)
                self._publish_detection_frame(envelope)
        finally:
            self.stop_event.set()
            self.evidence_video.close()

    def _run_detection_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                envelope = self._detection_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if self.stop_event.is_set():
                break
            try:
                result = self.detection_runner.process(envelope.image, envelope.timestamp)
                self._schedule_detection_videos(result, envelope.image, envelope.timestamp)
            except Exception:
                logger.exception("[ElectronicSeal] channel_id=%s detection_error", self.channel_id)

    def _publish_detection_frame(self, envelope: FrameEnvelope) -> None:
        try:
            self._detection_queue.put_nowait(envelope)
            return
        except queue.Full:
            pass
        try:
            self._detection_queue.get_nowait()
        except queue.Empty:
            return
        try:
            self._detection_queue.put_nowait(envelope)
        except queue.Full:
            pass

    def _latest_frame_for_health(self, _channel_id: str) -> Any | None:
        with self._latest_frame_lock:
            frame = self.latest_frame
            copier = getattr(frame, "copy", None)
            return copier() if callable(copier) else frame

    def snapshot_frame(self) -> Any | None:
        return self._latest_frame_for_health(self.channel_id)

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

    def _handle_health_results(self, results: list[Any], timestamp: datetime) -> None:
        self._schedule_health_videos(results, timestamp.timestamp())

    def status(self) -> dict[str, Any]:
        broker_status = self.frame_broker.get_status(self.channel_id)
        configs = []
        for config in self.configs:
            item = dict(config)
            item["state"] = self.store.get_state(
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
        store: ElectronicSealStoreProtocol | None = None,
        source_loader: Callable[[str], str] | None = None,
        fallback_source_loader: Callable[[str], str] | None = None,
        worker_factory: ElectronicSealWorkerFactory | None = None,
        channel_player: Any | None = None,
        settings: ElectronicSealSettings | None = None,
    ) -> None:
        self.store = store or ElectronicSealStore()
        self.settings = settings or ElectronicSealSettings()
        self.channel_player = channel_player or ChannelService()
        self.source_loader = source_loader or _load_source_url
        if fallback_source_loader is not None:
            self.fallback_source_loader = fallback_source_loader
        elif source_loader is None:
            self.fallback_source_loader = _load_fallback_source_url
        else:
            self.fallback_source_loader = lambda _channel_id: ""
        self.worker_factory = worker_factory or (
            lambda channel_id, source_url, configs: ElectronicSealChannelWorker(
                channel_id,
                source_url,
                configs,
                store=self.store,
                settings=self.settings,
                fallback_source_url=self.fallback_source_loader(channel_id),
            )
        )
        self._workers: dict[str, ElectronicSealWorkerProtocol] = {}
        self._cleanup_executor = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="ElectronicSealCleanup",
        )
        self._pending_upload_jobs: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self.snapshot_service = ElectronicSealSnapshotService(
            store=self.store,
            snapshot_dir=self.settings.snapshot_dir,
            worker_provider=self.worker,
        )
        self.online_status_scheduler = ElectronicSealOnlineStatusScheduler(
            store=self.store,
            wvp_service=WVPService(),
            interval_seconds=self.settings.online_status_interval_seconds,
        )

    def reconcile(self) -> dict[str, int]:
        if not bool(self.settings.enabled):
            return self.stop_all()

        self._initialize_configured_profiles()
        desired: dict[str, list[dict[str, Any]]] = defaultdict(list)
        profiles = sorted(
            self.store.list_bound_channel_profiles(),
            key=lambda profile: (
                _WORKER_START_ROLE_PRIORITY.get(str(profile.get("role") or ""), 99),
                str(profile.get("channel_id") or ""),
            ),
        )
        for profile in profiles:
            channel_id = str(profile.get("channel_id") or "").strip()
            if not channel_id or not bool(profile.get("bound", True)):
                continue
            role = str(profile.get("role") or "")
            self._seed_profile_defaults(channel_id, role)
            self._prune_profile_configs(channel_id, role)
            for config in self._channel_configs(channel_id, role):
                desired[channel_id].append(dict(config))
            if not profile.get("initialized_at"):
                self.store.upsert_channel_profile(
                    channel_id,
                    {
                        "role": str(profile.get("role") or ""),
                        "initialized_at": datetime.now(),
                    },
                )

        started = 0
        stopped = 0
        with self._lock:
            for channel_id in list(self._workers):
                source_url = self.source_loader(channel_id)
                fallback_source_url = self.fallback_source_loader(channel_id)
                if channel_id not in desired or not source_url:
                    self._workers.pop(channel_id).stop()
                    stopped += 1
                    continue
                worker = self._workers[channel_id]
                if (
                    worker.source_url != source_url
                    or worker.fallback_source_url != fallback_source_url
                ):
                    self._workers.pop(channel_id).stop()
                    stopped += 1
                elif _runtime_configs(worker.configs) != _runtime_configs(desired[channel_id]):
                    worker.update_configs(desired[channel_id])

            for channel_id, channel_configs in desired.items():
                source_url = self.source_loader(channel_id)
                if not source_url or channel_id in self._workers:
                    continue
                worker = self.worker_factory(channel_id, source_url, channel_configs)
                worker.start()
                self._workers[channel_id] = worker
                started += 1
        return {"started": started, "stopped": stopped, "running": len(self._workers)}

    def reconcile_channel(self, channel_id: str) -> dict[str, int]:
        normalized_id = str(channel_id or "").strip()
        if not normalized_id:
            raise ValueError("channel_id is required")

        profile = self.store.get_channel_profile(normalized_id)

        desired_configs: list[dict[str, Any]] = []
        if bool(self.settings.enabled) and profile and bool(profile.get("bound", True)):
            role = str(profile.get("role") or "")
            self._seed_profile_defaults(normalized_id, role)
            self._prune_profile_configs(normalized_id, role)
            desired_configs = [dict(item) for item in self._channel_configs(normalized_id, role)]
            if not profile.get("initialized_at"):
                self.store.upsert_channel_profile(
                    normalized_id,
                    {"role": role, "initialized_at": datetime.now()},
                )

        source_url = self.source_loader(normalized_id) if desired_configs else ""
        fallback_source_url = (
            self.fallback_source_loader(normalized_id) if desired_configs else ""
        )
        started = 0
        stopped = 0
        with self._lock:
            worker = self._workers.get(normalized_id)
            replace_worker = bool(
                worker is not None
                and (
                    not source_url
                    or worker.source_url != source_url
                    or worker.fallback_source_url != fallback_source_url
                )
            )
            if replace_worker:
                self._workers.pop(normalized_id, None)
                worker.request_stop()
                self._schedule_release_cleanup(normalized_id, worker)
                worker = None
                stopped = 1
            elif worker is not None and _runtime_configs(worker.configs) != _runtime_configs(desired_configs):
                worker.update_configs(desired_configs)

            if source_url and worker is None:
                worker = self.worker_factory(normalized_id, source_url, desired_configs)
                worker.start()
                self._workers[normalized_id] = worker
                started = 1

        return {"started": started, "stopped": stopped, "running": len(self._workers)}

    def _channel_configs(self, channel_id: str, role: str | None = None) -> list[dict[str, Any]]:
        configs = self.store.list_configs(channel_id)
        if not role:
            profile = self.store.get_channel_profile(channel_id)
            role = profile.get("role") if profile else None
        if role not in {"vehicle_entry", "dispatch_room"}:
            return configs
        allowed_algorithms = {
            item["algorithm_type"]
            for item in role_defaults(role, self.settings.defaults_file)
        }
        return [item for item in configs if item.get("algorithm_type") in allowed_algorithms]

    def _initialize_configured_profiles(self) -> None:
        existing = {
            str(item.get("channel_id"))
            for item in self.store.list_channel_profiles()
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
                self.store.upsert_channel_profile(channel_id, binding)
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
            try:
                self.store.ensure_default_configs(
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

    def _prune_profile_configs(self, channel_id: str, role: str) -> None:
        if role not in {"vehicle_entry", "dispatch_room"}:
            return
        allowed_algorithms = {
            item["algorithm_type"]
            for item in role_defaults(role, self.settings.defaults_file)
        }
        deleted = self.store.prune_channel_configs(channel_id, allowed_algorithms)
        if deleted:
            logger.info(
                "[ElectronicSeal] pruned stale role configs channel_id=%s role=%s deleted=%s",
                channel_id,
                role,
                deleted,
            )

    def restore_enabled_channels(self) -> dict[str, int]:
        result = self.reconcile()
        self.restore_pending_uploads()
        return result

    def start_online_status_scheduler(self) -> None:
        self.online_status_scheduler.start()

    def sample_online_status(self) -> dict[str, Any]:
        return self.online_status_scheduler.sample()

    def online_rates(self, period: str = "day", channel_id: str | None = None) -> dict[str, Any]:
        now = datetime.now()
        if period == "day":
            start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "week":
            start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
            start_time -= timedelta(days=start_time.weekday())
        else:
            raise ValueError("period must be day or week")
        return {
            "period": period,
            "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "items": self.store.online_rates(start_time, now, channel_id),
        }

    def restore_pending_uploads(self) -> list[dict[str, Any]]:
        self._pending_upload_jobs = self.store.list_upload_jobs(
            {"pending", "retry", "uploading"}
        )
        return list(self._pending_upload_jobs)

    def apply_config(self, channel_id: str, algorithm_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if algorithm_type not in SUPPORTED_ALGORITHMS:
            raise ValueError(f"unsupported electronic-seal algorithm: {algorithm_type}")
        result = self.store.upsert_config(channel_id, algorithm_type, payload)
        self.reconcile()
        return result

    def apply_profile(self, channel_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.store.upsert_channel_profile(channel_id, payload)
        self.reconcile_channel(channel_id)
        return result

    def list_channels(self) -> dict[str, Any]:
        return {"items": self.store.list_channels()}

    def get_channel(self, channel_id: str) -> dict[str, Any]:
        channel = self.store.get_channel(channel_id)
        if not channel:
            raise ValueError(f"electronic-seal channel not found: {channel_id}")
        return channel

    async def play_channel(self, channel_id: str) -> dict[str, Any]:
        channel = self.get_channel(channel_id)
        return await self.channel_player.play_channel_payload(channel)

    def capture_snapshot(self, channel_id: str) -> dict[str, Any]:
        return self.snapshot_service.capture(channel_id)

    def latest_snapshot(self, channel_id: str) -> dict[str, Any]:
        return self.snapshot_service.latest(channel_id)

    def resolve_snapshot_image(self, channel_id: str):
        return self.snapshot_service.resolve_image(channel_id)

    def claim_channel(self, channel_id: str, role: str) -> dict[str, Any]:
        result = self.store.claim_channel(channel_id, role)
        self.reconcile_channel(channel_id)
        return result

    def release_channel(self, channel_id: str) -> dict[str, Any]:
        normalized_id = str(channel_id or "").strip()
        with self._lock:
            worker = self._workers.pop(normalized_id, None)
        try:
            if worker is not None:
                worker.request_stop()
            result = self.store.release_channel(normalized_id)
        except Exception:
            if worker is not None:
                try:
                    worker.wait_and_close()
                except Exception:
                    logger.exception(
                        "[ElectronicSeal] channel_id=%s release_cleanup_error",
                        normalized_id,
                    )
            try:
                self.reconcile()
            except Exception:
                logger.exception(
                    "[ElectronicSeal] channel_id=%s release_recovery_error",
                    normalized_id,
                )
            raise
        if worker is None:
            result["runtime_stop"] = "not_running"
            return result
        self._schedule_release_cleanup(normalized_id, worker)
        result["runtime_stop"] = "scheduled"
        return result

    def _schedule_release_cleanup(self, channel_id: str, worker: Any) -> None:
        try:
            cleanup_future = self._cleanup_executor.submit(worker.wait_and_close)
        except RuntimeError:
            logger.warning(
                "[ElectronicSeal] channel_id=%s cleanup_executor_unavailable",
                channel_id,
            )
            threading.Thread(
                target=worker.wait_and_close,
                name=f"ElectronicSealCleanup-{channel_id}",
                daemon=True,
            ).start()
            return
        cleanup_future.add_done_callback(
            lambda future: self._log_release_cleanup_result(channel_id, future)
        )

    @staticmethod
    def _log_release_cleanup_result(channel_id: str, future: Future[Any]) -> None:
        try:
            future.result()
        except Exception:
            logger.exception(
                "[ElectronicSeal] channel_id=%s release_cleanup_error",
                channel_id,
            )
        else:
            logger.info(
                "[ElectronicSeal] channel_id=%s release_cleanup_complete",
                channel_id,
            )

    def bind_channels(self, streams: list[str]) -> dict[str, Any]:
        unique_streams = list(dict.fromkeys(str(stream).strip() for stream in streams))
        items = []
        counts = {"bound": 0, "already_bound": 0, "failed": 0}
        for stream in unique_streams:
            try:
                result = self.store.bind_channel_by_stream(stream)
                status = str(result.get("status") or "bound")
                if status not in {"bound", "already_bound"}:
                    status = "bound"
                counts[status] += 1
                channel = result.get("channel") or {}
                items.append(
                    {
                        "stream": stream,
                        "status": status,
                        "channel": {
                            key: channel.get(key)
                            for key in ("id", "name", "stream", "gb_id", "is_online")
                            if key in channel
                        },
                    }
                )
            except Exception as exc:
                counts["failed"] += 1
                items.append(
                    {
                        "stream": stream,
                        "status": "failed",
                        "detail": str(exc),
                    }
                )
        return {
            "total": len(unique_streams),
            **counts,
            "items": items,
        }

    def list_configs(
        self,
        channel_id: str | None = None,
        *,
        include_common: bool = False,
    ) -> dict[str, Any]:
        configs = (
            self._channel_configs(channel_id)
            if channel_id
            else self.store.list_configs(channel_id)
        )
        if channel_id and not include_common:
            profile = self.store.get_channel_profile(channel_id)
            algorithm_type = _ROLE_SPECIFIC_ALGORITHMS.get(
                str((profile or {}).get("role") or "")
            )
            if algorithm_type:
                configs = [
                    item
                    for item in configs
                    if item.get("algorithm_type") == algorithm_type
                ]
        return {
            "items": configs,
            "profiles": self.store.list_channel_profiles(channel_id),
        }

    def list_models(self) -> dict[str, Any]:
        return {"models": self.store.list_models()}

    def get_model(self, model_id: str) -> dict[str, Any]:
        model = self.store.get_model(model_id)
        if model is None:
            raise ValueError(f"electronic-seal model not found: {model_id}")
        return model

    def recalibrate(self, channel_id: str, algorithm_type: str) -> dict[str, Any]:
        if algorithm_type not in SUPPORTED_ALGORITHMS:
            raise ValueError(f"unsupported electronic-seal algorithm: {algorithm_type}")
        result = self.store.invalidate_baseline(channel_id, algorithm_type)
        worker = self.worker(channel_id)
        if worker is not None:
            worker.reset_baseline(algorithm_type)
        return {**result, "status": "calibrating"}

    def get_status(self, channel_id: str) -> dict[str, Any]:
        worker = self.worker(channel_id)
        if worker is None:
            profile = self.store.get_channel_profile(channel_id)
            return {
                "channel_id": channel_id,
                "running": False,
                "worker_status": "stopped",
                "source_type": "electronic_seal_channels.flv_or_source_url",
                "role": profile.get("role") if profile else None,
                "bound": bool(profile.get("bound")) if profile else False,
                "configs": self._channel_configs(channel_id),
            }
        result = worker.status()
        profile = self.store.get_channel_profile(channel_id)
        if profile:
            result.update({"role": profile["role"], "bound": bool(profile["bound"])})
        return result

    def list_profiles(self, channel_id: str | None = None) -> dict[str, Any]:
        return {"items": self.store.list_channel_profiles(channel_id)}

    def overview(self) -> dict[str, Any]:
        profiles = self.store.list_bound_channel_profiles()
        items: list[dict[str, Any]] = []
        with SessionLocal() as session:
            for profile in profiles:
                channel_id = str(profile["channel_id"])
                row = session.query(ElectronicSealChannelMeta).filter(
                    ElectronicSealChannelMeta.id == channel_id
                ).one_or_none()
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
        result = self.store.requeue_upload_job(job_id)
        if result is None:
            raise ValueError(f"upload job not found: {job_id}")
        return result

    def worker(self, channel_id: str):
        with self._lock:
            return self._workers.get(str(channel_id))

    def stop_all(self) -> dict[str, int]:
        self.online_status_scheduler.stop()
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
