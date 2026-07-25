from __future__ import annotations

from datetime import datetime
import threading
import time
from typing import Any, Callable

import numpy as np

from src.electronic_seal.algorithms.camera_health import (
    ElectronicSealCameraHealthAdapter,
    SUPPORTED_CAMERA_HEALTH_ALGORITHMS,
)
from src.electronic_seal.settings import ElectronicSealSettings
from src.electronic_seal.store import ElectronicSealStore


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


class ElectronicSealCameraHealthScheduler:
    """Schedule independent hourly checks without touching camera-health tables."""

    def __init__(
        self,
        *,
        frame_provider: Callable[[str], np.ndarray | None],
        channel_id: str | None = None,
        store: Any | None = None,
        runner: Any | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = datetime.now,
        poll_interval_seconds: float = 1.0,
        settings: ElectronicSealSettings | None = None,
    ) -> None:
        self.store = store or ElectronicSealStore()
        self.settings = settings or ElectronicSealSettings()
        self.frame_provider = frame_provider
        self.channel_id = str(channel_id) if channel_id is not None else None
        self.runner = runner or ElectronicSealCameraHealthAdapter(
            store=self.store,
            baseline_dir=self.settings.data_dir / "baselines",
        )
        self.monotonic = monotonic
        self.wall_clock = wall_clock
        self.poll_interval_seconds = max(float(poll_interval_seconds), 0.05)
        self._next_due: dict[tuple[str, str], float] = {}
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> list[Any]:
        mono_now = float(self.monotonic())
        wall_now = self.wall_clock()
        configs = self.store.list_configs()
        due_configs: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str]] = set()

        for config in configs:
            key = (str(config["channel_id"]), str(config["algorithm_type"]))
            if self.channel_id is not None and key[0] != self.channel_id:
                continue
            if key in seen_keys:
                continue
            seen_keys.add(key)
            if key[1] not in SUPPORTED_CAMERA_HEALTH_ALGORITHMS:
                self._next_due.pop(key, None)
                continue

            interval = max(
                float(
                    config.get("camera_health_interval_seconds")
                    or self.settings.camera_health_interval_seconds
                ),
                1.0,
            )
            state = self.store.get_state(*key) or {}
            last_run_at = _parse_datetime(state.get("last_run_at"))
            if last_run_at is not None:
                elapsed = (wall_now - last_run_at).total_seconds()
                if elapsed < interval:
                    self._next_due[key] = max(
                        self._next_due.get(key, mono_now),
                        mono_now + max(interval - max(elapsed, 0.0), 0.0),
                    )
                    continue

            if mono_now < self._next_due.get(key, mono_now):
                continue
            due_configs.append(config)

        frame_cache: dict[str, np.ndarray | None] = {}
        results: list[Any] = []
        for config in due_configs:
            channel_id = str(config["channel_id"])
            key = (channel_id, str(config["algorithm_type"]))
            if channel_id not in frame_cache:
                frame_cache[channel_id] = self.frame_provider(channel_id)
            result = self.runner.run(config, frame_cache[channel_id], wall_now)
            results.append(result)
            interval = max(
                float(
                    config.get("camera_health_interval_seconds")
                    or self.settings.camera_health_interval_seconds
                ),
                1.0,
            )
            self._next_due[key] = mono_now + interval
        return results

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self.run,
            name="ElectronicSealCameraHealthScheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=3.0)
        self._thread = None

    def run(self) -> None:
        while not self._stop_event.is_set():
            self.tick()
            self._stop_event.wait(self.poll_interval_seconds)


__all__ = ["ElectronicSealCameraHealthScheduler"]
