"""On-demand evidence from an already running analysis task (no extra capture)."""
import asyncio
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime
import logging
import math
from pathlib import Path
import threading
import time
from uuid import uuid4

from sqlalchemy import select

from src.alarm_push.media import alarm_video_path, public_media_url
from src.alarm_push.service import enqueue_event
from src.core.settings import settings
from src.db.database import SessionLocal
from src.db.models import AlarmPushTarget, EventMeta, RecordingMeta


logger = logging.getLogger(__name__)
WINDOW_SECONDS = 10.0
MAX_REQUEST_DELAY = 10.0
MAX_FRAME_GAP = 2.0
OUTPUT_FPS = 25


class VideoCaptureError(ValueError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code


@dataclass
class _VideoJob:
    id: str
    task_id: str
    channel_id: str
    timestamp: float
    frames: list
    committed: bool = False
    ready: bool = False
    failure: str | None = None
    timer: object = None

    @property
    def start(self):
        return self.timestamp - WINDOW_SECONDS

    @property
    def end(self):
        return self.timestamp + WINDOW_SECONDS


@dataclass
class _TaskBuffer:
    task_id: str
    channel_id: str
    frames: object
    stop_event: threading.Event
    jobs: dict = field(default_factory=dict)
    closed: bool = False


class ExternalVideoCaptureService:
    def __init__(self, analysis):
        self.analysis = analysis
        self._lock = threading.RLock()
        self._buffers: dict[str, _TaskBuffer] = {}
        self._active_jobs: dict[str, _VideoJob] = {}

    def register(self, task_id, channel_id, frames, stop_event):
        buffer = _TaskBuffer(task_id, str(channel_id or ""), frames, stop_event)
        with self._lock:
            self._buffers[task_id] = buffer
        return buffer

    def append(self, buffer, frame):
        """Called by the task worker with its existing JPEG-packed frame."""
        finished = []
        with self._lock:
            if buffer.closed:
                return
            previous = buffer.frames[-1][0] if buffer.frames else None
            if previous is not None and frame[0] <= previous:
                return
            buffer.frames.append(frame)
            for job in list(buffer.jobs.values()):
                if job.ready or job.failure:
                    continue
                if previous is not None and frame[0] - previous > MAX_FRAME_GAP:
                    job.failure = "Camera frame gap exceeded 2 seconds"
                elif frame[0] >= job.end:
                    job.ready = True
                elif frame[0] >= job.start:
                    job.frames.append(frame)
                    if len(job.frames) > 4000:
                        job.failure = "Video frame limit exceeded"
                if job.committed and (job.failure or job.ready):
                    buffer.jobs.pop(job.id, None)
                    finished.append(job)
        self._finish(finished)

    def reset(self, buffer):
        """Do not join frames across a change to the task's source."""
        self._cancel(buffer, "Task video source changed", close=False)

    def close(self, buffer):
        self._cancel(buffer, "Detection task stopped before video was complete", close=True)

    def _cancel(self, buffer, reason, *, close):
        finished = []
        with self._lock:
            if not close:
                buffer.frames.clear()
            if close:
                buffer.closed = True
                if self._buffers.get(buffer.task_id) is buffer:
                    self._buffers.pop(buffer.task_id, None)
            for job in list(buffer.jobs.values()):
                job.failure = reason
                if job.committed:
                    buffer.jobs.pop(job.id, None)
                    finished.append(job)
        self._finish(finished)

    async def capture(self, channel_id: str, timestamp: float, task_id: str | None = None):
        return await asyncio.to_thread(self._capture, channel_id, timestamp, task_id)

    def _capture(self, channel_id, timestamp, task_id):
        now = time.time()
        if not math.isfinite(timestamp) or timestamp <= 0:
            raise VideoCaptureError(422, "timestamp must be a positive Unix timestamp")
        if timestamp > now + 1 or timestamp < now - MAX_REQUEST_DELAY:
            raise VideoCaptureError(422, "timestamp must be within the last 10 seconds (1 second future tolerance)")
        if not settings.METADATA_DB_ENABLED:
            raise VideoCaptureError(503, "Video capture requires the metadata database and alarm push")
        # Reserve first, so incoming frames are retained during the DB transaction.
        with self._lock:
            candidates = [buffer for buffer in self._buffers.values()
                          if buffer.channel_id == channel_id and not buffer.closed
                          and not buffer.stop_event.is_set()
                          and (task_id is None or buffer.task_id == task_id)]
            if not candidates:
                raise VideoCaptureError(409, "Camera has no running detection task")
            start = timestamp - WINDOW_SECONDS
            usable = []
            for buffer in candidates:
                frames = list(buffer.frames)
                if not frames or frames[0][0] > start + 0.1 or frames[-1][0] < now - MAX_FRAME_GAP:
                    continue
                selected = [frame for frame in frames if start <= frame[0] < timestamp + WINDOW_SECONDS]
                if not selected or selected[0][0] > start + MAX_FRAME_GAP:
                    continue
                if any(b[0] - a[0] > MAX_FRAME_GAP for a, b in zip(selected, selected[1:])):
                    continue
                if frames[-1][0] >= timestamp + WINDOW_SECONDS and (
                    timestamp + WINDOW_SECONDS - selected[-1][0] > MAX_FRAME_GAP
                ):
                    continue
                usable.append((buffer, selected))
            if not usable:
                raise VideoCaptureError(409, "Camera needs a continuous 10-second prebuffer and fresh frames")
            buffer, frames = max(usable, key=lambda item: item[0].frames[-1][0])
            task_jobs = sum(job.task_id == buffer.task_id for job in self._active_jobs.values())
            if task_jobs >= 8 or len(self._active_jobs) >= 32:
                raise VideoCaptureError(429, "Too many pending video capture requests")
            job = _VideoJob(f"evt_{uuid4().hex}", buffer.task_id, channel_id, timestamp, frames,
                            ready=buffer.frames[-1][0] >= timestamp + WINDOW_SECONDS)
            buffer.jobs[job.id] = job
            self._active_jobs[job.id] = job
        try:
            self._persist(job)
        except Exception as exc:
            with self._lock:
                buffer.jobs.pop(job.id, None)
                self._active_jobs.pop(job.id, None)
            if isinstance(exc, VideoCaptureError):
                raise
            logger.exception("Failed to persist external video request %s", job.id)
            raise VideoCaptureError(503, "Failed to persist video request; nothing was queued") from exc
        with self._lock:
            job.committed = True
            finished = bool(job.failure or job.ready)
            if finished:
                buffer.jobs.pop(job.id, None)
            else:
                job.timer = threading.Timer(max(job.end - time.time(), 0) + 5,
                                            self._expire, args=(buffer, job.id))
                job.timer.daemon = True
                job.timer.start()
        if finished:
            self._finish([job])
        return {
            "id": job.id, "recording_id": job.id, "channel_id": channel_id,
            "task_id": job.task_id, "timestamp": timestamp,
            "start_timestamp": job.start, "end_timestamp": job.end,
            "status": "failed" if job.failure else "pending",
            "video_path": public_media_url(str(alarm_video_path(job.id))),
        }

    def _persist(self, job):
        created_at = datetime.fromtimestamp(job.timestamp)
        now = datetime.now()
        item = {
            "id": job.id, "channel_id": job.channel_id, "task_id": job.task_id,
            "algorithm_id": None, "model_id": None, "event_type": "external_video",
            "class_name": None, "conf": None, "snapshot_path": None, "video_path": None,
            "content": f"外部视频取证：{job.channel_id}，{created_at.isoformat()}，前后各10秒",
            "handling_status": 0, "remark": None, "status": "new",
            "created_at": created_at.isoformat(), "updated_at": now.isoformat(),
        }
        raw = {"trigger_timestamp": job.timestamp, "start_timestamp": job.start,
               "end_timestamp": job.end, "event_type": "external_video",
               "event_type_label": "external_video", "event_id": job.id}
        with SessionLocal() as session:
            if session.scalar(select(AlarmPushTarget.id).where(AlarmPushTarget.enabled.is_(True))) is None:
                raise VideoCaptureError(503, "No enabled alarm push receiver is configured")
            event = dict(item, created_at=created_at, updated_at=now)
            session.add(EventMeta(**event, raw_json={**item, **raw}))
            session.add(RecordingMeta(
                id=job.id, channel_id=job.channel_id, task_id=job.task_id,
                started_at=datetime.fromtimestamp(job.start), ended_at=datetime.fromtimestamp(job.end),
                duration=20, status="pending", created_at=now, updated_at=now, raw_json=raw,
            ))
            enqueue_event(session, item, need_video_clip=True)
            session.commit()

    def _expire(self, buffer, job_id):
        with self._lock:
            job = buffer.jobs.pop(job_id, None)
            if job:
                job.failure = "Timed out waiting for camera frames"
        if job:
            self._finish([job])

    def _finish(self, jobs):
        for job in jobs:
            if job.timer:
                job.timer.cancel()
            if job.failure:
                self._mark_failed(job, job.failure)
                continue
            try:
                future = self.analysis._alarm_video_executor.submit(self._encode, job)
                self.analysis._track_alarm_video_future(future)
            except Exception as exc:
                self._mark_failed(job, f"Could not schedule video encoding: {exc}")

    def _mark_failed(self, job, reason):
        logger.warning("External video failed event=%s reason=%s", job.id, reason)
        try:
            with SessionLocal() as session:
                row = session.get(RecordingMeta, job.id)
                if row:
                    row.status = "failed"
                    row.updated_at = datetime.now()
                    row.raw_json = {**(row.raw_json or {}), "last_error": reason}
                    session.commit()
        except Exception:
            logger.exception("Could not persist external video failure %s", job.id)
        finally:
            with self._lock:
                self._active_jobs.pop(job.id, None)

    def recover_interrupted(self):
        """Run once at startup, before workers accept requests (single API process)."""
        with SessionLocal() as session:
            rows = session.scalars(select(RecordingMeta).join(
                EventMeta, EventMeta.id == RecordingMeta.id,
            ).where(
                EventMeta.event_type == "external_video",
                RecordingMeta.status.in_(("pending", "encoding")),
            )).all()
            for row in rows:
                row.status = "failed"
                row.updated_at = datetime.now()
                row.raw_json = {**(row.raw_json or {}),
                                "last_error": "Service restarted before video generation completed"}
            session.commit()
            return {"failed": len(rows)}

    def _encode(self, job):
        try:
            with SessionLocal() as session:
                row = session.get(RecordingMeta, job.id)
                if not row:
                    raise RuntimeError("Video recording was removed")
                row.status = "encoding"
                row.updated_at = datetime.now()
                session.commit()
            timestamps = [frame[0] for frame in job.frames]
            # Time-based resampling keeps a sparse live source at the real 20s duration.
            frames = []
            for index in range(20 * OUTPUT_FPS):
                timestamp = job.start + index / OUTPUT_FPS
                source_index = max(bisect_right(timestamps, timestamp) - 1, 0)
                frames.append((timestamp, job.frames[source_index][1]))
            path = self.analysis._save_alarm_video(
                channel_id=job.channel_id, event_id=job.id, frames=frames, detections=[],
                fps=OUTPUT_FPS, event_timestamp=job.timestamp, duration_seconds=20,
                sanitize_frames=False,  # Decode one frame at a time; preserve the original imagery.
            )
            if not path or not Path(path).is_file() or Path(path).stat().st_size == 0:
                raise RuntimeError("Video encoding did not produce a file")
            with SessionLocal() as session:
                recording = session.get(RecordingMeta, job.id)
                event = session.get(EventMeta, job.id)
                if not recording or not event:
                    raise RuntimeError("Video request was removed during encoding")
                recording.file_path = path
                recording.status = "ready"
                recording.updated_at = datetime.now()
                recording.raw_json = {**(recording.raw_json or {}), "video_path": path}
                event.video_path = path
                event.updated_at = datetime.now()
                event.raw_json = {**(event.raw_json or {}), "video_path": path}
                session.commit()  # Video backfill does not enqueue another notification.
        except Exception as exc:
            logger.exception("External video encoding failed %s", job.id)
            self._mark_failed(job, str(exc))
        finally:
            with self._lock:
                self._active_jobs.pop(job.id, None)
