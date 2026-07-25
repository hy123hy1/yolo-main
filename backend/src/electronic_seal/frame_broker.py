from __future__ import annotations

import inspect
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from src.vision_core.ffmpeg_capture import FFmpegCapture


logger = logging.getLogger(__name__)


def _copy_frame(frame: Any) -> Any:
    copier = getattr(frame, "copy", None)
    return copier() if callable(copier) else frame


@dataclass(frozen=True)
class FrameEnvelope:
    channel_id: str
    sequence: int
    timestamp: float
    frame: Any = field(repr=False)

    @property
    def image(self) -> Any:
        return self.frame

    @property
    def captured_at(self) -> float:
        return self.timestamp


@dataclass
class _ChannelState:
    channel_id: str
    source_url: str
    subscriptions: dict[str, "FrameSubscription"] = field(default_factory=dict)
    latest: FrameEnvelope | None = None
    sequence: int = 0
    status: str = "offline"
    last_frame_timestamp: float | None = None
    last_success_monotonic: float | None = None
    last_error: str | None = None
    capture: Any = None
    capture_creations: int = 0
    stop_event: threading.Event = field(default_factory=threading.Event)
    next_reconnect_monotonic: float = 0.0
    generation: int = 0
    reader_thread: threading.Thread | None = None
    draining: bool = False
    drain_watcher_thread: threading.Thread | None = None


@dataclass(frozen=True)
class _CleanupHandles:
    capture: Any = None
    reader_thread: threading.Thread | None = None


class FrameSubscription:
    def __init__(self, broker: "FrameBroker", channel_id: str, token: str):
        self._broker = broker
        self.channel_id = channel_id
        self.token = token
        self._last_sequence = 0
        self._closed = False

    def get_latest(self, timeout: float | None = None) -> FrameEnvelope:
        if self._closed:
            if self._broker._shutdown:
                raise RuntimeError("frame broker is shut down")
            raise RuntimeError("subscription is closed")
        return self._broker._get_for_subscription(self, timeout)

    def unsubscribe(self) -> None:
        if not self._closed:
            self._closed = True
            self._broker.unsubscribe(self.channel_id, self.token)

    def __enter__(self) -> "FrameSubscription":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.unsubscribe()


class FrameBroker:
    """One latest-frame FFmpeg reader per channel with fan-out subscriptions."""

    def __init__(
        self,
        *,
        capture_factory: Callable[..., Any] = FFmpegCapture,
        width: int = 1280,
        height: int = 720,
        init_wait: float = 0.0,
        stale_grace_seconds: float = 5.0,
        reconnect_delay_seconds: float = 1.0,
        read_retry: int = 1,
        start_reader: bool = True,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        reader_join_timeout: float = 1.0,
    ):
        if width <= 0 or height <= 0:
            raise ValueError("width and height must be positive")
        if stale_grace_seconds < 0:
            raise ValueError("stale_grace_seconds must be non-negative")
        if reconnect_delay_seconds < 0:
            raise ValueError("reconnect_delay_seconds must be non-negative")
        if reader_join_timeout < 0:
            raise ValueError("reader_join_timeout must be non-negative")

        self.capture_factory = capture_factory
        self.width = width
        self.height = height
        self.init_wait = init_wait
        self.stale_grace_seconds = float(stale_grace_seconds)
        self.reconnect_delay_seconds = float(reconnect_delay_seconds)
        self.read_retry = max(1, int(read_retry))
        self.start_reader = bool(start_reader)
        self._clock = clock
        self._monotonic = monotonic
        self.reader_join_timeout = float(reader_join_timeout)
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._lifecycle_lock = threading.RLock()
        self._channels: dict[str, _ChannelState] = {}
        self._shutdown = False

    def subscribe(self, channel_id: str, source_url: str) -> FrameSubscription:
        channel_id = str(channel_id).strip()
        source_url = str(source_url).strip()
        if not channel_id:
            raise ValueError("channel_id is required")
        if not source_url:
            raise ValueError("source_url is required")

        with self._lifecycle_lock:
            cleanup = None
            with self._condition:
                if self._shutdown:
                    raise RuntimeError("frame broker is shut down")
                state = self._channels.get(channel_id)
                if state is None:
                    state = _ChannelState(
                        channel_id=channel_id,
                        source_url=source_url,
                        last_success_monotonic=self._monotonic(),
                    )
                    self._channels[channel_id] = state
                elif state.source_url != source_url:
                    cleanup = self._restart_source_locked(state, source_url)

            if cleanup is not None:
                timed_out = self._cleanup_handles(cleanup)
                if timed_out:
                    self._schedule_drain_watcher(
                        state, state.generation, cleanup.reader_thread
                    )
                else:
                    self._finish_cleanup(state, cleanup.reader_thread)

            with self._condition:
                if self._shutdown:
                    raise RuntimeError("frame broker is shut down")
                state = self._channels[channel_id]
                token = uuid.uuid4().hex
                subscription = FrameSubscription(self, channel_id, token)
                state.subscriptions[token] = subscription
                should_open = self._needs_capture_locked(state)
                generation = state.generation
                source = state.source_url

            if should_open:
                self._create_and_install_capture(state, generation, source)

            with self._condition:
                if self._shutdown:
                    subscription._closed = True
                    state.subscriptions.pop(token, None)
                    raise RuntimeError("frame broker is shut down")
                if self.start_reader:
                    self._start_reader_locked(state)
                return subscription

    def get_latest(self, channel_id: str, timeout: float | None = None) -> FrameEnvelope:
        channel_id = str(channel_id)
        deadline = None if timeout is None else self._monotonic() + max(0.0, timeout)
        result = None
        with self._condition:
            while True:
                if self._shutdown:
                    raise RuntimeError("frame broker is shut down")
                state = self._channels.get(channel_id)
                if state is not None and state.latest is not None:
                    result = state.latest
                    break
                remaining = self._remaining(deadline)
                if remaining == 0:
                    raise TimeoutError(f"no frame available for channel {channel_id}")
                self._condition.wait(remaining)
        return self._copy_envelope(result)

    def _get_for_subscription(
        self, subscription: FrameSubscription, timeout: float | None
    ) -> FrameEnvelope:
        deadline = None if timeout is None else self._monotonic() + max(0.0, timeout)
        result = None
        with self._condition:
            while True:
                if self._shutdown or subscription._closed:
                    raise RuntimeError("subscription is closed")
                state = self._channels.get(subscription.channel_id)
                if state is None or subscription.token not in state.subscriptions:
                    raise RuntimeError("subscription is closed")
                if state.latest is not None and state.latest.sequence > subscription._last_sequence:
                    subscription._last_sequence = state.latest.sequence
                    result = state.latest
                    break
                remaining = self._remaining(deadline)
                if remaining == 0:
                    raise TimeoutError(
                        f"no new frame available for channel {subscription.channel_id}"
                    )
                self._condition.wait(remaining)
        return self._copy_envelope(result)

    def publish(
        self, channel_id: str, frame: Any, timestamp: float | None = None
    ) -> FrameEnvelope:
        channel_id = str(channel_id)
        copied_frame = _copy_frame(frame)
        with self._condition:
            if self._shutdown:
                raise RuntimeError("frame broker is shut down")
            state = self._channels.get(channel_id)
            created_state = state is None
            if state is None:
                state = _ChannelState(channel_id=channel_id, source_url="")
                self._channels[channel_id] = state
            envelope = self._publish_locked(state, copied_frame, timestamp)
            if created_state and not state.subscriptions:
                self._channels.pop(channel_id, None)
        return self._copy_envelope(envelope)

    def get_status(self, channel_id: str) -> dict[str, Any]:
        channel_id = str(channel_id)
        with self._condition:
            state = self._channels.get(channel_id)
            if state is None:
                return {
                    "channel_id": channel_id,
                    "status": "offline",
                    "source_url": None,
                    "last_frame_timestamp": None,
                    "last_error": None,
                    "subscriber_count": 0,
                    "capture_count": 0,
                    "last_frame_age_seconds": None,
                    "capture_diagnostics": {},
                    "draining": False,
                }

            if (
                state.status == "online"
                and state.last_success_monotonic is not None
                and self._monotonic() - state.last_success_monotonic
                > self.stale_grace_seconds
            ):
                state.status = "offline"
            last_frame_age = None
            if state.last_frame_timestamp is not None:
                last_frame_age = max(self._clock() - state.last_frame_timestamp, 0.0)
            return {
                "channel_id": channel_id,
                "status": state.status,
                "source_url": state.source_url or None,
                "last_frame_timestamp": state.last_frame_timestamp,
                "last_error": state.last_error,
                "subscriber_count": len(state.subscriptions),
                "capture_count": state.capture_creations,
                "last_frame_age_seconds": last_frame_age,
                "capture_diagnostics": self._capture_diagnostics(state.capture),
                "draining": state.draining,
            }

    def unsubscribe(self, channel_id: str, token: str) -> None:
        with self._lifecycle_lock:
            cleanup = None
            with self._condition:
                state = self._channels.get(str(channel_id))
                if state is None:
                    return
                state.subscriptions.pop(token, None)
                if not state.subscriptions:
                    cleanup = self._close_state_locked(state)
                self._condition.notify_all()
            if cleanup is not None:
                timed_out = self._cleanup_handles(cleanup)
                if timed_out:
                    self._schedule_drain_watcher(
                        state, state.generation, cleanup.reader_thread
                    )
                else:
                    self._finish_cleanup(state, cleanup.reader_thread)

    def shutdown(self) -> None:
        with self._lifecycle_lock:
            cleanups = []
            with self._condition:
                if self._shutdown:
                    return
                self._shutdown = True
                for state in self._channels.values():
                    for subscription in state.subscriptions.values():
                        subscription._closed = True
                    state.subscriptions.clear()
                    cleanups.append(self._close_state_locked(state))
                self._channels.clear()
                self._condition.notify_all()
            for cleanup in cleanups:
                if self._cleanup_handles(cleanup):
                    logger.warning(
                        "electronic-seal frame reader remained active during shutdown"
                    )
                    self._schedule_shutdown_reader_watcher(cleanup.reader_thread)

    def capture_count(self, channel_id: str) -> int:
        with self._lock:
            state = self._channels.get(str(channel_id))
            return 0 if state is None else state.capture_creations

    def _needs_capture_locked(self, state: _ChannelState) -> bool:
        return bool(
            state.source_url
            and state.capture is None
            and not state.draining
            and (
                state.reader_thread is None
                or not state.reader_thread.is_alive()
            )
        )

    def _create_and_install_capture(
        self,
        state: _ChannelState,
        generation: int,
        source_url: str,
    ) -> Any:
        if not source_url:
            return None
        try:
            capture = self._create_capture(source_url)
        except Exception as exc:
            with self._condition:
                if (
                    not self._shutdown
                    and state.generation == generation
                    and state.source_url == source_url
                    and state.capture is None
                    and not state.draining
                ):
                    state.status = "offline"
                    state.last_error = str(exc)
                    state.next_reconnect_monotonic = (
                        self._monotonic() + self.reconnect_delay_seconds
                    )
            logger.warning(
                "electronic-seal frame capture failed channel=%s error=%s",
                state.channel_id,
                exc,
            )
            return None

        install = False
        with self._condition:
            if (
                not self._shutdown
                and state.generation == generation
                and state.source_url == source_url
                and state.capture is None
                and not state.draining
            ):
                state.capture = capture
                state.capture_creations += 1
                state.next_reconnect_monotonic = 0.0
                state.last_error = None
                install = True
        if not install:
            self._release_capture(capture)
            return None
        logger.info(
            "[ElectronicSealFrame] capture_created channel=%s count=%s diagnostics=%s",
            state.channel_id,
            state.capture_creations,
            self._capture_diagnostics(capture),
        )
        return capture

    @staticmethod
    def _capture_diagnostics(capture: Any) -> dict[str, Any]:
        diagnostics = getattr(capture, "diagnostics", None)
        if not callable(diagnostics):
            return {}
        try:
            result = diagnostics()
        except Exception as exc:
            return {"diagnostics_error": str(exc)}
        return dict(result) if isinstance(result, dict) else {}

    def _create_capture(self, source_url: str) -> Any:
        kwargs = {}
        try:
            parameters = inspect.signature(self.capture_factory).parameters
            accepts_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )
            if accepts_kwargs or "init_wait" in parameters:
                kwargs["init_wait"] = self.init_wait
            if accepts_kwargs or "latest_only" in parameters:
                kwargs["latest_only"] = True
        except (TypeError, ValueError):
            kwargs = {"init_wait": self.init_wait, "latest_only": True}
        return self.capture_factory(source_url, self.width, self.height, **kwargs)

    def _start_reader_locked(self, state: _ChannelState) -> None:
        if state.draining:
            return
        if state.reader_thread is not None and state.reader_thread.is_alive():
            return
        generation = state.generation
        stop_event = state.stop_event
        state.reader_thread = threading.Thread(
            target=self._reader_loop,
            args=(state, generation, stop_event),
            name=f"ElectronicSealFrameReader-{state.channel_id}",
            daemon=True,
        )
        state.reader_thread.start()

    def _reader_loop(
        self,
        state: _ChannelState,
        generation: int,
        stop_event: threading.Event,
    ) -> None:
        read_failures = 0
        last_failure_log_at = float("-inf")
        while not stop_event.is_set():
            capture = None
            should_open = False
            open_generation = generation
            source_url = ""
            with self._condition:
                if (
                    self._shutdown
                    or state.generation != generation
                    or stop_event.is_set()
                    or not state.subscriptions
                ):
                    return
                capture = state.capture
                if capture is None:
                    now = self._monotonic()
                    if (
                        not state.draining
                        and now >= state.next_reconnect_monotonic
                    ):
                        should_open = True
                        open_generation = state.generation
                        source_url = state.source_url
                    if capture is None:
                        wait_for = max(
                            0.01,
                            state.next_reconnect_monotonic - self._monotonic(),
                        )
                    else:
                        wait_for = 0.0
                else:
                    wait_for = 0.0

            if should_open:
                capture = self._create_and_install_capture(
                    state, open_generation, source_url
                )

            if capture is None:
                stop_event.wait(wait_for)
                continue

            try:
                ok, frame = capture.read(retry=self.read_retry)
            except Exception as exc:
                ok, frame = False, None
                with self._condition:
                    if (
                        not self._shutdown
                        and state.generation == generation
                        and state.capture is capture
                        and not stop_event.is_set()
                    ):
                        state.last_error = str(exc)

            if ok and frame is not None:
                if read_failures:
                    logger.info(
                        "[ElectronicSealFrame] capture_recovered channel=%s failures=%s diagnostics=%s",
                        state.channel_id,
                        read_failures,
                        self._capture_diagnostics(capture),
                    )
                read_failures = 0
                timestamp = getattr(capture, "last_frame_timestamp", None)
                copied_frame = _copy_frame(frame)
                with self._condition:
                    if (
                        self._shutdown
                        or state.generation != generation
                        or state.capture is not capture
                        or stop_event.is_set()
                        or not state.subscriptions
                    ):
                        return
                    self._publish_locked(
                        state,
                        copied_frame,
                        timestamp=self._clock() if timestamp is None else timestamp,
                    )
                continue

            read_failures += 1
            failure_log_at = self._monotonic()
            if failure_log_at - last_failure_log_at >= 5.0:
                last_failure_log_at = failure_log_at
                logger.warning(
                    "[ElectronicSealFrame] read_failed channel=%s failures=%s diagnostics=%s",
                    state.channel_id,
                    read_failures,
                    self._capture_diagnostics(capture),
                )
            stale_capture = None
            with self._condition:
                if (
                    self._shutdown
                    or state.generation != generation
                    or state.capture is not capture
                    or stop_event.is_set()
                    or not state.subscriptions
                ):
                    return
                now = self._monotonic()
                last_success = state.last_success_monotonic or now
                if now - last_success >= self.stale_grace_seconds:
                    state.status = "offline"
                    state.last_error = state.last_error or "frame read failed or stale"
                    stale_capture = self._detach_capture_locked(state, capture)
                    state.next_reconnect_monotonic = now + self.reconnect_delay_seconds
                    logger.warning(
                        "[ElectronicSealFrame] capture_stale channel=%s failures=%s stale_seconds=%.3f diagnostics=%s",
                        state.channel_id,
                        read_failures,
                        now - last_success,
                        self._capture_diagnostics(capture),
                    )
                else:
                    state.status = "connecting"
            if stale_capture is not None:
                self._release_capture(stale_capture)
            stop_event.wait(0.01)

    def _publish_locked(
        self, state: _ChannelState, frame: Any, timestamp: float | None
    ) -> FrameEnvelope:
        state.sequence += 1
        captured_at = self._clock() if timestamp is None else float(timestamp)
        envelope = FrameEnvelope(
            channel_id=state.channel_id,
            sequence=state.sequence,
            timestamp=captured_at,
            frame=frame,
        )
        state.latest = envelope
        state.last_frame_timestamp = captured_at
        state.last_success_monotonic = self._monotonic()
        state.status = "online"
        state.last_error = None
        self._condition.notify_all()
        return envelope

    def _restart_source_locked(
        self, state: _ChannelState, source_url: str
    ) -> _CleanupHandles:
        state.generation += 1
        state.stop_event.set()
        cleanup = _CleanupHandles(
            capture=state.capture,
            reader_thread=state.reader_thread,
        )
        state.capture = None
        state.reader_thread = (
            cleanup.reader_thread
            if cleanup.reader_thread is not None
            and cleanup.reader_thread.is_alive()
            else None
        )
        state.draining = state.reader_thread is not None
        state.stop_event = threading.Event()
        state.source_url = source_url
        state.latest = None
        state.last_frame_timestamp = None
        state.last_success_monotonic = self._monotonic()
        state.status = "offline"
        state.last_error = None
        return cleanup

    def _detach_capture_locked(
        self, state: _ChannelState, expected_capture: Any = None
    ) -> Any:
        if expected_capture is not None and state.capture is not expected_capture:
            return None
        capture = state.capture
        state.capture = None
        return capture

    def _close_state_locked(self, state: _ChannelState) -> _CleanupHandles:
        state.generation += 1
        state.stop_event.set()
        cleanup = _CleanupHandles(
            capture=state.capture,
            reader_thread=state.reader_thread,
        )
        state.capture = None
        state.latest = None
        state.last_frame_timestamp = None
        state.sequence = 0
        state.status = "offline"
        state.reader_thread = (
            cleanup.reader_thread
            if cleanup.reader_thread is not None
            and cleanup.reader_thread.is_alive()
            else None
        )
        state.draining = state.reader_thread is not None
        return cleanup

    def _cleanup_handles(self, cleanup: _CleanupHandles) -> bool:
        self._release_capture(cleanup.capture)
        reader_thread = cleanup.reader_thread
        if (
            reader_thread is None
            or reader_thread is threading.current_thread()
            or not reader_thread.is_alive()
        ):
            return False
        reader_thread.join(timeout=self.reader_join_timeout)
        if reader_thread.is_alive():
            logger.warning(
                "electronic-seal frame reader did not stop before timeout thread=%s",
                reader_thread.name,
            )
            return True
        return False

    def _finish_cleanup(
        self,
        state: _ChannelState,
        reader_thread: threading.Thread | None,
    ) -> None:
        with self._condition:
            if state.reader_thread is reader_thread:
                state.reader_thread = None
            state.draining = False
            state.drain_watcher_thread = None
            if not state.subscriptions and self._channels.get(state.channel_id) is state:
                self._channels.pop(state.channel_id, None)

    def _schedule_drain_watcher(
        self,
        state: _ChannelState,
        generation: int,
        reader_thread: threading.Thread | None,
    ) -> None:
        if reader_thread is None:
            self._finish_cleanup(state, None)
            return

        def wait_for_reader() -> None:
            reader_thread.join()
            with self._lifecycle_lock:
                with self._condition:
                    if (
                        self._shutdown
                        or state.generation != generation
                        or not state.draining
                    ):
                        return
                    state.reader_thread = None
                    state.drain_watcher_thread = None
                    state.draining = False
                    should_open = bool(state.subscriptions)
                    source_url = state.source_url
                    if not should_open and self._channels.get(state.channel_id) is state:
                        self._channels.pop(state.channel_id, None)
                        return
                if should_open:
                    self._create_and_install_capture(state, generation, source_url)
                with self._condition:
                    if not self._shutdown and state.generation == generation:
                        self._start_reader_locked(state)

        watcher = threading.Thread(
            target=wait_for_reader,
            name=f"ElectronicSealDrainWatcher-{state.channel_id}",
            daemon=True,
        )
        with self._condition:
            if self._shutdown or state.generation != generation or not state.draining:
                return
            state.drain_watcher_thread = watcher
        watcher.start()

    @staticmethod
    def _schedule_shutdown_reader_watcher(
        reader_thread: threading.Thread | None,
    ) -> None:
        if reader_thread is None or reader_thread is threading.current_thread():
            return

        def wait_for_reader() -> None:
            reader_thread.join()
            logger.info(
                "electronic-seal frame reader exited after shutdown thread=%s",
                reader_thread.name,
            )

        threading.Thread(
            target=wait_for_reader,
            name=f"ElectronicSealShutdownWatcher-{reader_thread.name}",
            daemon=True,
        ).start()

    @staticmethod
    def _release_capture(capture: Any) -> None:
        if capture is None:
            return
        try:
            capture.release()
        except Exception:
            logger.debug("capture release failed", exc_info=True)

    def _copy_envelope(self, envelope: FrameEnvelope) -> FrameEnvelope:
        return FrameEnvelope(
            channel_id=envelope.channel_id,
            sequence=envelope.sequence,
            timestamp=envelope.timestamp,
            frame=_copy_frame(envelope.frame),
        )

    def _remaining(self, deadline: float | None) -> float | None:
        if deadline is None:
            return None
        return max(0.0, deadline - self._monotonic())


__all__ = ["FrameBroker", "FrameEnvelope", "FrameSubscription"]
