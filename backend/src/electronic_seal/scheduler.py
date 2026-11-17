from __future__ import annotations

from datetime import datetime
import logging
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


logger = logging.getLogger("uvicorn.error")


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
    """Schedule independent periodic checks without touching camera-health tables."""

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
        evidence_service: Any | None = None,
        result_callback: Callable[[list[Any], datetime], None] | None = None,
        runtime_owner_id: str | None = None,
    ) -> None:
        self.store = store or ElectronicSealStore()
        self.settings = settings or ElectronicSealSettings()
        self.frame_provider = frame_provider
        self.channel_id = str(channel_id) if channel_id is not None else None
        self.runner = runner or ElectronicSealCameraHealthAdapter(
            store=self.store,
            baseline_dir=self.settings.data_dir / "baselines",
            evidence_service=evidence_service,
            baseline_warmup_seconds=self.settings.baseline_warmup_seconds,
            baseline_min_valid_frames=self.settings.baseline_min_valid_frames,
            baseline_max_calibration_seconds=self.settings.baseline_max_calibration_seconds,
            runtime_owner_id=runtime_owner_id,
        )
        self.monotonic = monotonic
        self.wall_clock = wall_clock
        self.poll_interval_seconds = max(float(poll_interval_seconds), 0.05)
        self.result_callback = result_callback
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
        if results and self.result_callback is not None:
            try:
                self.result_callback(results, wall_now)
            except Exception:
                logger.exception(
                    "[ElectronicSeal] health result callback failed channel_id=%s",
                    self.channel_id,
                )
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
        self.request_stop()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=3.0)
        self._thread = None

    def request_stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            self.tick()
            self._stop_event.wait(self.poll_interval_seconds)


def _floor_to_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


class ElectronicSealOnlineStatusScheduler:
    """Persist one WVP online-status sample per electronic-seal channel per hour."""

    def __init__(
        self,
        *,
        store: ElectronicSealStore,
        wvp_service: Any,
        interval_seconds: float = 3600.0,
        wall_clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.store = store
        self.wvp_service = wvp_service
        self.interval_seconds = max(float(interval_seconds), 1.0)
        self.wall_clock = wall_clock
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def sample(self, sampled_at: datetime | None = None) -> dict[str, Any]:
        sample_time = _floor_to_hour(sampled_at or self.wall_clock())
        channels = list(self.store.list_channels() or [])
        samples = {
            str(channel.get("id") or "").strip(): {
                "id": str(channel.get("id") or "").strip(),
                "name": str(channel.get("name") or ""),
                "gb_id": str(channel.get("gb_id") or "").strip(),
                "stream": str(channel.get("stream") or "").strip(),
                "is_online": False,
            }
            for channel in channels
            if str(channel.get("id") or "").strip()
        }

        online_channel_ids: set[str] = set()
        try:
            remote_items = self._list_all_remote_channels()
            for remote_item in remote_items:
                if not self._remote_is_online(remote_item):
                    continue
                remote_ids = self._remote_identity_values(remote_item)
                for channel_id, channel in samples.items():
                    channel_ids = {
                        str(channel.get("id") or "").strip(),
                        str(channel.get("gb_id") or "").strip(),
                        str(channel.get("stream") or "").strip(),
                    }
                    if remote_ids.intersection(value for value in channel_ids if value):
                        online_channel_ids.add(channel_id)
        except Exception as exc:
            # The requested policy is fail-closed: an unavailable or malformed
            # WVP response leaves every electronic-seal channel offline.
            logger.warning(
                "[ElectronicSealOnlineStatus] WVP status query failed, mark offline error=%s",
                exc,
            )

        for channel_id, sample in samples.items():
            sample["is_online"] = channel_id in online_channel_ids
        self.store.record_online_status_samples(list(samples.values()), sample_time)
        online_count = sum(1 for item in samples.values() if item["is_online"])
        return {
            "sampled_at": sample_time.strftime("%Y-%m-%d %H:%M:%S"),
            "total": len(samples),
            "online": online_count,
            "offline": len(samples) - online_count,
        }

    def _list_all_remote_channels(self) -> list[dict[str, Any]]:
        page = 1
        count = 100
        items: list[dict[str, Any]] = []
        while True:
            result = self.wvp_service.list_remote_channels(page=page, count=count)
            if not isinstance(result, dict) or not bool(result.get("enabled")):
                return []
            page_items = [
                item for item in result.get("items") or [] if isinstance(item, dict)
            ]
            items.extend(page_items)
            total = int(result.get("total") or len(items))
            if not page_items or len(items) >= total:
                return items
            page += 1

    @staticmethod
    def _remote_identity_values(remote_item: dict[str, Any]) -> set[str]:
        values = {
            remote_item.get("id"),
            remote_item.get("channelId"),
            remote_item.get("channel_id"),
            remote_item.get("gbId"),
            remote_item.get("gb_id"),
            remote_item.get("stream"),
            remote_item.get("streamId"),
        }
        return {str(value).strip() for value in values if str(value or "").strip()}

    @staticmethod
    def _remote_is_online(remote_item: dict[str, Any]) -> bool:
        falsey_text = {"off", "offline", "false", "0", "no"}
        truthy_text = {"on", "online", "true", "1", "yes"}
        observed = False
        for key in ("gbStatus", "status", "online", "isOnline", "gbOnline"):
            if key not in remote_item:
                continue
            observed = True
            value = remote_item.get(key)
            if value is True or (isinstance(value, (int, float)) and value == 1):
                return True
            if isinstance(value, str) and value.strip().lower() in truthy_text:
                return True
            if value is False or (isinstance(value, (int, float)) and value == 0):
                return False
            if isinstance(value, str) and value.strip().lower() in falsey_text:
                return False
        return False if observed else False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self.run,
            name="ElectronicSealOnlineStatusScheduler",
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
            try:
                self.sample()
            except Exception:
                logger.exception("[ElectronicSealOnlineStatus] sample failed")
            self._stop_event.wait(self.interval_seconds)


__all__ = [
    "ElectronicSealCameraHealthScheduler",
    "ElectronicSealOnlineStatusScheduler",
]
