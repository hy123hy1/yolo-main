from __future__ import annotations

from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
import logging
import math
import threading
from typing import Any

from src.electronic_seal.evidence import EvidenceService
from src.electronic_seal.evidence_buffer import EvidenceBuffer


logger = logging.getLogger("uvicorn.error")
VIDEO_ALGORITHMS = frozenset({"room_empty", "camera_occlusion", "camera_view_shift"})


@dataclass(frozen=True)
class _PendingVideo:
    event_id: str
    algorithm_type: str
    trigger_timestamp: float
    before_seconds: float
    after_seconds: float
    annotations: dict[str, Any]


class EvidenceVideoCoordinator:
    """Build one asynchronous before/after video for each new alarm event."""

    def __init__(
        self,
        *,
        channel_id: str,
        store: Any,
        evidence_service: EvidenceService,
        before_seconds: float = 10.0,
        after_seconds: float = 10.0,
        fps: float = 25.0,
        buffer_fps: float = 30.0,
        buffer_max_bytes: int = 256 * 1024 * 1024,
        frame_quality: int = 75,
        executor: Executor | None = None,
    ) -> None:
        self.channel_id = str(channel_id)
        self.store = store
        self.evidence_service = evidence_service
        self.before_seconds = max(float(before_seconds), 0.0)
        self.after_seconds = max(float(after_seconds), 0.0)
        self.fps = max(float(fps), 1.0)
        self.frame_quality = min(max(int(frame_quality), 10), 95)
        buffer_seconds = self.before_seconds + self.after_seconds + 2.0
        capacity = max(
            int(math.ceil(self.fps * buffer_seconds)),
            int(math.ceil(buffer_fps * buffer_seconds)),
            64,
        )
        self.buffer = EvidenceBuffer(
            self.channel_id,
            capacity=capacity,
            max_bytes=max(int(buffer_max_bytes), 1),
        )
        self._executor = executor or ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"electronic-seal-video-{self.channel_id}",
        )
        self._owns_executor = executor is None
        self._pending: dict[str, _PendingVideo] = {}
        self._lock = threading.RLock()
        self._closed = False

    def append_frame(self, frame: Any, timestamp: float) -> None:
        packed = self.evidence_service.encode_frame(frame, self.frame_quality)
        self.buffer.append(packed, float(timestamp))
        self._flush_ready(float(timestamp))

    def schedule(
        self,
        event: dict[str, Any],
        config: dict[str, Any],
        timestamp: float,
        annotations: dict[str, Any] | None = None,
    ) -> bool:
        algorithm_type = str(event.get("algorithm_type") or config.get("algorithm_type") or "")
        event_id = str(event.get("id") or "")
        if algorithm_type not in VIDEO_ALGORITHMS or not event_id:
            return False
        if not bool(config.get("need_video_clip", False)):
            return False
        job = _PendingVideo(
            event_id=event_id,
            algorithm_type=algorithm_type,
            trigger_timestamp=float(timestamp),
            before_seconds=self.before_seconds,
            after_seconds=self.after_seconds,
            annotations=dict(annotations or {}),
        )
        with self._lock:
            if self._closed or event_id in self._pending:
                return False
            self._pending[event_id] = job
        if job.after_seconds <= 0:
            self._flush_ready(float(timestamp), force=True)
        return True

    def flush(self) -> None:
        self._flush_ready(float("inf"), force=True)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.flush()
        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=False)

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def _flush_ready(self, timestamp: float, *, force: bool = False) -> None:
        ready: list[tuple[_PendingVideo, Any]] = []
        with self._lock:
            for event_id, job in list(self._pending.items()):
                if not force and timestamp < job.trigger_timestamp + job.after_seconds:
                    continue
                clip = self.buffer.build_clip(
                    job.trigger_timestamp - job.before_seconds,
                    job.trigger_timestamp + job.after_seconds,
                )
                ready.append((job, clip))
                del self._pending[event_id]
        for job, clip in ready:
            if not clip.frames:
                logger.warning(
                    "[ElectronicSeal] skip empty evidence video channel_id=%s event_id=%s algorithm=%s",
                    self.channel_id,
                    job.event_id,
                    job.algorithm_type,
                )
                continue
            self._executor.submit(self._write_video, job=job, clip=clip)

    def _write_video(self, *, job: _PendingVideo, clip) -> None:
        try:
            overlay_callback = None
            if job.annotations:
                overlay_callback = lambda frame, _timestamp: self.evidence_service.overlay_frame(
                    frame,
                    job.annotations,
                )
            video_path = self.evidence_service.write_clip(
                channel_id=self.channel_id,
                event_id=job.event_id,
                clip=clip,
                fps=self.fps,
                event_timestamp=job.trigger_timestamp,
                overlay_callback=overlay_callback,
                preserve_duration=True,
            )
            updater = getattr(self.store, "update_event", None)
            if callable(updater):
                updater(job.event_id, {"evidence_video_path": video_path})
        except Exception:
            logger.exception(
                "[ElectronicSeal] evidence video failed channel_id=%s event_id=%s algorithm=%s",
                self.channel_id,
                job.event_id,
                job.algorithm_type,
            )


__all__ = ["EvidenceVideoCoordinator", "VIDEO_ALGORITHMS"]
