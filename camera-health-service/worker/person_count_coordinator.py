from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
import threading
import time
from typing import Any

from config import settings
from detectors.person_count import PersonCountDetector, PersonCountResult, encode_person_count
from person_count.snapshot_manager import PersonCountSnapshotManager
from storage.repository import CameraHealthRepository
from stream.frame_buffer import FrameBuffer


logger = logging.getLogger(__name__)


@dataclass
class _ChannelState:
    channel: dict[str, Any]
    frame_buffer: FrameBuffer
    last_sequence: int = 0
    last_inference_at: float | None = None
    last_person_count: int | None = None
    last_success_at: float | None = None
    last_error: str | None = None
    last_inference_ms: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class PersonCountCoordinator:
    """Schedule one shared person-count detector from each channel's latest frame."""

    def __init__(
        self,
        *,
        repository: CameraHealthRepository,
        detector: PersonCountDetector,
        snapshot_manager: PersonCountSnapshotManager | None = None,
        interval_seconds: float | None = None,
        frame_timeout_seconds: float | None = None,
        poll_interval_seconds: float = 0.05,
    ):
        self.repository = repository
        self.detector = detector
        self.snapshot_manager = snapshot_manager or PersonCountSnapshotManager()
        self.interval_seconds = max(
            float(
                interval_seconds
                if interval_seconds is not None
                else settings.person_count_interval_seconds
            ),
            0.001,
        )
        self.frame_timeout_seconds = max(
            float(
                frame_timeout_seconds
                if frame_timeout_seconds is not None
                else settings.frame_timeout_seconds
            ),
            0.1,
        )
        self.poll_interval_seconds = max(float(poll_interval_seconds), 0.01)
        self._channels: dict[str, _ChannelState] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._running = True
            self._thread = threading.Thread(
                target=self._run,
                name="camera-health-person-count",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            self._running = False
            self._thread = None

    def register(
        self,
        channel: dict[str, Any],
        frame_buffer: FrameBuffer,
    ) -> dict[str, Any]:
        channel_id = str(channel.get("id") or "").strip()
        if not channel_id:
            raise ValueError("channel id is required")
        with self._lock:
            current = self._channels.get(channel_id)
            if current is not None:
                with current.lock:
                    current.channel = dict(channel)
                    current.frame_buffer = frame_buffer
                return self._status_for(current)
            state = _ChannelState(
                channel=dict(channel),
                frame_buffer=frame_buffer,
            )
            self._channels[channel_id] = state
            return self._status_for(state)

    def unregister(self, channel_id: str) -> bool:
        with self._lock:
            return self._channels.pop(str(channel_id).strip(), None) is not None

    def process_once(
        self,
        channel_id: str,
        *,
        now: float | None = None,
    ) -> bool:
        key = str(channel_id).strip()
        with self._lock:
            state = self._channels.get(key)
        if state is None:
            return False
        current_time = float(now if now is not None else time.time())
        with state.lock:
            if (
                state.last_inference_at is not None
                and current_time - state.last_inference_at < self.interval_seconds
            ):
                return False
            snapshot = state.frame_buffer.snapshot(now=current_time)
            frame = snapshot["frame"]
            captured_at = snapshot["captured_at"]
            sequence = int(snapshot["sequence"] or 0)
            age_seconds = snapshot["age_seconds"]
            if frame is None or captured_at is None or age_seconds is None or age_seconds > self.frame_timeout_seconds:
                state.last_sequence = sequence
                state.last_error = "frame unavailable or stale"
                return False
            if sequence == state.last_sequence:
                return False
            state.last_sequence = sequence
            state.last_inference_at = current_time
            try:
                result = self.detector.analyze(frame)
            except Exception as exc:
                state.last_error = str(exc)
                logger.exception(
                    "person count inference failed channel_id=%s",
                    key,
                )
                return False
            state.last_success_at = current_time
            state.last_error = None
            state.last_inference_ms = result.inference_ms
            previous_count = state.last_person_count
            current_count = int(result.person_count)
            state.last_person_count = current_count
            if current_count <= 0:
                return True

            changed = previous_count is None or previous_count != current_count
            snapshot_path: str | None = None
            created_at = datetime.fromtimestamp(float(captured_at))
            if changed:
                try:
                    snapshot_path = self.snapshot_manager.save(
                        state.channel,
                        result,
                        created_at=created_at,
                    )["snapshot_path"]
                except Exception as exc:
                    state.last_error = f"snapshot failed: {exc}"
                    logger.warning(
                        "person count snapshot failed channel_id=%s error=%s",
                        key,
                        exc,
                    )
            try:
                self.repository.create_person_count_record(
                    {
                        "channel_id": key,
                        "analysis_type": "01",
                        "analysis_case": encode_person_count(current_count),
                        "snapshot_path": snapshot_path,
                        "created_at": created_at,
                    }
                )
            except Exception as exc:
                state.last_error = f"record persistence failed: {exc}"
                logger.exception(
                    "person count persistence failed channel_id=%s",
                    key,
                )
            return True

    def get_status(self, channel_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._channels.get(str(channel_id).strip())
        return self._status_for(state) if state else None

    def list_status(self) -> list[dict[str, Any]]:
        with self._lock:
            states = list(self._channels.values())
        return [self._status_for(state) for state in states]

    def close(self) -> None:
        self.stop()
        with self._lock:
            self._channels.clear()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                channel_ids = list(self._channels)
            for channel_id in channel_ids:
                if self._stop_event.is_set():
                    break
                try:
                    self.process_once(channel_id)
                except Exception:
                    logger.exception(
                        "person count coordinator cycle failed channel_id=%s",
                        channel_id,
                    )
            self._stop_event.wait(self.poll_interval_seconds)
        with self._lock:
            self._running = False

    def _status_for(self, state: _ChannelState | None) -> dict[str, Any]:
        if state is None:
            return {}
        with state.lock:
            return {
                "channel_id": str(state.channel.get("id") or "").strip(),
                "enabled": bool(state.channel.get("person_count_enabled")),
                "running": self._running,
                "current_count": state.last_person_count,
                "last_inference_at": state.last_inference_at,
                "last_success_at": state.last_success_at,
                "last_error": state.last_error,
                "last_inference_ms": state.last_inference_ms,
            }
