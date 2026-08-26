from __future__ import annotations

import threading
from typing import Any, Callable

from baseline.baseline_manager import BaselineManager
from storage.repository import CameraHealthRepository
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
        if bool(channel.get("person_count_enabled")):
            coordinator.register(channel, worker.frame_buffer)
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
