from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
import threading
import time
from typing import Any, Callable

from config import settings
from detectors.person_count import PersonCountDetector, PersonCountResult, encode_person_count
from person_count.entry_tracker import PersonEntryDetector
from person_count.loitering_detector import LoiteringDetector
from person_count.snapshot_manager import PersonCountSnapshotManager
from person_count.tailgating_detector import TailgatingDetector
from storage.repository import CameraHealthRepository, person_entry_analysis_type
from stream.frame_buffer import FrameBuffer


logger = logging.getLogger(__name__)


@dataclass
class _ChannelState:
    channel: dict[str, Any]
    frame_buffer: FrameBuffer
    detector: Any | None = None
    last_sequence: int = 0
    last_inference_at: float | None = None
    last_count_inference_at: float | None = None
    last_person_count: int | None = None
    last_recorded_count: int | None = None
    last_recorded_at: float | None = None
    last_success_at: float | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    last_inference_ms: float | None = None
    entry_detector: PersonEntryDetector | None = None
    entry_frame_size: tuple[Any, ...] | None = None
    last_entry_inference_at: float | None = None
    last_entry_at: float | None = None
    count_interval_seconds: float | None = None
    behavior_interval_seconds: float | None = None
    tailgating_detector: TailgatingDetector | None = None
    behavior_frame_size: tuple[Any, ...] | None = None
    last_behavior_inference_at: float | None = None
    loitering_detector: LoiteringDetector | None = None
    loitering_frame_size: tuple[Any, ...] | None = None
    loitering_interval_seconds: float | None = None
    last_loitering_inference_at: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class PersonCountCoordinator:
    """Schedule isolated person-analysis contexts from each channel's latest frame."""

    def __init__(
        self,
        *,
        repository: CameraHealthRepository,
        detector: PersonCountDetector,
        detector_factory: Callable[[], Any] | None = None,
        snapshot_manager: PersonCountSnapshotManager | None = None,
        interval_seconds: float | None = None,
        entry_interval_seconds: float | None = None,
        record_interval_seconds: float | None = None,
        frame_timeout_seconds: float | None = None,
        poll_interval_seconds: float = 0.05,
    ):
        self.repository = repository
        self.detector = detector
        self.detector_factory = detector_factory
        self.snapshot_manager = snapshot_manager or PersonCountSnapshotManager()
        self.interval_seconds = max(
            float(
                interval_seconds
                if interval_seconds is not None
                else settings.person_count_interval_seconds
            ),
            0.001,
        )
        self.entry_interval_seconds = max(
            float(
                entry_interval_seconds
                if entry_interval_seconds is not None
                else settings.person_entry_interval_seconds
            ),
            0.001,
        )
        self.record_interval_seconds = max(
            float(
                record_interval_seconds
                if record_interval_seconds is not None
                else settings.person_count_record_interval_seconds
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
                    stream_changed = current.frame_buffer is not frame_buffer
                    entry_changed = self._entry_config_signature(
                        current.channel
                    ) != self._entry_config_signature(channel)
                    behavior_changed = self._behavior_config_signature(
                        current.channel
                    ) != self._behavior_config_signature(channel)
                    loitering_changed = self._loitering_config_signature(
                        current.channel
                    ) != self._loitering_config_signature(channel)
                    current.channel = dict(channel)
                    current.frame_buffer = frame_buffer
                    current.count_interval_seconds = _channel_count_interval(
                        channel, self.interval_seconds
                    )
                    current.behavior_interval_seconds = _channel_behavior_interval(
                        channel, self.entry_interval_seconds
                    )
                    if stream_changed or entry_changed:
                        current.entry_detector = None
                        current.entry_frame_size = None
                    if stream_changed or behavior_changed:
                        current.tailgating_detector = None
                        current.behavior_frame_size = None
                    current.loitering_interval_seconds = _channel_loitering_interval(
                        channel, self.entry_interval_seconds
                    )
                    if stream_changed or loitering_changed:
                        current.loitering_detector = None
                        current.loitering_frame_size = None
                    if stream_changed:
                        current.detector = None
                return self._status_for(current)
            state = _ChannelState(
                channel=dict(channel),
                frame_buffer=frame_buffer,
                count_interval_seconds=_channel_count_interval(
                    channel, self.interval_seconds
                ),
                behavior_interval_seconds=_channel_behavior_interval(
                    channel, self.entry_interval_seconds
                ),
                loitering_interval_seconds=_channel_loitering_interval(
                    channel, self.entry_interval_seconds
                ),
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
            count_enabled = bool(state.channel.get("person_count_enabled"))
            entry_enabled = bool(state.channel.get("person_entry_enabled"))
            behavior_enabled = bool(state.channel.get("person_behavior_enabled"))
            loitering_enabled = bool(state.channel.get("loitering_enabled"))
            count_due = count_enabled and (
                state.last_count_inference_at is None
                or current_time - state.last_count_inference_at
                >= (state.count_interval_seconds or self.interval_seconds)
            )
            entry_due = entry_enabled and (
                state.last_entry_inference_at is None
                or current_time - state.last_entry_inference_at >= self.entry_interval_seconds
            )
            behavior_due = behavior_enabled and (
                state.last_behavior_inference_at is None
                or current_time - state.last_behavior_inference_at
                >= (state.behavior_interval_seconds or self.entry_interval_seconds)
            )
            loitering_due = loitering_enabled and (
                state.last_loitering_inference_at is None
                or current_time - state.last_loitering_inference_at
                >= (state.loitering_interval_seconds or self.entry_interval_seconds)
            )
            if not count_due and not entry_due and not behavior_due and not loitering_due:
                return False
            snapshot = state.frame_buffer.snapshot(now=current_time)
            frame = snapshot["frame"]
            captured_at = snapshot["captured_at"]
            sequence = int(snapshot["sequence"] or 0)
            age_seconds = snapshot["age_seconds"]
            if frame is None or captured_at is None or age_seconds is None or age_seconds > self.frame_timeout_seconds:
                state.last_sequence = sequence
                state.last_error = "frame unavailable or stale"
                state.consecutive_failures += 1
                return False
            if sequence == state.last_sequence:
                return False
            state.last_sequence = sequence
            state.last_inference_at = current_time
            if count_due:
                state.last_count_inference_at = current_time
            if entry_due:
                state.last_entry_inference_at = current_time
            if behavior_due:
                state.last_behavior_inference_at = current_time
            if loitering_due:
                state.last_loitering_inference_at = current_time
            try:
                detector = self._detector_for(state)
                result = self._analyze(
                    frame,
                    detector=detector,
                    tracking=bool(
                        (entry_due and entry_enabled)
                        or (behavior_due and behavior_enabled)
                        or (loitering_due and loitering_enabled)
                    ),
                )
            except Exception as exc:
                state.last_error = str(exc)
                state.consecutive_failures += 1
                logger.exception(
                    "person count inference failed channel_id=%s",
                    key,
                )
                return False
            state.last_success_at = current_time
            state.last_error = None
            state.consecutive_failures = 0
            state.last_inference_ms = result.inference_ms
            previous_count = state.last_person_count
            current_count = int(result.person_count)
            state.last_person_count = current_count

            if entry_due:
                try:
                    entry_detector = self._ensure_entry_detector(state, frame)
                    if entry_detector is not None:
                        for event in entry_detector.update(
                            self._entry_detections(result),
                            float(captured_at),
                        ):
                            snapshot_path: str | None = None
                            try:
                                save_entry = getattr(
                                    self.snapshot_manager,
                                    "save_entry",
                                    None,
                                )
                                if callable(save_entry):
                                    snapshot_path = save_entry(
                                        state.channel,
                                        result,
                                        event,
                                        created_at=datetime.fromtimestamp(float(captured_at)),
                                    )["snapshot_path"]
                            except Exception as exc:
                                logger.warning(
                                    "person entry snapshot failed channel_id=%s track_id=%s error=%s",
                                    key,
                                    event.track_id,
                                    exc,
                                )
                            try:
                                self.repository.create_person_entry_event(
                                    {
                                        "channel_id": key,
                                        "track_id": event.track_id,
                                        "line_id": event.line_id,
                                        "direction": event.direction,
                                        "analysis_type": person_entry_analysis_type(
                                            event.direction
                                        ),
                                        "confidence": event.confidence,
                                        "bbox": event.bbox,
                                        "snapshot_path": snapshot_path,
                                        "crossing_timestamp": event.crossing_timestamp,
                                    }
                                )
                                state.last_entry_at = current_time
                            except Exception as exc:
                                state.last_error = f"entry persistence failed: {exc}"
                                logger.exception(
                                    "person entry persistence failed channel_id=%s track_id=%s",
                                    key,
                                    event.track_id,
                                )
                except Exception as exc:
                    state.last_error = f"entry tracking failed: {exc}"
                    logger.exception("person entry tracking failed channel_id=%s", key)

            if behavior_due:
                try:
                    tailgating_detector = self._ensure_tailgating_detector(state, frame)
                    if tailgating_detector is not None:
                        for event in tailgating_detector.update(
                            self._entry_detections(result),
                            float(captured_at),
                        ):
                            snapshot_path: str | None = None
                            try:
                                save_behavior = getattr(
                                    self.snapshot_manager,
                                    "save_behavior",
                                    None,
                                )
                                if callable(save_behavior):
                                    snapshot_path = save_behavior(
                                        state.channel,
                                        result,
                                        event,
                                        created_at=datetime.fromtimestamp(float(captured_at)),
                                    )["snapshot_path"]
                            except Exception as exc:
                                logger.warning(
                                    "tailgating snapshot failed channel_id=%s track_id=%s error=%s",
                                    key,
                                    event.follower_track_id,
                                    exc,
                                )
                            try:
                                self.repository.create_person_behavior_event(
                                    {
                                        "channel_id": key,
                                        "algorithm_code": "tailgating",
                                        "behavior_code": event.behavior_code,
                                        "track_id": event.follower_track_id,
                                        "related_track_ids": [
                                            event.leader_track_id,
                                            event.follower_track_id,
                                        ],
                                        "confidence": event.confidence,
                                        "bbox": event.bbox,
                                        "snapshot_path": snapshot_path,
                                        "event_timestamp": event.event_timestamp,
                                    }
                                )
                            except Exception as exc:
                                state.last_error = f"behavior persistence failed: {exc}"
                                logger.exception(
                                    "tailgating persistence failed channel_id=%s track_id=%s",
                                    key,
                                    event.follower_track_id,
                                )
                except Exception as exc:
                    state.last_error = f"tailgating tracking failed: {exc}"
                    logger.exception("tailgating tracking failed channel_id=%s", key)

            if loitering_due:
                try:
                    loitering_detector = self._ensure_loitering_detector(state, frame)
                    if loitering_detector is not None:
                        for event in loitering_detector.update(
                            self._entry_detections(result),
                            float(captured_at),
                        ):
                            snapshot_path: str | None = None
                            try:
                                save_behavior = getattr(
                                    self.snapshot_manager,
                                    "save_behavior",
                                    None,
                                )
                                if callable(save_behavior):
                                    snapshot_path = save_behavior(
                                        state.channel,
                                        result,
                                        event,
                                        created_at=datetime.fromtimestamp(float(captured_at)),
                                    )["snapshot_path"]
                            except Exception as exc:
                                logger.warning(
                                    "loitering snapshot failed channel_id=%s track_id=%s error=%s",
                                    key,
                                    event.track_id,
                                    exc,
                                )
                            try:
                                self.repository.create_person_behavior_event(
                                    {
                                        "channel_id": key,
                                        "algorithm_code": "loitering",
                                        "behavior_code": event.behavior_code,
                                        "track_id": event.track_id,
                                        "related_track_ids": [event.track_id],
                                        "confidence": event.confidence,
                                        "bbox": event.bbox,
                                        "metrics": {
                                            "dwell_seconds": event.dwell_seconds,
                                        },
                                        "snapshot_path": snapshot_path,
                                        "event_timestamp": event.event_timestamp,
                                    }
                                )
                            except Exception as exc:
                                state.last_error = f"behavior persistence failed: {exc}"
                                logger.exception(
                                    "loitering persistence failed channel_id=%s track_id=%s",
                                    key,
                                    event.track_id,
                                )
                except Exception as exc:
                    state.last_error = f"loitering tracking failed: {exc}"
                    logger.exception("loitering tracking failed channel_id=%s", key)

            changed = (
                current_count > 0
                and (previous_count is None or previous_count != current_count)
            )
            should_record = (
                state.last_recorded_count is None
                or state.last_recorded_count != current_count
                or state.last_recorded_at is None
                or current_time - state.last_recorded_at >= self.record_interval_seconds
            )
            if not count_enabled or not count_due or not should_record:
                return True
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
                        "analysis_type": person_entry_analysis_type(
                            str(
                                state.channel.get("person_analysis_direction")
                                or state.channel.get("person_entry_direction")
                                or "outside_to_inside"
                            ).strip().lower()
                        ),
                        "analysis_case": encode_person_count(current_count),
                        "snapshot_path": snapshot_path,
                        "created_at": created_at,
                    }
                )
                state.last_recorded_count = current_count
                state.last_recorded_at = current_time
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
                "last_recorded_at": state.last_recorded_at,
                "last_inference_at": state.last_inference_at,
                "last_count_inference_at": state.last_count_inference_at,
                "last_success_at": state.last_success_at,
                "last_error": state.last_error,
                "consecutive_failures": state.consecutive_failures,
                "last_inference_ms": state.last_inference_ms,
                "last_entry_inference_at": state.last_entry_inference_at,
                "last_entry_at": state.last_entry_at,
                "last_behavior_inference_at": state.last_behavior_inference_at,
                "last_loitering_inference_at": state.last_loitering_inference_at,
            }

    @staticmethod
    def _entry_detections(result: PersonCountResult) -> list[dict[str, Any]]:
        detections = []
        for detection in result.detections:
            item = {
                "label": "person",
                "confidence": detection.confidence,
                "bbox": list(detection.box),
            }
            if detection.track_id is not None:
                item["track_id"] = detection.track_id
            detections.append(item)
        return detections

    def _analyze(
        self,
        frame: Any,
        *,
        detector: Any,
        tracking: bool,
    ) -> PersonCountResult:
        return detector.analyze(frame, track=tracking)

    def _detector_for(self, state: _ChannelState) -> Any:
        if state.detector is not None:
            return state.detector
        if self.detector_factory is not None:
            state.detector = self.detector_factory()
        else:
            new_context = getattr(self.detector, "new_context", None)
            state.detector = (
                new_context() if callable(new_context) else self.detector
            )
        return state.detector

    @staticmethod
    def _entry_config_signature(channel: dict[str, Any]) -> tuple[Any, ...]:
        return (
            bool(channel.get("person_entry_enabled")),
            repr(channel.get("person_analysis_region_config") or {}),
            repr(channel.get("person_entry_line_config") or {}),
            repr(channel.get("person_entry_region_config") or {}),
            str(
                channel.get("person_entry_direction")
                or channel.get("person_analysis_direction")
                or "outside_to_inside"
            ),
        )

    @staticmethod
    def _behavior_config_signature(channel: dict[str, Any]) -> tuple[Any, ...]:
        return (
            bool(channel.get("person_behavior_enabled")),
            repr(channel.get("tailgating_config") or {}),
            str(channel.get("person_entry_direction") or ""),
        )

    @staticmethod
    def _loitering_config_signature(channel: dict[str, Any]) -> tuple[Any, ...]:
        return (
            bool(channel.get("loitering_enabled")),
            repr(channel.get("loitering_config") or {}),
        )

    @staticmethod
    def _entry_config_key(
        channel: dict[str, Any],
        frame: Any,
    ) -> tuple[Any, ...]:
        height, width = frame.shape[:2]
        return (
            width,
            height,
            repr(channel.get("person_analysis_region_config") or {}),
            repr(channel.get("person_entry_line_config") or {}),
            repr(channel.get("person_entry_region_config") or {}),
            str(
                channel.get("person_entry_direction")
                or channel.get("person_analysis_direction")
                or "outside_to_inside"
            ),
        )

    def _ensure_entry_detector(
        self,
        state: _ChannelState,
        frame: Any,
    ) -> PersonEntryDetector | None:
        if not bool(state.channel.get("person_entry_enabled")):
            return None
        line_config = state.channel.get("person_entry_line_config") or {}
        region_config = state.channel.get("person_entry_region_config") or {}
        if region_config and len(region_config.get("points") or []) == 4:
            key = self._entry_config_key(state.channel, frame)
            if state.entry_detector is not None and state.entry_frame_size == key:
                return state.entry_detector
            height, width = frame.shape[:2]
            state.entry_detector = PersonEntryDetector.from_region_config(
                region_config,
                frame_width=width,
                frame_height=height,
            )
            state.entry_frame_size = key
            return state.entry_detector
        if not line_config:
            raise ValueError("person entry line configuration is required")
        key = self._entry_config_key(state.channel, frame)
        if state.entry_detector is not None and state.entry_frame_size == key:
            return state.entry_detector
        height, width = frame.shape[:2]
        state.entry_detector = PersonEntryDetector.from_config(
            line_config,
            region_config,
            frame_width=width,
            frame_height=height,
            direction=(
                state.channel.get("person_entry_direction")
                or state.channel.get("person_analysis_direction")
                or "outside_to_inside"
            ),
        )
        state.entry_frame_size = key
        return state.entry_detector

    def _ensure_tailgating_detector(
        self,
        state: _ChannelState,
        frame: Any,
    ) -> TailgatingDetector | None:
        if not bool(state.channel.get("person_behavior_enabled")):
            return None
        config = state.channel.get("tailgating_config") or {}
        if not isinstance(config, dict):
            raise ValueError("tailgating config must be an object")
        region_config = config.get("region_config") or {}
        line_config = config.get("line_config") or {}
        key = (
            frame.shape[1],
            frame.shape[0],
            repr(config),
            str(state.channel.get("person_entry_direction") or ""),
        )
        if state.tailgating_detector is not None and state.behavior_frame_size == key:
            return state.tailgating_detector
        height, width = frame.shape[:2]
        options = {
            "max_follow_seconds": float(config.get("max_follow_seconds", 2.0)),
            "max_person_gap": float(config.get("max_person_gap", 0.25)),
            "cooldown_seconds": float(config.get("cooldown_seconds", 5.0)),
        }
        if region_config and len(region_config.get("points") or []) == 4:
            state.tailgating_detector = TailgatingDetector.from_region_config(
                region_config,
                frame_width=width,
                frame_height=height,
                **options,
            )
        else:
            if not line_config:
                raise ValueError("tailgating region or line configuration is required")
            state.tailgating_detector = TailgatingDetector.from_config(
                line_config,
                region_config,
                frame_width=width,
                frame_height=height,
                direction=(
                    config.get("direction")
                    or state.channel.get("person_entry_direction")
                    or "outside_to_inside"
                ),
                **options,
            )
        state.behavior_frame_size = key
        return state.tailgating_detector

    def _ensure_loitering_detector(
        self,
        state: _ChannelState,
        frame: Any,
    ) -> LoiteringDetector | None:
        if not bool(state.channel.get("loitering_enabled")):
            return None
        config = state.channel.get("loitering_config") or {}
        if not isinstance(config, dict):
            raise ValueError("loitering config must be an object")
        region_config = config.get("region_config") or {}
        if len(region_config.get("points") or []) != 4:
            raise ValueError("loitering region configuration is required")
        key = (frame.shape[1], frame.shape[0], repr(config))
        if state.loitering_detector is not None and state.loitering_frame_size == key:
            return state.loitering_detector
        height, width = frame.shape[:2]
        state.loitering_detector = LoiteringDetector.from_region_config(
            region_config,
            frame_width=width,
            frame_height=height,
            dwell_seconds=float(config.get("dwell_seconds", 30.0)),
        )
        state.loitering_frame_size = key
        return state.loitering_detector


def _channel_count_interval(channel: dict[str, Any], fallback: float) -> float:
    try:
        value = float(channel.get("person_count_interval_seconds", fallback))
    except (TypeError, ValueError, OverflowError):
        return fallback
    return max(value, 0.001)


def _channel_behavior_interval(channel: dict[str, Any], fallback: float) -> float:
    try:
        value = float(channel.get("tailgating_interval_seconds", fallback))
    except (TypeError, ValueError, OverflowError):
        return fallback
    return max(value, 0.001)


def _channel_loitering_interval(channel: dict[str, Any], fallback: float) -> float:
    try:
        value = float(channel.get("loitering_interval_seconds", fallback))
    except (TypeError, ValueError, OverflowError):
        return fallback
    return max(value, 0.001)
