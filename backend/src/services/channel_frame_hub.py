from __future__ import annotations

import json
import logging
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from src.core.settings import settings
from src.services.zone_preview_service import FFmpegCapture


logger = logging.getLogger("uvicorn.error")


@dataclass(slots=True)
class FrameEnvelope:
    channel_id: str
    sequence: int
    captured_at: float
    image: np.ndarray


class ChannelFrameSubscription:
    def __init__(self, channel_id: str, token: str, unsubscribe: Callable[[str, str], None]):
        self.channel_id = channel_id
        self.token = token
        self._queue: queue.Queue[FrameEnvelope] = queue.Queue(maxsize=1)
        self._unsubscribe = unsubscribe
        self._closed = False

    def get_latest(self, timeout: float | None = None) -> FrameEnvelope:
        return self._queue.get(timeout=timeout)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._unsubscribe(self.channel_id, self.token)

    def _offer(self, frame: FrameEnvelope) -> None:
        if self._closed:
            return
        try:
            self._queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._queue.put_nowait(frame)
        except queue.Full:
            pass


class ChannelFrameHub:
    def __init__(
        self,
        *,
        capture_factory: Callable[[str, int, int], object] | None = None,
        probe_factory: Callable[[str], tuple[int, int]] | None = None,
        start_reader: bool = True,
        reconnect_delay_seconds: float = 1.0,
        offline_grace_seconds: float | None = None,
    ):
        self._capture_factory = capture_factory or self._create_capture
        self._probe_factory = probe_factory or self._probe_stream
        self._start_reader = bool(start_reader)
        self._reconnect_delay_seconds = max(float(reconnect_delay_seconds), 0.1)
        configured_grace = (
            offline_grace_seconds
            if offline_grace_seconds is not None
            else getattr(settings, "CAMERA_HEALTH_FRAME_STALE_SECONDS", 5.0)
        )
        self._offline_grace_seconds = max(float(configured_grace or 0.0), 0.0)
        self._lock = threading.RLock()
        self._entries: dict[str, dict] = {}
        self._token_counter = 0

    @staticmethod
    def _probe_stream(source_url: str) -> tuple[int, int]:
        ffmpeg_path = Path(str(getattr(settings, "FFMPEG_BIN", "ffmpeg") or "ffmpeg"))
        ffprobe_name = "ffprobe.exe" if ffmpeg_path.suffix.lower() == ".exe" else "ffprobe"
        command = [str(ffmpeg_path.with_name(ffprobe_name)), "-v", "error"]
        if source_url.lower().startswith("rtsp://"):
            command.extend(["-rtsp_transport", "tcp", "-rw_timeout", "5000000"])
        command.extend([
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            source_url,
        ])
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        if result.returncode != 0:
            raise ValueError(f"failed to probe channel source: {(result.stderr or '').strip()}")
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams") or []
        if not streams:
            raise ValueError("channel source has no video stream")
        width = int(streams[0].get("width") or 0)
        height = int(streams[0].get("height") or 0)
        if width <= 0 or height <= 0:
            raise ValueError("channel source returned invalid video dimensions")
        return width, height

    @staticmethod
    def _create_capture(source_url: str, width: int, height: int) -> FFmpegCapture:
        return FFmpegCapture(source_url, width, height, init_wait=0.3, latest_only=True)

    def _new_token(self) -> str:
        self._token_counter += 1
        return f"health-sub-{self._token_counter}"

    def subscribe(self, channel_id: str, source_url: str) -> ChannelFrameSubscription:
        normalized_channel_id = str(channel_id or "").strip()
        normalized_source_url = str(source_url or "").strip()
        if not normalized_channel_id:
            raise ValueError("channel_id is required")
        if not normalized_source_url:
            raise ValueError("source_url is required")

        with self._lock:
            entry = self._entries.get(normalized_channel_id)
            if entry is None or entry["source_url"] != normalized_source_url:
                if entry is not None:
                    self._stop_entry_locked(normalized_channel_id, entry)
                width, height = self._probe_factory(normalized_source_url)
                capture = self._capture_factory(normalized_source_url, width, height)
                entry = {
                    "channel_id": normalized_channel_id,
                    "source_url": normalized_source_url,
                    "capture": capture,
                    "subscriptions": {},
                    "stop_event": threading.Event(),
                    "sequence": 0,
                    "status": "starting",
                    "last_frame_at": None,
                    "read_failure_since": None,
                    "reader": None,
                }
                self._entries[normalized_channel_id] = entry
                if self._start_reader:
                    reader = threading.Thread(
                        target=self._read_loop,
                        args=(normalized_channel_id, entry),
                        daemon=True,
                        name=f"CameraHealthFrame-{normalized_channel_id}",
                    )
                    entry["reader"] = reader
                    reader.start()

            token = self._new_token()
            subscription = ChannelFrameSubscription(normalized_channel_id, token, self.unsubscribe)
            entry["subscriptions"][token] = subscription
            return subscription

    def unsubscribe(self, channel_id: str, token: str) -> None:
        with self._lock:
            entry = self._entries.get(channel_id)
            if entry is None:
                return
            entry["subscriptions"].pop(token, None)
            if not entry["subscriptions"]:
                self._stop_entry_locked(channel_id, entry)
                self._entries.pop(channel_id, None)

    def publish(self, channel_id: str, image: np.ndarray, *, captured_at: float | None = None) -> None:
        with self._lock:
            entry = self._entries.get(channel_id)
            if entry is None:
                return
            entry["sequence"] += 1
            entry["status"] = "online"
            entry["last_frame_at"] = float(captured_at or time.time())
            entry["read_failure_since"] = None
            envelope = FrameEnvelope(
                channel_id=channel_id,
                sequence=entry["sequence"],
                captured_at=entry["last_frame_at"],
                image=image.copy(),
            )
            subscriptions = list(entry["subscriptions"].values())
        for subscription in subscriptions:
            subscription._offer(envelope)

    def _read_loop(self, channel_id: str, entry: dict) -> None:
        retry_delay = self._reconnect_delay_seconds
        while not entry["stop_event"].is_set():
            capture = entry["capture"]
            try:
                ok, frame = capture.read(retry=3)
            except Exception as exc:
                ok, frame = False, None
                logger.warning("[CameraHealthFrame] channel_id=%s read_error=%s", channel_id, exc)
            if ok and frame is not None:
                self.publish(channel_id, frame, captured_at=time.time())
                retry_delay = self._reconnect_delay_seconds
                continue

            with self._lock:
                if self._entries.get(channel_id) is entry:
                    failure_since = entry.get("read_failure_since")
                    if failure_since is None:
                        failure_since = time.monotonic()
                        entry["read_failure_since"] = failure_since
                    stale_seconds = time.monotonic() - failure_since
                    if stale_seconds < self._offline_grace_seconds:
                        should_reconnect = False
                    else:
                        entry["status"] = "offline"
                        should_reconnect = True
                else:
                    should_reconnect = False
            if not should_reconnect:
                if entry["stop_event"].wait(timeout=min(self._reconnect_delay_seconds, 0.1)):
                    break
                continue
            try:
                capture.release()
            except Exception:
                pass
            if entry["stop_event"].wait(timeout=retry_delay):
                break
            retry_delay = min(retry_delay * 2, 5.0)
            try:
                width, height = self._probe_factory(entry["source_url"])
                replacement = self._capture_factory(entry["source_url"], width, height)
            except Exception as exc:
                logger.warning(
                    "[CameraHealthFrame] channel_id=%s reconnect_error=%s",
                    channel_id,
                    exc,
                )
                continue
            with self._lock:
                if self._entries.get(channel_id) is not entry or entry["stop_event"].is_set():
                    replacement.release()
                    break
                entry["capture"] = replacement
                entry["status"] = "starting"

    def _stop_entry_locked(self, channel_id: str, entry: dict) -> None:
        entry["stop_event"].set()
        capture = entry.get("capture")
        if capture is not None:
            try:
                capture.release()
            except Exception:
                pass
        reader = entry.get("reader")
        if reader is not None and reader.is_alive() and reader is not threading.current_thread():
            reader.join(timeout=1.0)
        logger.info("[CameraHealthFrame] channel_id=%s stopped", channel_id)

    def get_status(self, channel_id: str) -> dict:
        with self._lock:
            entry = self._entries.get(channel_id)
            if entry is None:
                return {"channel_id": channel_id, "status": "stopped", "sequence": 0}
            return {
                "channel_id": channel_id,
                "source_url": entry["source_url"],
                "status": entry["status"],
                "sequence": entry["sequence"],
                "last_frame_at": entry["last_frame_at"],
                "subscriber_count": len(entry["subscriptions"]),
            }

    def capture_count(self, channel_id: str) -> int:
        with self._lock:
            return 1 if channel_id in self._entries else 0

    def shutdown(self) -> None:
        with self._lock:
            entries = list(self._entries.items())
            self._entries.clear()
            for channel_id, entry in entries:
                self._stop_entry_locked(channel_id, entry)


channel_frame_hub = ChannelFrameHub()
