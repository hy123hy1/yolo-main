from __future__ import annotations

import threading
from typing import Any, Callable

from baseline.baseline_manager import BaselineManager
from storage.repository import CameraHealthRepository, effective_person_analysis_config
from worker.channel_worker import ChannelWorker


class WorkerManager:
    """Own at most one health Worker for each channel id."""

    def __init__(
        self,
        *,
        repository: CameraHealthRepository,
        baseline_manager: BaselineManager | None = None,
        worker_factory: Callable[..., ChannelWorker] = ChannelWorker,
        person_count_coordinator: Any | None = None,
    ):
        self.repository = repository
        self.baseline_manager = baseline_manager
        self.worker_factory = worker_factory
        self.person_count_coordinator = person_count_coordinator
        self._workers: dict[str, ChannelWorker] = {}
        self._lock = threading.RLock()

    def reconcile(self, channel: dict[str, Any]) -> dict[str, Any] | None:
        channel_id = str(channel.get("id") or "").strip()
        if not channel_id:
            return None
        sources = self._sources(channel)
        with self._lock:
            current = self._workers.get(channel_id)
            if not sources:
                if current is not None:
                    current.stop()
                    current.join(timeout=2.0)
                    self._workers.pop(channel_id, None)
                self._unregister_person_count(channel_id)
                return None
            if current is not None:
                if self._sources(current.channel) == sources:
                    self._reconcile_person_count(channel, current)
                    return current.status()
                current.stop()
                current.join(timeout=2.0)
                self._workers.pop(channel_id, None)
                self._unregister_person_count(channel_id)

            worker = self.worker_factory(
                channel,
                repository=self.repository,
                baseline_manager=self.baseline_manager,
            )
            worker.start()
            self._workers[channel_id] = worker
            self._reconcile_person_count(channel, worker)
            return worker.status()

    def stop_channel(self, channel_id: str) -> bool:
        key = str(channel_id).strip()
        with self._lock:
            worker = self._workers.pop(key, None)
            if worker is None:
                self._unregister_person_count(key)
                return False
            worker.stop()
            worker.join(timeout=2.0)
            self._unregister_person_count(key)
            return True

    def stop_missing(self, active_channel_ids: set[str]) -> int:
        active = {str(value).strip() for value in active_channel_ids}
        with self._lock:
            missing = [key for key in self._workers if key not in active]
        for channel_id in missing:
            self.stop_channel(channel_id)
        return len(missing)

    def get_status(self, channel_id: str) -> dict[str, Any] | None:
        with self._lock:
            worker = self._workers.get(str(channel_id).strip())
            return worker.status() if worker else None

    def reload_baseline(self, channel_id: str, snapshot_path: str) -> bool:
        key = str(channel_id).strip()
        with self._lock:
            worker = self._workers.get(key)
            if worker is None:
                return False
            reloader = getattr(worker, "reload_baseline", None)
            if reloader is None:
                return False
            reloader(snapshot_path)
            return True

    def capture_baseline(self, channel_id: str) -> dict[str, Any] | None:
        """Use a running Worker's live buffer for a fast baseline refresh."""
        key = str(channel_id).strip()
        with self._lock:
            worker = self._workers.get(key)
        if worker is None:
            return None
        capture = getattr(worker, "capture_baseline_from_buffer", None)
        if capture is None:
            return None
        return capture()

    def list_status(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                worker.status()
                for worker in self._workers.values()
            ]

    def get_person_count_status(self, channel_id: str) -> dict[str, Any] | None:
        coordinator = self.person_count_coordinator
        if coordinator is None:
            return None
        return coordinator.get_status(channel_id)

    def close(self) -> None:
        with self._lock:
            channel_ids = list(self._workers)
        for channel_id in channel_ids:
            self.stop_channel(channel_id)

    def _reconcile_person_count(
        self,
        channel: dict[str, Any],
        worker: ChannelWorker,
    ) -> None:
        coordinator = self.person_count_coordinator
        if coordinator is None:
            return
        channel_id = str(channel.get("id") or "").strip()
        algorithm_config = self.repository.get_channel_algorithm_config(
            channel_id,
            "person_count",
        )
        tailgating_config = self.repository.get_channel_algorithm_config(
            channel_id,
            "tailgating",
        )
        loitering_config = self.repository.get_channel_algorithm_config(
            channel_id,
            "loitering",
        )
        tailgating_enabled = bool(
            tailgating_config is not None and tailgating_config["enabled"]
        )
        loitering_enabled = bool(
            loitering_config is not None and loitering_config["enabled"]
        )
        normalized: dict[str, Any] | None = None
        if algorithm_config is not None:
            normalized = dict(channel)
            if algorithm_config["enabled"]:
                algorithm_settings = algorithm_config.get("config") or {}
                region_config = algorithm_settings.get("region_config") or {}
                line_config = algorithm_settings.get("line_config") or {}
                has_region = (
                    isinstance(region_config, dict)
                    and len(region_config.get("points") or []) == 4
                )
                direction = algorithm_settings.get("direction", "outside_to_inside")
                normalized.update(
                    {
                        "person_analysis_enabled": True,
                        "person_analysis_mode": "count_and_entry",
                        "person_analysis_direction": direction,
                        "person_analysis_line_config": line_config,
                        "person_analysis_region_config": region_config,
                        "person_count_enabled": True,
                        "person_count_interval_seconds": algorithm_config[
                            "analysis_interval"
                        ],
                        "person_entry_enabled": has_region or bool(line_config),
                        "person_entry_direction": direction,
                        "person_entry_line_config": line_config,
                        "person_entry_region_config": region_config,
                    }
                )
            else:
                normalized.update(
                    {
                        "person_analysis_enabled": False,
                        "person_count_enabled": False,
                        "person_entry_enabled": False,
                    }
                )
        else:
            effective = effective_person_analysis_config(channel)
            if effective["enabled"]:
                normalized = dict(channel)
                normalized.update(
                    {
                        "person_analysis_enabled": effective["enabled"],
                        "person_analysis_mode": effective["mode"],
                        "person_analysis_direction": effective["direction"],
                        "person_analysis_line_config": effective["line_config"],
                        "person_analysis_region_config": effective["region_config"],
                        "person_count_enabled": effective["count_enabled"],
                        "person_entry_enabled": effective["entry_enabled"],
                        "person_entry_direction": effective["direction"],
                        "person_entry_line_config": effective["line_config"],
                        "person_entry_region_config": effective["region_config"],
                    }
                )

        if tailgating_enabled:
            if normalized is None:
                normalized = dict(channel)
            tailgating_settings = dict(tailgating_config.get("config") or {})
            if not tailgating_settings.get("region_config"):
                reusable_region = (
                    normalized.get("person_analysis_region_config")
                    or normalized.get("person_entry_region_config")
                    or {}
                )
                if reusable_region:
                    tailgating_settings["region_config"] = reusable_region
            if not tailgating_settings.get("line_config"):
                reusable_line = (
                    normalized.get("person_analysis_line_config")
                    or normalized.get("person_entry_line_config")
                    or {}
                )
                if reusable_line:
                    tailgating_settings["line_config"] = reusable_line
            normalized.update(
                {
                    "person_behavior_enabled": True,
                    "tailgating_interval_seconds": tailgating_config[
                        "analysis_interval"
                    ],
                    "tailgating_config": tailgating_settings,
                }
            )

        if loitering_enabled:
            if normalized is None:
                normalized = dict(channel)
            loitering_settings = dict(loitering_config.get("config") or {})
            if not loitering_settings.get("region_config"):
                reusable_region = (
                    normalized.get("person_analysis_region_config")
                    or normalized.get("person_entry_region_config")
                    or {}
                )
                if reusable_region:
                    loitering_settings["region_config"] = reusable_region
            normalized.update(
                {
                    "loitering_enabled": True,
                    "loitering_interval_seconds": loitering_config[
                        "analysis_interval"
                    ],
                    "loitering_config": loitering_settings,
                }
            )

        if normalized is not None and (
            bool(normalized.get("person_count_enabled"))
            or bool(normalized.get("person_entry_enabled"))
            or bool(normalized.get("person_analysis_enabled"))
            or tailgating_enabled
            or loitering_enabled
        ):
            coordinator.register(normalized, worker.frame_buffer)
        else:
            coordinator.unregister(channel_id)

    def _unregister_person_count(self, channel_id: str) -> None:
        if self.person_count_coordinator is not None:
            self.person_count_coordinator.unregister(channel_id)

    @staticmethod
    def _sources(channel: dict[str, Any]) -> list[tuple[str, str]]:
        source_url = str(channel.get("source_url") or "").strip()
        flv = str(channel.get("flv") or "").strip()
        sources: list[tuple[str, str]] = []
        if source_url:
            sources.append(("rtsp", source_url))
        if flv and flv != source_url:
            sources.append(("flv", flv))
        return sources
