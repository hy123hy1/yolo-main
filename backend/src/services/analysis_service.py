import asyncio
import gc
import json
import logging
import os
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from src.core.algorithm_rule_config import get_algorithm_rule_config
from src.core.settings import settings
from src.db.database import SessionLocal
from src.db.models import AlgorithmRegionMeta, ChannelMeta, ModelMetaRecord, TaskAlgorithmMeta, TaskAlgorithmRegionMeta, TaskMeta, TaskRegionMeta
from src.services.event_service import EventService
from src.services.metric_service import metric_service
from src.services.recording_service import RecordingService
from src.services.platform_storage import format_datetime, load_json, now_iso, save_json
from src.services.zone_preview_service import FFmpegCapture


logger = logging.getLogger(__name__)

DETECTION_RULE_TYPES = {
    "detection",
    "intrusion",
    "smoke_alarm",
    "fire_alarm",
    "helmet_detection",
    "sleep_on_duty",
    "mining_truck_detect",
}

TASK_ANALYSIS_READ_FAIL_LIMIT = 10


class AnalysisService:
    def __init__(self):
        self.channels_dir: Path = settings.CHANNELS_DIR
        self.tasks_dir: Path = settings.TASKS_DIR
        self.events_service = EventService()
        self.recording_service = RecordingService()
        self._lock = threading.Lock()
        self._model_cache_lock = threading.Lock()
        self._workers: dict[str, dict] = {}
        self._model_cache: dict[str, tuple[object, dict]] = {}
        self._task_workers: dict[str, dict] = {}
        self._alarm_video_futures: set[Future] = set()
        self._task_inference_executor = ThreadPoolExecutor(
            max_workers=self._task_inference_worker_count(),
            thread_name_prefix="task-infer",
        )
        self._alarm_video_executor = ThreadPoolExecutor(
            max_workers=self._alarm_video_worker_count(),
            thread_name_prefix="alarm-video",
        )

    def _alarm_video_before_seconds(self) -> int:
        return max(int(getattr(settings, "ALARM_VIDEO_BEFORE_SECONDS", 10) or 10), 0)

    def _alarm_video_after_seconds(self) -> int:
        return max(int(getattr(settings, "ALARM_VIDEO_AFTER_SECONDS", 10) or 10), 0)

    def _alarm_video_total_seconds(self) -> int:
        return max(self._alarm_video_before_seconds() + self._alarm_video_after_seconds(), 0)

    def _alarm_video_frame_limit(self, fps: float = 25.0) -> int:
        safe_fps = max(float(fps or 25.0), 1.0)
        return max(int(round(safe_fps * self._alarm_video_total_seconds())), 1)

    def _alarm_video_pre_event_buffer_size(self, fps: float = 25.0) -> int:
        return self._alarm_video_frame_limit(fps)

    def _alarm_video_buffer_jpeg_quality(self) -> int:
        return min(max(int(getattr(settings, "ALARM_VIDEO_BUFFER_JPEG_QUALITY", 75) or 75), 10), 95)

    def _task_analysis_startup_warmup_seconds(self) -> float:
        return max(float(getattr(settings, "TASK_ANALYSIS_STARTUP_WARMUP_SECONDS", 5.0) or 0.0), 0.0)

    def _task_inference_worker_count(self) -> int:
        configured = int(getattr(settings, "TASK_ANALYSIS_ALGORITHM_MAX_CONCURRENCY", 0) or 0)
        if configured > 0:
            return max(configured, 1)
        return max(min(os.cpu_count() or 2, 4), 2)

    def _alarm_video_worker_count(self) -> int:
        configured = int(getattr(settings, "ALARM_VIDEO_JOB_MAX_WORKERS", 0) or 0)
        if configured > 0:
            return max(configured, 1)
        return 2

    def _task_alarm_video_read_fail_limit(self) -> int:
        return max(int(getattr(settings, "TASK_ALARM_VIDEO_READ_FAIL_LIMIT", 20) or 20), 1)

    def _task_alarm_video_capture_timeout_seconds(self, after_seconds: int) -> float:
        configured = float(getattr(settings, "TASK_ALARM_VIDEO_CAPTURE_TIMEOUT_SECONDS", 0.0) or 0.0)
        if configured > 0:
            return configured
        return max(float(after_seconds) + 5.0, 5.0)

    def _pack_alarm_video_frame(self, frame) -> bytes | Any:
        ok, buffer = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, self._alarm_video_buffer_jpeg_quality()],
        )
        if ok:
            return buffer.tobytes()
        return frame.copy()

    def _decode_alarm_video_frame(self, frame):
        if isinstance(frame, (bytes, bytearray, memoryview)):
            encoded = np.frombuffer(frame, dtype=np.uint8)
            decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
            if decoded is None:
                raise ValueError("Failed to decode compressed alarm video frame")
            return decoded
        return frame

    def _is_frame_usable(self, frame) -> bool:
        try:
            decoded = self._decode_alarm_video_frame(frame)
        except Exception:
            return False
        if decoded is None or not hasattr(decoded, "shape"):
            return False
        if len(decoded.shape) < 2 or decoded.shape[0] <= 0 or decoded.shape[1] <= 0:
            return False
        if len(decoded.shape) == 2:
            gray = decoded
        else:
            gray = cv2.cvtColor(decoded, cv2.COLOR_BGR2GRAY)
        stddev = float(np.std(gray))
        dynamic_range = int(gray.max()) - int(gray.min())
        mean_value = float(np.mean(gray))
        near_mean_ratio = float(np.mean(np.abs(gray.astype(np.float32) - mean_value) <= 4.0))
        if stddev < 6.0 and dynamic_range < 24:
            return False
        if stddev < 8.0 and dynamic_range < 32 and near_mean_ratio > 0.98:
            return False
        return True

    def _latest_usable_frame(self, frames: list[tuple[float, object]] | deque[tuple[float, object]] | None):
        for _, frame in reversed(list(frames or [])):
            if self._is_frame_usable(frame):
                return self._decode_alarm_video_frame(frame).copy()
        return None

    def _select_usable_frame(self, frame, fallback_frame=None):
        if self._is_frame_usable(frame):
            return self._decode_alarm_video_frame(frame).copy()
        if fallback_frame is not None and self._is_frame_usable(fallback_frame):
            return self._decode_alarm_video_frame(fallback_frame).copy()
        return self._decode_alarm_video_frame(frame)

    def _sanitize_alarm_video_frames(self, frames: list[tuple[float, object]]) -> list[tuple[float, object]]:
        decoded_frames = [
            (ts_value, self._decode_alarm_video_frame(frame))
            for ts_value, frame in list(frames or [])
        ]
        first_good_frame = next(
            (decoded.copy() for _, decoded in decoded_frames if self._is_frame_usable(decoded)),
            None,
        )
        sanitized: list[tuple[float, object]] = []
        last_good_frame = first_good_frame.copy() if first_good_frame is not None else None
        for ts_value, decoded in decoded_frames:
            if self._is_frame_usable(decoded):
                last_good_frame = decoded.copy()
                sanitized.append((ts_value, last_good_frame.copy()))
                continue
            replacement = last_good_frame.copy() if last_good_frame is not None else decoded.copy()
            sanitized.append((ts_value, replacement))
        return sanitized

    def _get_channel_path(self, channel_id: str) -> Path:
        return self.channels_dir / f"{channel_id}.json"

    def _load_channel(self, channel_id: str) -> dict | None:
        if settings.METADATA_DB_ENABLED:
            with SessionLocal() as session:
                row = session.query(ChannelMeta).filter(ChannelMeta.id == channel_id).one_or_none()
                if not row:
                    return None
                payload = dict(row.raw_json or {})
                payload.setdefault("id", row.id)
                payload["device_id"] = row.device_id
                payload["media_node_id"] = row.media_node_id
                payload["name"] = row.name
                payload["type"] = row.type
                payload["source_url"] = row.source_url
                payload["app"] = row.app
                payload["stream"] = row.stream
                payload["is_online"] = row.is_online
                payload["is_playing"] = row.is_playing
                payload["ai_enabled"] = row.ai_enabled
                payload["record_mode"] = row.record_mode
                payload["snapshot_url"] = row.snapshot_url
                payload["snapshot_path"] = row.snapshot_path
                payload["webrtc_api"] = row.webrtc_api
                payload["hls"] = row.hls
                payload["created_at"] = format_datetime(row.created_at)
                payload["updated_at"] = format_datetime(row.updated_at)
                payload.setdefault("ext", {})
                return payload

        path = self._get_channel_path(channel_id)
        if not path.exists():
            return None
        return load_json(path)

    def _save_channel(self, channel: dict) -> None:
        channel["updated_at"] = now_iso()
        if settings.METADATA_DB_ENABLED:
            with SessionLocal() as session:
                row = session.query(ChannelMeta).filter(ChannelMeta.id == channel["id"]).one_or_none()
                if not row:
                    return
                payload = dict(row.raw_json or {})
                payload.update(channel)
                payload["device_id"] = row.device_id
                payload["media_node_id"] = row.media_node_id
                payload["name"] = row.name
                payload["type"] = row.type
                payload["source_url"] = row.source_url
                payload["app"] = row.app
                payload["stream"] = row.stream
                payload["is_online"] = row.is_online
                payload["is_playing"] = row.is_playing
                payload["ai_enabled"] = row.ai_enabled
                payload["record_mode"] = row.record_mode
                payload["snapshot_url"] = row.snapshot_url
                payload["snapshot_path"] = row.snapshot_path
                payload["webrtc_api"] = row.webrtc_api
                payload["hls"] = row.hls
                row.updated_at = self._parse_runtime_dt(channel.get("updated_at")) or datetime.now()
                row.raw_json = payload
                session.commit()
            return
        save_json(self._get_channel_path(channel["id"]), channel)

    def _get_task_path(self, task_id: str) -> Path:
        return self.tasks_dir / f"{task_id}.json"

    def _load_task_from_metadata(self, task_id: str) -> dict | None:
        with SessionLocal() as session:
            task_row = session.query(TaskMeta).filter(TaskMeta.id == task_id).one_or_none()
            if not task_row:
                return None

            algorithm_rows = (
                session.query(TaskAlgorithmMeta)
                .filter(TaskAlgorithmMeta.task_id == task_id)
                .order_by(TaskAlgorithmMeta.sort_order.asc(), TaskAlgorithmMeta.id.asc())
                .all()
            )
            algorithm_region_rows = (
                session.query(AlgorithmRegionMeta)
                .filter(AlgorithmRegionMeta.task_id == task_id)
                .order_by(AlgorithmRegionMeta.algorithm_id.asc(), AlgorithmRegionMeta.id.asc())
                .all()
            )
            region_rows = (
                session.query(TaskRegionMeta)
                .filter(TaskRegionMeta.task_id == task_id)
                .order_by(TaskRegionMeta.id.asc())
                .all()
            )
            link_rows = (
                session.query(TaskAlgorithmRegionMeta)
                .filter(TaskAlgorithmRegionMeta.task_id == task_id)
                .order_by(TaskAlgorithmRegionMeta.algorithm_id.asc(), TaskAlgorithmRegionMeta.region_id.asc())
                .all()
            )
            model_ids = [
                row.model_id
                for row in algorithm_rows
                if row.model_id
            ]
            model_rows = (
                session.query(ModelMetaRecord.id, ModelMetaRecord.alert_interval)
                .filter(ModelMetaRecord.id.in_(model_ids))
                .all()
                if model_ids
                else []
            )

        model_alert_intervals_by_id = {
            str(row[0]): int(row[1])
            for row in model_rows
            if row and row[0] and row[1] is not None
        }

        algorithm_regions_by_algorithm: dict[str, list[dict[str, Any]]] = {}
        if algorithm_region_rows:
            for row in algorithm_region_rows:
                algorithm_regions_by_algorithm.setdefault(row.algorithm_id, []).append(
                    {
                        "id": row.id,
                        "name": row.name,
                        "type": row.type,
                        "color": row.color,
                        "coordinates": row.coordinates_json or [],
                        "enabled": bool(row.enabled),
                    }
                )
        else:
            regions = [
                {
                    "id": row.id,
                    "name": row.name,
                    "type": row.type,
                    "color": row.color,
                    "coordinates": row.coordinates_json or [],
                    "enabled": bool(row.enabled),
                }
                for row in region_rows
            ]
            region_map = {region["id"]: region for region in regions}
            region_ids_by_algorithm: dict[str, list[str]] = {}
            for row in link_rows:
                region_ids_by_algorithm.setdefault(row.algorithm_id, []).append(row.region_id)
            for algorithm_id, region_ids in region_ids_by_algorithm.items():
                algorithm_regions_by_algorithm[algorithm_id] = [
                    {
                        **region_map[region_id],
                        "id": f"{algorithm_id}_{region_id}"[:64],
                    }
                    for region_id in region_ids
                    if region_id in region_map
                ]

        algorithms = []
        for row in algorithm_rows:
            algorithm_regions = list(algorithm_regions_by_algorithm.get(row.id, []))
            region_ids = [region["id"] for region in algorithm_regions if region.get("id")]
            model_alert_interval = model_alert_intervals_by_id.get(str(row.model_id or ""))
            alert_interval = model_alert_interval if model_alert_interval is not None else int(row.alert_interval or 60)
            algorithms.append(
                {
                    "id": row.id,
                    "algorithm_type": row.algorithm_type or "detection",
                    "scene_key": row.scene_key,
                    "scene_name": row.scene_name,
                    "rule_type": row.rule_type or row.algorithm_type or "detection",
                    "model_id": row.model_id,
                    "enabled": bool(row.enabled),
                    "labels": row.labels_json or [],
                    "detect_all_classes": bool(row.detect_all_classes),
                    "label_source": row.label_source or ("all" if row.detect_all_classes else "manual"),
                    "confidence": float(row.confidence or 0.25),
                    "alert_interval": alert_interval,
                    "need_snapshot": bool(row.need_snapshot),
                    "need_video_clip": row.need_video_clip is None or bool(row.need_video_clip),
                    "video_before_seconds": int(row.video_before_seconds or 0),
                    "video_after_seconds": int(row.video_after_seconds or 0),
                    "need_overlay": bool(row.need_overlay),
                    "requires_region": bool(row.requires_region),
                    "overlay_color": row.overlay_color or "#ff4d4f",
                    "postprocess_config": row.postprocess_config_json or {},
                    "region_ids": region_ids,
                    "regions": algorithm_regions,
                }
            )

        regions: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for algorithm in algorithms:
            for region in list(algorithm.get("regions") or []):
                region_id = region.get("id")
                if not region_id or region_id in seen_ids:
                    continue
                seen_ids.add(region_id)
                regions.append(region)

        runtime_payload = dict(((task_row.raw_json or {}).get("runtime")) or {})
        runtime_status = runtime_payload.get("status") or task_row.status or "stopped"
        runtime_payload["status"] = runtime_status
        runtime_payload["is_running"] = bool(runtime_payload.get("is_running", task_row.is_running))
        runtime_payload["started_at"] = runtime_payload.get("started_at") or format_datetime(task_row.started_at)
        runtime_payload["stopped_at"] = runtime_payload.get("stopped_at") or format_datetime(task_row.stopped_at)
        runtime_payload["last_analysis_at"] = runtime_payload.get("last_analysis_at") or format_datetime(task_row.last_analysis_at)
        runtime_payload["last_event_at"] = runtime_payload.get("last_event_at") or format_datetime(task_row.last_event_at)
        runtime_payload["last_error"] = runtime_payload.get("last_error", task_row.last_error)
        runtime_payload.setdefault("frame_count", 0)
        runtime_payload.setdefault("event_count", 0)
        runtime_payload.setdefault("models", {})

        return {
            "id": task_row.id,
            "name": task_row.name,
            "source_url": task_row.source_url,
            "source_type": task_row.source_type,
            "channel_id": task_row.channel_id,
            "frame_interval": int(task_row.frame_interval or 10),
            "status": task_row.status or "stopped",
            "created_at": format_datetime(task_row.created_at),
            "updated_at": format_datetime(task_row.updated_at),
            "regions": regions,
            "algorithms": algorithms,
            "runtime": runtime_payload,
        }

    def _load_task(self, task_id: str) -> dict | None:
        task = self._load_task_from_metadata(task_id)
        if task:
            return task
        if settings.METADATA_DB_ENABLED:
            return None
        path = self._get_task_path(task_id)
        if path.exists():
            return load_json(path)
        return None

    def _save_task(self, task: dict) -> None:
        task["updated_at"] = now_iso()
        if settings.METADATA_DB_ENABLED:
            return
        save_json(self._get_task_path(task["id"]), task, sync_metadata=False)

    def _probe_task_stream(self, source_url: str) -> tuple[int, int]:
        cap = cv2.VideoCapture(source_url)
        if not cap.isOpened():
            raise ValueError("Failed to open task source for AI analysis")

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

        if width <= 0 or height <= 0:
            ok, frame = cap.read()
            if not ok or frame is None:
                cap.release()
                raise ValueError("Failed to read initial task frame for AI analysis")
            height, width = frame.shape[:2]

        cap.release()
        return width, height

    def _build_task_capture(self, source_url: str):
        if str(source_url).lower().startswith("rtsp://"):
            width, height = self._probe_task_stream(source_url)
            return FFmpegCapture(source_url, width, height, init_wait=0.3)
        cap = cv2.VideoCapture(source_url)
        if not cap.isOpened():
            raise ValueError("Failed to open task source for AI analysis")
        return cap

    @staticmethod
    def _read_task_frame(capture) -> tuple[bool, Any]:
        if isinstance(capture, FFmpegCapture):
            return capture.read()
        return capture.read()

    @staticmethod
    def _release_task_capture(capture) -> None:
        if capture is None:
            return
        try:
            capture.release()
        except Exception:
            pass

    def _update_runtime(
        self,
        channel_id: str,
        *,
        status: str | None = None,
        last_error: str | None = None,
        last_event_at: str | None = None,
        last_analysis_at: str | None = None,
        started_at: str | None = None,
        stopped_at: str | None = None,
        frame_count: int | None = None,
        event_count: int | None = None,
        clear_error: bool = False,
    ) -> None:
        channel = self._load_channel(channel_id)
        if not channel:
            return

        runtime = channel.setdefault("ext", {}).setdefault("ai_runtime", {})
        if status is not None:
            runtime["status"] = status
        if clear_error:
            runtime["last_error"] = None
        elif last_error is not None:
            runtime["last_error"] = last_error
        if last_event_at is not None:
            runtime["last_event_at"] = last_event_at
        if last_analysis_at is not None:
            runtime["last_analysis_at"] = last_analysis_at
        if started_at is not None:
            runtime["started_at"] = started_at
        if stopped_at is not None:
            runtime["stopped_at"] = stopped_at
        if frame_count is not None:
            runtime["frame_count"] = frame_count
        if event_count is not None:
            runtime["event_count"] = event_count

        self._save_channel(channel)

    def _update_task_runtime(
        self,
        task_id: str,
        *,
        status: str | None = None,
        last_error: str | None = None,
        last_event_at: str | None = None,
        last_analysis_at: str | None = None,
        started_at: str | None = None,
        stopped_at: str | None = None,
        frame_count: int | None = None,
        event_count: int | None = None,
        clear_error: bool = False,
        models: dict[str, Any] | None = None,
    ) -> None:
        task = self._load_task(task_id)
        if not task:
            return

        runtime = task.setdefault("runtime", {})
        if status is not None:
            runtime["status"] = status
            task["status"] = status
        if clear_error:
            runtime["last_error"] = None
        elif last_error is not None:
            runtime["last_error"] = last_error
        if last_event_at is not None:
            runtime["last_event_at"] = last_event_at
        if last_analysis_at is not None:
            runtime["last_analysis_at"] = last_analysis_at
        if started_at is not None:
            runtime["started_at"] = started_at
        if stopped_at is not None:
            runtime["stopped_at"] = stopped_at
        if frame_count is not None:
            runtime["frame_count"] = frame_count
        if event_count is not None:
            runtime["event_count"] = event_count
        if models is not None:
            runtime["models"] = models
        runtime["is_running"] = runtime.get("status") in {"running", "starting", "restarting"}
        self._save_task(task)
        with SessionLocal() as session:
            row = session.query(TaskMeta).filter(TaskMeta.id == task_id).one_or_none()
            if row:
                row.status = runtime.get("status") or task.get("status") or "stopped"
                row.is_running = bool(runtime.get("is_running"))
                row.started_at = self._parse_runtime_dt(runtime.get("started_at"))
                row.stopped_at = self._parse_runtime_dt(runtime.get("stopped_at"))
                row.last_analysis_at = self._parse_runtime_dt(runtime.get("last_analysis_at"))
                row.last_event_at = self._parse_runtime_dt(runtime.get("last_event_at"))
                row.last_error = runtime.get("last_error")
                row.updated_at = self._parse_runtime_dt(task.get("updated_at")) or datetime.now()
                row.raw_json = dict(task)
                session.commit()

    def _parse_runtime_dt(self, value: str | None):
        if not value or not isinstance(value, str):
            return None
        candidate = value.strip().replace("Z", "+00:00")
        if not candidate:
            return None
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            return None

    def _point_in_polygon(self, x: float, y: float, polygon: list[list[float]]) -> bool:
        if len(polygon) < 3:
            return False
        inside = False
        j = len(polygon) - 1
        for i in range(len(polygon)):
            xi, yi = polygon[i]
            xj, yj = polygon[j]
            intersects = ((yi > y) != (yj > y)) and (
                x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
            )
            if intersects:
                inside = not inside
            j = i
        return inside

    def _point_on_segment(
        self,
        px: float,
        py: float,
        ax: float,
        ay: float,
        bx: float,
        by: float,
    ) -> bool:
        cross = (px - ax) * (by - ay) - (py - ay) * (bx - ax)
        if abs(cross) > 1e-9:
            return False
        return (
            min(ax, bx) - 1e-9 <= px <= max(ax, bx) + 1e-9
            and min(ay, by) - 1e-9 <= py <= max(ay, by) + 1e-9
        )

    def _orientation(
        self,
        ax: float,
        ay: float,
        bx: float,
        by: float,
        cx: float,
        cy: float,
    ) -> int:
        value = (by - ay) * (cx - bx) - (bx - ax) * (cy - by)
        if abs(value) <= 1e-9:
            return 0
        return 1 if value > 0 else 2

    def _segments_intersect(
        self,
        a1: tuple[float, float],
        a2: tuple[float, float],
        b1: tuple[float, float],
        b2: tuple[float, float],
    ) -> bool:
        ax1, ay1 = a1
        ax2, ay2 = a2
        bx1, by1 = b1
        bx2, by2 = b2

        o1 = self._orientation(ax1, ay1, ax2, ay2, bx1, by1)
        o2 = self._orientation(ax1, ay1, ax2, ay2, bx2, by2)
        o3 = self._orientation(bx1, by1, bx2, by2, ax1, ay1)
        o4 = self._orientation(bx1, by1, bx2, by2, ax2, ay2)

        if o1 != o2 and o3 != o4:
            return True

        if o1 == 0 and self._point_on_segment(bx1, by1, ax1, ay1, ax2, ay2):
            return True
        if o2 == 0 and self._point_on_segment(bx2, by2, ax1, ay1, ax2, ay2):
            return True
        if o3 == 0 and self._point_on_segment(ax1, ay1, bx1, by1, bx2, by2):
            return True
        if o4 == 0 and self._point_on_segment(ax2, ay2, bx1, by1, bx2, by2):
            return True

        return False

    def _bbox_intersects_polygon(self, detection: dict, polygon: list[list[float]]) -> bool:
        if len(polygon) < 3:
            return False

        x1 = float(detection["x1"])
        y1 = float(detection["y1"])
        x2 = float(detection["x2"])
        y2 = float(detection["y2"])

        bbox_corners = [
            (x1, y1),
            (x2, y1),
            (x2, y2),
            (x1, y2),
        ]
        bbox_edges = [
            (bbox_corners[0], bbox_corners[1]),
            (bbox_corners[1], bbox_corners[2]),
            (bbox_corners[2], bbox_corners[3]),
            (bbox_corners[3], bbox_corners[0]),
        ]
        polygon_points = [(float(point[0]), float(point[1])) for point in polygon]
        polygon_edges = [
            (polygon_points[index], polygon_points[(index + 1) % len(polygon_points)])
            for index in range(len(polygon_points))
        ]

        for px, py in bbox_corners:
            if self._point_in_polygon(px, py, polygon):
                return True

        for px, py in polygon_points:
            if x1 <= px <= x2 and y1 <= py <= y2:
                return True

        for bbox_edge in bbox_edges:
            for polygon_edge in polygon_edges:
                if self._segments_intersect(bbox_edge[0], bbox_edge[1], polygon_edge[0], polygon_edge[1]):
                    return True

        return False

    def _filter_detections_by_zones(self, detections: list[dict], channel: dict) -> list[dict]:
        config = channel.get("ext", {}).get("ai_config", {})
        zone_ids = config.get("zone_ids") or []
        if not zone_ids:
            return detections

        zones = {
            zone.get("id"): zone
            for zone in channel.get("ext", {}).get("zones", [])
            if zone.get("id") in zone_ids
        }
        if not zones:
            return detections

        filtered: list[dict] = []
        for detection in detections:
            for zone_id, zone in zones.items():
                polygon = zone.get("coordinates") or []
                if self._bbox_intersects_polygon(detection, polygon):
                    detection = {**detection, "zone_id": zone_id}
                    filtered.append(detection)
                    break
        return filtered

    def _filter_detections_by_labels(self, detections: list[dict], channel: dict) -> list[dict]:
        config = channel.get("ext", {}).get("ai_config", {})
        if config.get("detect_all_classes"):
            return detections
        labels = config.get("labels") or []
        if not labels:
            return detections

        label_set = {str(label) for label in labels}
        return [
            detection
            for detection in detections
            if str(detection.get("class_name", "")) in label_set
        ]

    def _build_scoped_channel(self, channel: dict, model_config: dict) -> dict:
        return {
            **channel,
            "ext": {
                **channel.get("ext", {}),
                "ai_config": {
                    **channel.get("ext", {}).get("ai_config", {}),
                    "labels": model_config.get("labels") or [],
                    "detect_all_classes": bool(model_config.get("detect_all_classes", False)),
                    "zone_ids": model_config.get("zone_ids") or [],
                },
            },
        }

    def _build_scoped_task(self, task: dict, algorithm: dict) -> dict:
        scoped_zones = list(algorithm.get("regions") or [])
        return {
            **task,
            "ext": {
                "ai_config": {
                    "labels": algorithm.get("labels") or [],
                    "detect_all_classes": bool(algorithm.get("detect_all_classes", False)),
                    "zone_ids": [
                        zone.get("id")
                        for zone in scoped_zones
                        if isinstance(zone, dict) and zone.get("id")
                    ],
                },
                "zones": scoped_zones,
            },
        }

    # def _summarize_classes(self, detections: list[dict]) -> list[str]:
    #     return sorted({
    #         str(detection.get("class_name", ""))
    #         for detection in detections
    #         if detection.get("class_name") is not None
    #     })

    def _normalize_model_configs(self, channel: dict) -> list[dict]:
        ai_config = channel.get("ext", {}).get("ai_config", {}) or {}
        models = ai_config.get("models")
        normalized: list[dict] = []
        if isinstance(models, list) and models:
            for item in models:
                if not isinstance(item, dict):
                    continue
                model_id = item.get("model_id")
                if not model_id:
                    continue
                normalized.append({
                    "model_id": model_id,
                    "enabled": bool(item.get("enabled", True)),
                    "labels": list(item.get("labels") or []),
                    "detect_all_classes": bool(item.get("detect_all_classes", not list(item.get("labels") or []))),
                    "conf": float(item.get("conf", ai_config.get("conf", 0.25)) or 0.25),
                    "frame_interval": max(int(item.get("frame_interval", ai_config.get("frame_interval", 10)) or 10), 1),
                    "alert_interval": max(int(item.get("alert_interval", ai_config.get("alert_interval", 60)) or 60), 0),
                    "zone_ids": list(item.get("zone_ids") or []),
                    "save_snapshot": bool(item.get("save_snapshot", ai_config.get("save_snapshot", True))),
                })
            return normalized

        model_id = ai_config.get("model_id")
        if model_id:
            normalized.append({
                "model_id": model_id,
                "enabled": bool(ai_config.get("enabled")),
                "labels": list(ai_config.get("labels") or []),
                "detect_all_classes": bool(ai_config.get("detect_all_classes", not list(ai_config.get("labels") or []))),
                "conf": float(ai_config.get("conf", 0.25) or 0.25),
                "frame_interval": max(int(ai_config.get("frame_interval", 10) or 10), 1),
                "alert_interval": max(int(ai_config.get("alert_interval", 60) or 60), 0),
                "zone_ids": list(ai_config.get("zone_ids") or []),
                "save_snapshot": bool(ai_config.get("save_snapshot", True)),
            })
        return normalized

    def _apply_model_scoped_filters(self, detections: list[dict], channel: dict, model_config: dict) -> list[dict]:
        scoped_channel = self._build_scoped_channel(channel, model_config)
        detections = self._filter_detections_by_labels(detections, scoped_channel)
        detections = self._filter_detections_by_zones(detections, scoped_channel)
        return detections

    def _apply_algorithm_scoped_filters(self, detections: list[dict], task: dict, algorithm: dict) -> list[dict]:
        scoped_task = self._build_scoped_task(task, algorithm)
        detections = self._filter_detections_by_labels(detections, scoped_task)
        detections = self._filter_detections_by_zones(detections, scoped_task)
        return detections

    def _release_torch_memory(self) -> None:
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def _select_inference_device(self, configured_device: str | None, cuda_available: bool) -> str:
        configured = str(configured_device or "auto").strip().lower()
        if configured in {"", "auto"}:
            return "cuda:0" if cuda_available else "cpu"
        if configured.startswith("cuda") and not cuda_available:
            return "cpu"
        return configured

    def _resolve_inference_device(self) -> str:
        try:
            import torch

            cuda_available = bool(torch.cuda.is_available())
        except Exception:
            cuda_available = False
        return self._select_inference_device(
            getattr(settings, "AI_INFERENCE_DEVICE", "auto"),
            cuda_available=cuda_available,
        )

    def clear_model_cache(self) -> None:
        with self._model_cache_lock:
            self._model_cache.clear()
        gc.collect()
        self._release_torch_memory()

    def _active_model_ids(self) -> set[str]:
        with self._lock:
            channel_ids = list(self._workers.keys())
            task_ids = list(self._task_workers.keys())

        active_model_ids: set[str] = set()
        for channel_id in channel_ids:
            try:
                channel = self._load_channel(channel_id)
                if not channel:
                    continue
                active_model_ids.update(
                    str(item["model_id"])
                    for item in self._normalize_model_configs(channel)
                    if item.get("enabled") and item.get("model_id")
                )
            except Exception:
                logger.exception("Failed to collect active channel model ids: %s", channel_id)

        for task_id in task_ids:
            try:
                task = self._load_task(task_id)
                if not task:
                    continue
                active_model_ids.update(
                    str(item["model_id"])
                    for item in self._normalize_task_algorithms(task)
                    if item.get("enabled") and item.get("model_id")
                )
            except Exception:
                logger.exception("Failed to collect active task model ids: %s", task_id)

        return active_model_ids

    def _prune_model_cache(self) -> None:
        active_model_ids = self._active_model_ids()
        with self._model_cache_lock:
            stale_model_ids = [
                model_id
                for model_id in list(self._model_cache.keys())
                if model_id not in active_model_ids
            ]
        if not stale_model_ids:
            return
        with self._model_cache_lock:
            for model_id in stale_model_ids:
                self._model_cache.pop(model_id, None)
        gc.collect()
        self._release_torch_memory()

    def _load_model(self, model_id: str):
        with self._model_cache_lock:
            cached = self._model_cache.get(model_id)
            if cached:
                return cached

        model_dir = settings.REGISTRY_DIR / model_id
        model_file = model_dir / "model.json"
        if not model_file.exists():
            raise ValueError(f"Model not found: {model_id}")

        model_meta = load_json(model_file)
        weights_path = model_meta.get("weights_path")
        if not weights_path:
            raise ValueError("Model weights_path not found in model.json")

        from ultralytics import YOLO

        model = YOLO(str(weights_path))
        with self._model_cache_lock:
            cached = self._model_cache.get(model_id)
            if cached:
                return cached
            self._model_cache[model_id] = (model, model_meta)
            return model, model_meta

    def _detect_frame(self, model, model_meta: dict, frame, conf_threshold: float) -> list[dict]:
        classes = model_meta.get("classes", [])
        results = model(
            frame,
            verbose=False,
            conf=conf_threshold,
            device=self._resolve_inference_device(),
        )
        detections = []
        for result in results:
            boxes = result.boxes
            for i in range(len(boxes)):
                box = boxes[i]
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                conf = float(box.conf[0])
                class_id = int(box.cls[0])
                class_name = classes[class_id] if class_id < len(classes) else f"class_{class_id}"
                detections.append({
                    "class_id": class_id,
                    "class_name": class_name,
                    "conf": conf,
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                })
        return detections

    def _format_detection_label(self, detection: dict) -> str:
        custom_label = str(detection.get("annotation_label") or "").strip()
        if custom_label:
            return custom_label
        class_name = str(detection.get("class_name") or "unknown")
        conf = detection.get("conf")
        if conf is None:
            return class_name
        return f"{class_name} {float(conf):.2f}"

    @staticmethod
    def _zone_color_bgr(zone: dict | None) -> tuple[int, int, int]:
        color_hex = str((zone or {}).get("color") or "#ff4d4f").lstrip("#")
        try:
            rgb = tuple(int(color_hex[i:i + 2], 16) for i in (0, 2, 4)) if len(color_hex) == 6 else (255, 77, 79)
        except Exception:
            rgb = (255, 77, 79)
        return (rgb[2], rgb[1], rgb[0])

    def _draw_event_regions(self, frame, regions: list[dict] | None):
        annotated = frame.copy()
        if not regions:
            return annotated

        for region in list(regions or []):
            if not isinstance(region, dict):
                continue
            color = self._zone_color_bgr(region)
            if region.get("type") == "fullscreen" or region.get("full_frame"):
                height, width = annotated.shape[:2]
                cv2.rectangle(annotated, (0, 0), (max(width - 1, 0), max(height - 1, 0)), color, 3)
                continue

            coordinates = region.get("coordinates") or []
            points = []
            for point in coordinates:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                points.append((int(point[0]), int(point[1])))
            if len(points) < 2:
                continue

            pts = np.array(points, dtype=np.int32).reshape((-1, 1, 2))
            overlay = annotated.copy()
            cv2.fillPoly(overlay, [pts], color)
            annotated = cv2.addWeighted(overlay, 0.12, annotated, 0.88, 0)
            cv2.polylines(annotated, [pts], isClosed=True, color=color, thickness=2)
        return annotated

    def _annotate_absence_frame(self, frame, detections: list[dict], regions: list[dict] | None = None):
        annotated = self._draw_event_regions(frame, regions)
        box_color = (0, 0, 255)
        text_color = (255, 255, 255)
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.7
        font_thickness = 2
        label_padding = 6

        for detection in detections:
            label = self._format_detection_label(detection)
            anchor = detection.get("label_anchor") or [16, 16]
            if not isinstance(anchor, (list, tuple)) or len(anchor) < 2:
                anchor = [16, 16]
            anchor_x = max(int(anchor[0]), 0)
            anchor_y = max(int(anchor[1]), 0)
            (text_width, text_height), baseline = cv2.getTextSize(label, font, font_scale, font_thickness)
            label_x1 = anchor_x
            label_y1 = anchor_y
            label_x2 = min(label_x1 + text_width + (label_padding * 2), annotated.shape[1] - 1)
            label_y2 = min(label_y1 + text_height + baseline + (label_padding * 2), annotated.shape[0] - 1)

            cv2.rectangle(
                annotated,
                (label_x1, label_y1),
                (label_x2, label_y2),
                box_color,
                thickness=-1,
            )
            text_x = label_x1 + label_padding
            text_y = min(
                max(label_y1 + label_padding + text_height, text_height),
                annotated.shape[0] - baseline - 1,
            )
            cv2.putText(
                annotated,
                label,
                (text_x, text_y),
                font,
                font_scale,
                text_color,
                font_thickness,
                cv2.LINE_AA,
            )
        return annotated

    def _annotate_frame(self, frame, detections: list[dict] | None, regions: list[dict] | None = None):
        annotated = self._draw_event_regions(frame.copy(), regions) if regions else frame.copy()
        if not detections:
            return annotated

        if any(detection.get("absence_seconds") is not None for detection in detections):
            return self._annotate_absence_frame(frame.copy(), detections, regions=regions)

        box_color = (0, 0, 255)
        text_color = (255, 255, 255)
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.6
        font_thickness = 2
        box_thickness = 2
        label_padding = 4

        for detection in detections:
            x1 = max(int(round(float(detection.get("x1", 0)))), 0)
            y1 = max(int(round(float(detection.get("y1", 0)))), 0)
            x2 = max(int(round(float(detection.get("x2", 0)))), x1 + 1)
            y2 = max(int(round(float(detection.get("y2", 0)))), y1 + 1)

            cv2.rectangle(annotated, (x1, y1), (x2, y2), box_color, box_thickness)

            label = self._format_detection_label(detection)
            (text_width, text_height), baseline = cv2.getTextSize(label, font, font_scale, font_thickness)
            label_x1 = x1
            label_y2 = y1
            label_y1 = max(label_y2 - text_height - baseline - (label_padding * 2), 0)

            if label_y1 == 0:
                label_y2 = min(label_y1 + text_height + baseline + (label_padding * 2), annotated.shape[0] - 1)

            label_x2 = min(label_x1 + text_width + (label_padding * 2), annotated.shape[1] - 1)

            cv2.rectangle(
                annotated,
                (label_x1, label_y1),
                (label_x2, label_y2),
                box_color,
                thickness=-1,
            )

            text_x = label_x1 + label_padding
            text_y = min(max(label_y1 + label_padding + text_height, text_height), annotated.shape[0] - baseline - 1)
            cv2.putText(
                annotated,
                label,
                (text_x, text_y),
                font,
                font_scale,
                text_color,
                font_thickness,
                cv2.LINE_AA,
            )

        return annotated

    def _save_event_snapshot(
        self,
        channel_id: str,
        frame,
        detections: list[dict] | None = None,
        regions: list[dict] | None = None,
        fallback_frame=None,
    ) -> str:
        snapshots_dir = settings.SNAPSHOTS_DIR
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{channel_id}_evt_{int(time.time() * 1000)}.jpg"
        output_path = snapshots_dir / filename
        source_frame = self._select_usable_frame(frame, fallback_frame=fallback_frame)
        annotated = self._annotate_frame(source_frame, detections, regions=regions)
        max_bytes = 100 * 1024
        encoded = None

        # Prefer lowering JPEG quality first, then reduce resolution if needed.
        for quality in (85, 75, 65, 55, 45, 35, 25):
            ok, buffer = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if not ok:
                continue
            encoded = buffer
            if len(buffer) <= max_bytes:
                break

        if encoded is not None and len(encoded) > max_bytes:
            current = annotated
            for scale in (0.85, 0.7, 0.55, 0.45, 0.35):
                resized = cv2.resize(current, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                ok, buffer = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, 35])
                if not ok:
                    continue
                encoded = buffer
                if len(buffer) <= max_bytes:
                    break

        if encoded is not None:
            output_path.write_bytes(encoded.tobytes())
        else:
            cv2.imwrite(str(output_path), annotated)
        return str(output_path.resolve())

    def _ts_to_iso(self, ts: float | None) -> str:
        if ts is None:
            return now_iso()
        return datetime.fromtimestamp(ts).isoformat()

    def _save_alarm_video(
        self,
        channel_id: str,
        event_id: str,
        frames: list[object],
        detections: list[dict] | None = None,
        fps: float = 25.0,
    ) -> str | None:
        if not frames:
            return None

        safe_fps = max(float(fps or 25.0), 1.0)
        frame_limit = self._alarm_video_frame_limit(safe_fps)
        frames = list(frames)[:frame_limit]
        frames = self._sanitize_alarm_video_frames(frames)
        if not frames:
            return None

        recordings_dir = settings.RECORDINGS_DIR
        recordings_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{channel_id}_alarm_{int(time.time() * 1000)}_{event_id}.mp4"
        output_path = recordings_dir / filename
        temp_output_path = recordings_dir / f"{output_path.stem}_raw{output_path.suffix}"

        first_frame = self._decode_alarm_video_frame(frames[0][1])
        height, width = first_frame.shape[:2]
        writer = cv2.VideoWriter(
            str(temp_output_path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            safe_fps,
            (width, height),
        )
        try:
            for _, frame in frames:
                writer.write(self._prepare_alarm_video_frame(frame, detections))
        finally:
            writer.release()

        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(temp_output_path),
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(output_path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                temp_output_path.unlink(missing_ok=True)
            except Exception:
                pass
        except Exception:
            if temp_output_path.exists():
                try:
                    if output_path.exists():
                        output_path.unlink()
                except Exception:
                    pass
                temp_output_path.replace(output_path)
        return str(output_path.resolve())

    def _prepare_alarm_video_frame(self, frame, detections: list[dict] | None = None):
        return self._decode_alarm_video_frame(frame)

    def _build_alarm_video_frames(
        self,
        *,
        before_frames: list[tuple[float, object]],
        after_frames: list[tuple[float, object]],
        fps: float = 25.0,
        before_seconds: int = 10,
        after_seconds: int = 10,
    ) -> list[tuple[float, object]]:
        safe_fps = max(float(fps or 25.0), 1.0)
        safe_before_seconds = max(int(before_seconds or 0), 0)
        safe_after_seconds = max(int(after_seconds or 0), 0)
        before_limit = max(int(round(safe_fps * safe_before_seconds)), 0)
        after_limit = max(int(round(safe_fps * safe_after_seconds)), 0)
        selected_before = list(before_frames)[-before_limit:] if before_limit else []
        selected_after = list(after_frames)[:after_limit] if after_limit else []
        total_limit = max(int(round(safe_fps * (safe_before_seconds + safe_after_seconds))), 1)
        return (selected_before + selected_after)[:total_limit]

    def _build_alarm_recording_payload(
        self,
        *,
        event: dict,
        channel_id: str | None,
        task_id: str | None,
        algorithm_id: str | None,
        model_id: str | None,
        detection: dict,
        snapshot_path: str | None,
        video_path: str,
        video_frames: list[tuple[float, object]],
        fps: float = 25.0,
    ) -> dict:
        duration = self._alarm_video_total_seconds()
        class_name = detection.get("class_name", "unknown")
        conf = detection.get("conf", 0)
        event_type = event.get("event_type") or "detection"
        return {
            "id": event["id"],
            "channel_id": channel_id,
            "task_id": task_id,
            "algorithm_id": algorithm_id,
            "started_at": self._ts_to_iso(video_frames[0][0] if video_frames else time.time()),
            "ended_at": self._ts_to_iso(video_frames[-1][0] if video_frames else time.time()),
            "duration": duration,
            "file_path": video_path,
            "status": "ready",
            "raw_json": {
                "event_id": event["id"],
                "task_id": task_id,
                "algorithm_id": algorithm_id,
                "channel_id": channel_id,
                "model_id": model_id,
                "event_type": event_type,
                "event_type_label": "alarm_clip",
                "class_name": class_name,
                "conf": conf,
                "snapshot_path": snapshot_path,
                "video_path": video_path,
            },
        }

    @staticmethod
    def _build_event_update_request(*, video_path: str | None = None):
        return type("Req", (), {
            "event_type": None,
            "class_name": None,
            "conf": None,
            "snapshot_path": None,
            "video_path": video_path,
            "remark": None,
            "status": None,
        })()

    @staticmethod
    def _task_runtime_model_key(algorithm: dict) -> str:
        return str(algorithm.get("id") or algorithm.get("model_id") or "unknown")

    def _task_eligible_algorithms_for_frame(self, algorithms: list[dict], frame_count: int) -> list[dict]:
        eligible: list[dict] = []
        for algorithm in algorithms:
            frame_interval = max(int(algorithm.get("frame_interval", 10) or 10), 1)
            if frame_count % frame_interval == 0:
                eligible.append(algorithm)
        return eligible

    def _run_task_algorithm_inference(self, *, task: dict, algorithm: dict, frame) -> dict[str, Any]:
        model_id = algorithm["model_id"]
        algorithm_id = algorithm.get("id")
        rule_type = str(algorithm.get("rule_type") or algorithm.get("algorithm_type") or "detection").lower()
        try:
            model, model_meta = self._load_model(model_id)
            detections = self._detect_frame(
                model,
                model_meta,
                frame,
                float(algorithm.get("confidence", 0.25) or 0.25),
            )
            detections = self._apply_algorithm_scoped_filters(detections, task, algorithm)
            return {
                "algorithm": algorithm,
                "algorithm_id": algorithm_id,
                "model_id": model_id,
                "rule_type": rule_type,
                "detections": detections,
                "error": None,
            }
        except Exception as exc:
            logger.exception(
                "Task algorithm inference failed: task=%s algorithm=%s model=%s",
                task.get("id"),
                algorithm_id,
                model_id,
            )
            return {
                "algorithm": algorithm,
                "algorithm_id": algorithm_id,
                "model_id": model_id,
                "rule_type": rule_type,
                "detections": [],
                "error": str(exc),
            }

    def _collect_task_algorithm_inferences(
        self,
        *,
        task: dict,
        algorithms: list[dict],
        frame,
        frame_count: int,
    ) -> list[dict[str, Any]]:
        eligible_algorithms = self._task_eligible_algorithms_for_frame(algorithms, frame_count)
        if not eligible_algorithms:
            return []
        if len(eligible_algorithms) == 1:
            return [
                self._run_task_algorithm_inference(
                    task=task,
                    algorithm=eligible_algorithms[0],
                    frame=frame,
                )
            ]

        futures = [
            (
                index,
                self._task_inference_executor.submit(
                    self._run_task_algorithm_inference,
                    task=task,
                    algorithm=algorithm,
                    frame=frame,
                ),
            )
            for index, algorithm in enumerate(eligible_algorithms)
        ]
        results: list[tuple[int, dict[str, Any]]] = []
        for index, future in futures:
            results.append((index, future.result()))
        results.sort(key=lambda item: item[0])
        return [item[1] for item in results]

    def _collect_task_alarm_after_frames(
        self,
        *,
        source_url: str,
        after_frame_limit: int,
        after_seconds: int,
    ) -> list[tuple[float, object]]:
        if after_frame_limit <= 0:
            return []

        capture = None
        after_frames: list[tuple[float, object]] = []
        fail_count = 0
        deadline = time.time() + self._task_alarm_video_capture_timeout_seconds(after_seconds)
        try:
            capture = self._build_task_capture(source_url)
            while len(after_frames) < after_frame_limit and time.time() < deadline:
                ok_after, after_frame = self._read_task_frame(capture)
                if not ok_after:
                    fail_count += 1
                    if fail_count >= self._task_alarm_video_read_fail_limit():
                        break
                    time.sleep(0.05)
                    continue
                fail_count = 0
                ts_after = time.time()
                after_frames.append((ts_after, self._pack_alarm_video_frame(after_frame)))
        except Exception:
            logger.exception("Failed to collect task alarm video frames: source=%s", source_url)
        finally:
            self._release_task_capture(capture)
        return after_frames

    def _finalize_task_alarm_video_job(
        self,
        *,
        task_id: str,
        task_channel_id: str | None,
        source_url: str,
        event: dict,
        algorithm: dict,
        model_id: str,
        detection: dict,
        snapshot_path: str | None,
        before_frames: list[tuple[float, object]],
        fps: float,
        before_seconds: int,
        after_seconds: int,
    ) -> None:
        after_frame_limit = max(int(round(max(fps, 1.0) * max(after_seconds, 0))), 0)
        after_frames = self._collect_task_alarm_after_frames(
            source_url=source_url,
            after_frame_limit=after_frame_limit,
            after_seconds=after_seconds,
        )
        video_frames = self._build_alarm_video_frames(
            before_frames=before_frames,
            after_frames=after_frames,
            fps=fps,
            before_seconds=before_seconds,
            after_seconds=after_seconds,
        )
        if not video_frames:
            logger.warning("Skip empty task alarm video: task=%s event=%s", task_id, event["id"])
            return

        video_path = self._save_alarm_video(task_id, event["id"], video_frames, [detection], fps=fps)
        if not video_path:
            logger.warning("Failed to save task alarm video: task=%s event=%s", task_id, event["id"])
            return

        self.events_service.update_event_sync(
            event["id"],
            self._build_event_update_request(video_path=video_path),
        )
        self.recording_service.create_recording_sync(
            self._build_alarm_recording_payload(
                event=event,
                channel_id=task_channel_id,
                task_id=task_id,
                algorithm_id=algorithm.get("id"),
                model_id=model_id,
                detection=detection,
                snapshot_path=snapshot_path,
                video_path=video_path,
                video_frames=video_frames,
                fps=fps,
            )
        )

    def _submit_task_alarm_video_job(
        self,
        *,
        task_id: str,
        task_channel_id: str | None,
        source_url: str,
        event: dict,
        algorithm: dict,
        model_id: str,
        detection: dict,
        snapshot_path: str | None,
        before_frames: list[tuple[float, object]],
        fps: float,
        before_seconds: int,
        after_seconds: int,
    ) -> None:
        future = self._alarm_video_executor.submit(
            self._finalize_task_alarm_video_job,
            task_id=task_id,
            task_channel_id=task_channel_id,
            source_url=source_url,
            event=event,
            algorithm=algorithm,
            model_id=model_id,
            detection=detection,
            snapshot_path=snapshot_path,
            before_frames=list(before_frames),
            fps=fps,
            before_seconds=before_seconds,
            after_seconds=after_seconds,
        )
        self._track_alarm_video_future(future)

        def _log_failure(done_future):
            exc = done_future.exception()
            if exc is not None:
                logger.error(
                    "Task alarm video job failed: task=%s event=%s",
                    task_id,
                    event["id"],
                    exc_info=exc,
                )

        future.add_done_callback(_log_failure)

    def _collect_channel_alarm_after_frames(
        self,
        *,
        source_url: str,
        after_frame_limit: int,
        after_seconds: int,
    ) -> list[tuple[float, object]]:
        if after_frame_limit <= 0:
            return []

        cap = None
        after_frames: list[tuple[float, object]] = []
        fail_count = 0
        deadline = time.time() + self._task_alarm_video_capture_timeout_seconds(after_seconds)
        try:
            cap = cv2.VideoCapture(source_url)
            if not cap.isOpened():
                return []
            while len(after_frames) < after_frame_limit and time.time() < deadline:
                ok_after, after_frame = cap.read()
                if not ok_after:
                    fail_count += 1
                    if fail_count >= self._task_alarm_video_read_fail_limit():
                        break
                    time.sleep(0.05)
                    continue
                fail_count = 0
                ts_after = time.time()
                after_frames.append((ts_after, self._pack_alarm_video_frame(after_frame)))
        except Exception:
            logger.exception("Failed to collect channel alarm video frames: source=%s", source_url)
        finally:
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass
        return after_frames

    def _finalize_channel_alarm_video_job(
        self,
        *,
        channel_id: str,
        source_url: str,
        event: dict,
        model_id: str,
        detection: dict,
        snapshot_path: str | None,
        before_frames: list[tuple[float, object]],
        fps: float,
        before_seconds: int,
        after_seconds: int,
    ) -> None:
        after_frame_limit = max(int(round(max(fps, 1.0) * max(after_seconds, 0))), 0)
        after_frames = self._collect_channel_alarm_after_frames(
            source_url=source_url,
            after_frame_limit=after_frame_limit,
            after_seconds=after_seconds,
        )
        video_frames = self._build_alarm_video_frames(
            before_frames=before_frames,
            after_frames=after_frames,
            fps=fps,
            before_seconds=before_seconds,
            after_seconds=after_seconds,
        )
        if not video_frames:
            logger.warning("Skip empty channel alarm video: channel=%s event=%s", channel_id, event["id"])
            return

        video_path = self._save_alarm_video(channel_id, event["id"], video_frames, [detection], fps=fps)
        if not video_path:
            logger.warning("Failed to save channel alarm video: channel=%s event=%s", channel_id, event["id"])
            return

        self.events_service.update_event_sync(
            event["id"],
            self._build_event_update_request(video_path=video_path),
        )
        self.recording_service.create_recording_sync(
            self._build_alarm_recording_payload(
                event=event,
                channel_id=channel_id,
                task_id=None,
                algorithm_id=None,
                model_id=model_id,
                detection=detection,
                snapshot_path=snapshot_path,
                video_path=video_path,
                video_frames=video_frames,
                fps=fps,
            )
        )

    def _submit_channel_alarm_video_job(
        self,
        *,
        channel_id: str,
        source_url: str,
        event: dict,
        model_id: str,
        detection: dict,
        snapshot_path: str | None,
        before_frames: list[tuple[float, object]],
        fps: float,
        before_seconds: int,
        after_seconds: int,
    ) -> None:
        future = self._alarm_video_executor.submit(
            self._finalize_channel_alarm_video_job,
            channel_id=channel_id,
            source_url=source_url,
            event=event,
            model_id=model_id,
            detection=detection,
            snapshot_path=snapshot_path,
            before_frames=list(before_frames),
            fps=fps,
            before_seconds=before_seconds,
            after_seconds=after_seconds,
        )
        self._track_alarm_video_future(future)

        def _log_failure(done_future):
            exc = done_future.exception()
            if exc is not None:
                logger.error(
                    "Channel alarm video job failed: channel=%s event=%s",
                    channel_id,
                    event["id"],
                    exc_info=exc,
                )

        future.add_done_callback(_log_failure)

    def _track_alarm_video_future(self, future: Future) -> None:
        with self._lock:
            self._alarm_video_futures.add(future)

        def _cleanup(done_future: Future) -> None:
            with self._lock:
                self._alarm_video_futures.discard(done_future)

        future.add_done_callback(_cleanup)

    async def _wait_for_alarm_video_jobs(self, timeout_seconds: float) -> tuple[int, int]:
        deadline = time.time() + max(float(timeout_seconds or 0), 0.0)
        while time.time() < deadline:
            with self._lock:
                pending = [future for future in self._alarm_video_futures if not future.done()]
            if not pending:
                return 0, 0
            await asyncio.sleep(0.05)

        with self._lock:
            pending = [future for future in self._alarm_video_futures if not future.done()]
        return len(pending), len(pending)

    def _event_cooldown_key(self, detection: dict) -> str:
        zone_id = detection.get("zone_id", "global")
        return f"{detection.get('class_name', 'unknown')}::{zone_id}"

    def _event_cooldown_key_for_algorithm(self, algorithm: dict, detection: dict) -> str:
        rule_type = str(algorithm.get("rule_type") or algorithm.get("algorithm_type") or "detection").lower()
        algorithm_type = str(algorithm.get("algorithm_type") or "detection").lower()
        if algorithm_type == "detection" and rule_type in {"detection", "intrusion"}:
            return "detection"
        return self._event_cooldown_key(detection)

    def _rule_state_key(self, algorithm: dict, suffix: str = "global") -> str:
        algorithm_id = str(algorithm.get("id") or algorithm.get("model_id") or "unknown")
        return f"{algorithm_id}::{suffix}"

    def _absence_threshold_seconds(self, algorithm: dict) -> int:
        config = get_algorithm_rule_config("absence")
        return max(int(config.get("absence_seconds", 5) or 5), 1)

    def _algorithm_regions(self, algorithm: dict) -> list[dict]:
        return [
            region
            for region in list(algorithm.get("regions") or [])
            if isinstance(region, dict) and region.get("id")
        ]

    def _absence_regions(self, algorithm: dict) -> list[dict]:
        return self._algorithm_regions(algorithm)

    def _count_rule_threshold(self, algorithm: dict, rule_type: str) -> tuple[int, int]:
        config = get_algorithm_rule_config(rule_type)
        default_count = 3 if rule_type == "gathering" else 5
        count_threshold = max(int(config.get("count_threshold", default_count) or default_count), 1)
        hold_seconds = max(int(config.get("hold_seconds", 2) or 2), 1)
        return count_threshold, hold_seconds

    def _loitering_threshold_seconds(self, algorithm: dict) -> int:
        config = get_algorithm_rule_config("loitering")
        return max(int(config.get("linger_seconds", 10) or 10), 1)

    def _fall_like_thresholds(self, algorithm: dict) -> tuple[float, int, int]:
        config = get_algorithm_rule_config("fall_like")
        ratio_threshold = max(float(config.get("fall_ratio_threshold", 1.2) or 1.2), 0.1)
        hold_seconds = max(int(config.get("hold_seconds", 2) or 2), 1)
        min_box_height = max(int(config.get("min_box_height", 40) or 40), 1)
        return ratio_threshold, hold_seconds, min_box_height

    @staticmethod
    def _region_label_anchor(region: dict | None, fallback_detection: dict | None = None) -> list[int]:
        if isinstance(region, dict):
            coordinates = region.get("coordinates") or []
            if coordinates and isinstance(coordinates[0], (list, tuple)) and len(coordinates[0]) >= 2:
                return [
                    max(int(coordinates[0][0]), 0),
                    max(int(coordinates[0][1]) - 28, 0),
                ]
        if isinstance(fallback_detection, dict):
            return [
                max(int(float(fallback_detection.get("x1", 16))), 0),
                max(int(float(fallback_detection.get("y1", 16))) - 28, 0),
            ]
        return [16, 16]

    def _build_count_threshold_events(
        self,
        *,
        algorithm: dict,
        detections: list[dict],
        now_ts: float,
        state: dict,
        rule_type: str,
    ) -> list[dict]:
        count_threshold, hold_seconds = self._count_rule_threshold(algorithm, rule_type)
        primary_label = str((algorithm.get("labels") or [rule_type])[0])
        regions_by_id = {
            str(region.get("id")): region
            for region in self._algorithm_regions(algorithm)
            if region.get("id")
        }
        grouped_detections: dict[str, list[dict]] = {}
        for detection in detections:
            zone_id = str(detection.get("zone_id") or "global")
            grouped_detections.setdefault(zone_id, []).append(dict(detection))

        zone_ids = list(regions_by_id.keys()) if regions_by_id else list(grouped_detections.keys()) or ["global"]
        events: list[dict] = []
        for zone_id in zone_ids:
            grouped = grouped_detections.get(zone_id, [])
            state_key = self._rule_state_key(algorithm, f"{rule_type}::{zone_id}")
            threshold_state = state.setdefault(state_key, {})
            current_count = len(grouped)
            if current_count < count_threshold:
                threshold_state["active_since"] = None
                threshold_state["emitted"] = False
                threshold_state["last_count"] = current_count
                continue

            active_since = threshold_state.get("active_since")
            if active_since is None:
                threshold_state["active_since"] = now_ts
                threshold_state["emitted"] = False
                threshold_state["last_count"] = current_count
                continue

            elapsed = max(int(now_ts - float(active_since)), 0)
            threshold_state["last_count"] = current_count
            if elapsed < hold_seconds or threshold_state.get("emitted"):
                continue

            region = regions_by_id.get(zone_id)
            representative = dict(grouped[0]) if grouped else {
                "class_name": primary_label,
                "conf": 1.0,
            }
            representative["class_name"] = primary_label
            representative["conf"] = max(float(item.get("conf", 0) or 0) for item in grouped) if grouped else 1.0
            representative["object_count"] = current_count
            representative["zone_id"] = zone_id
            representative["zone_name"] = (region or {}).get("name") or zone_id
            representative["annotation_label"] = f"{algorithm.get('scene_name') or rule_type} {current_count}"
            representative["label_anchor"] = self._region_label_anchor(region, representative)
            representative["snapshot_detections"] = grouped or [dict(representative)]
            representative["snapshot_regions"] = [region] if region else []
            threshold_state["emitted"] = True
            events.append(representative)
        return events

    def _build_loitering_events(
        self,
        *,
        algorithm: dict,
        detections: list[dict],
        now_ts: float,
        state: dict,
    ) -> list[dict]:
        linger_seconds = self._loitering_threshold_seconds(algorithm)
        regions_by_id = {
            str(region.get("id")): region
            for region in self._algorithm_regions(algorithm)
            if region.get("id")
        }
        grouped_detections: dict[str, list[dict]] = {}
        for detection in detections:
            zone_id = str(detection.get("zone_id") or "global")
            grouped_detections.setdefault(zone_id, []).append(dict(detection))

        zone_ids = list(regions_by_id.keys()) if regions_by_id else list(grouped_detections.keys()) or ["global"]
        events: list[dict] = []
        for zone_id in zone_ids:
            grouped = grouped_detections.get(zone_id, [])
            state_key = self._rule_state_key(algorithm, f"loitering::{zone_id}")
            loiter_state = state.setdefault(state_key, {})
            if not grouped:
                loiter_state["active_since"] = None
                loiter_state["emitted"] = False
                continue

            active_since = loiter_state.get("active_since")
            if active_since is None:
                loiter_state["active_since"] = now_ts
                loiter_state["emitted"] = False
                continue

            elapsed = max(int(now_ts - float(active_since)), 0)
            if elapsed < linger_seconds or loiter_state.get("emitted"):
                continue

            region = regions_by_id.get(zone_id)
            representative = dict(grouped[0])
            representative["loiter_seconds"] = elapsed
            representative["zone_id"] = zone_id
            representative["zone_name"] = (region or {}).get("name") or zone_id
            representative["annotation_label"] = f"{algorithm.get('scene_name') or 'loitering'} {elapsed}s"
            representative["label_anchor"] = self._region_label_anchor(region, representative)
            representative["snapshot_detections"] = grouped or [dict(representative)]
            representative["snapshot_regions"] = [region] if region else []
            loiter_state["emitted"] = True
            events.append(representative)
        return events

    def _build_fall_like_events(
        self,
        *,
        algorithm: dict,
        detections: list[dict],
        now_ts: float,
        state: dict,
    ) -> list[dict]:
        ratio_threshold, hold_seconds, min_box_height = self._fall_like_thresholds(algorithm)
        target_labels = {
            str(label)
            for label in list(algorithm.get("labels") or ["person"])
            if str(label or "").strip()
        } or {"person"}
        regions_by_id = {
            str(region.get("id")): region
            for region in self._algorithm_regions(algorithm)
            if region.get("id")
        }
        grouped_candidates: dict[str, list[dict]] = {}
        for detection in detections:
            class_name = str(detection.get("class_name") or "")
            if target_labels and class_name not in target_labels:
                continue
            width = max(float(detection.get("x2", 0) or 0) - float(detection.get("x1", 0) or 0), 0.0)
            height = max(float(detection.get("y2", 0) or 0) - float(detection.get("y1", 0) or 0), 0.0)
            if height < float(min_box_height):
                continue
            if height <= 0:
                continue
            ratio = width / height
            if ratio < ratio_threshold:
                continue
            zone_id = str(detection.get("zone_id") or "global")
            grouped_candidates.setdefault(zone_id, []).append({
                **dict(detection),
                "fall_ratio": ratio,
            })

        zone_ids = list(regions_by_id.keys()) if regions_by_id else list(grouped_candidates.keys()) or ["global"]
        events: list[dict] = []
        for zone_id in zone_ids:
            grouped = grouped_candidates.get(zone_id, [])
            state_key = self._rule_state_key(algorithm, f"fall_like::{zone_id}")
            fall_state = state.setdefault(state_key, {})
            if not grouped:
                fall_state["active_since"] = None
                fall_state["emitted"] = False
                continue

            active_since = fall_state.get("active_since")
            if active_since is None:
                fall_state["active_since"] = now_ts
                fall_state["emitted"] = False
                continue

            elapsed = max(int(now_ts - float(active_since)), 0)
            if elapsed < hold_seconds or fall_state.get("emitted"):
                continue

            region = regions_by_id.get(zone_id)
            representative = max(
                grouped,
                key=lambda item: (
                    float(item.get("fall_ratio", 0) or 0),
                    float(item.get("conf", 0) or 0),
                ),
            )
            representative = dict(representative)
            representative["fall_seconds"] = elapsed
            representative["zone_id"] = zone_id
            representative["zone_name"] = (region or {}).get("name") or zone_id
            representative["annotation_label"] = f"{algorithm.get('scene_name') or '人员倒地'} {elapsed}s"
            representative["label_anchor"] = self._region_label_anchor(region, representative)
            representative["snapshot_detections"] = grouped or [dict(representative)]
            representative["snapshot_regions"] = [region] if region else []
            fall_state["emitted"] = True
            events.append(representative)
        return events

    def _build_rule_events(
        self,
        task: dict,
        algorithm: dict,
        detections: list[dict],
        now_ts: float,
        state: dict,
    ) -> list[dict]:
        rule_type = str(algorithm.get("rule_type") or algorithm.get("algorithm_type") or "detection").lower()

        if rule_type in {"detection", "intrusion"}:
            return [dict(detection) for detection in detections]

        if rule_type == "count":
            return []

        if rule_type in {"gathering", "congestion"}:
            return self._build_count_threshold_events(
                algorithm=algorithm,
                detections=detections,
                now_ts=now_ts,
                state=state,
                rule_type=rule_type,
            )

        if rule_type == "loitering":
            return self._build_loitering_events(
                algorithm=algorithm,
                detections=detections,
                now_ts=now_ts,
                state=state,
            )

        if rule_type == "fall_like":
            return self._build_fall_like_events(
                algorithm=algorithm,
                detections=detections,
                now_ts=now_ts,
                state=state,
            )

        if rule_type == "absence":
            tracked_labels = list(algorithm.get("labels") or [])
            target_class = str(tracked_labels[0] if tracked_labels else "person")
            threshold = self._absence_threshold_seconds(algorithm)
            matched = [
                detection for detection in detections
                if str(detection.get("class_name") or "") == target_class
            ]
            regions = self._absence_regions(algorithm)
            if not regions:
                state_key = self._rule_state_key(algorithm, "absence::global")
                absence_state = state.setdefault(state_key, {})
                if matched:
                    absence_state["missing_since"] = None
                    absence_state["emitted"] = False
                    return []

                missing_since = absence_state.get("missing_since")
                if missing_since is None:
                    absence_state["missing_since"] = now_ts
                    absence_state["emitted"] = False
                    return []

                elapsed = max(int(now_ts - float(missing_since)), 0)
                if elapsed < threshold or absence_state.get("emitted"):
                    return []

                absence_state["emitted"] = True
                return [{
                    "class_name": target_class,
                    "conf": 1.0,
                    "absence_seconds": elapsed,
                    "zone_id": "global",
                    "annotation_label": f"{algorithm.get('scene_name') or '离岗检测'} {elapsed}s",
                    "label_anchor": [16, 16],
                }]

            matched_zone_ids = {
                str(detection.get("zone_id") or "")
                for detection in matched
                if str(detection.get("zone_id") or "")
            }
            events: list[dict] = []
            for region in regions:
                zone_id = str(region.get("id") or "")
                if not zone_id:
                    continue
                state_key = self._rule_state_key(algorithm, f"absence::{zone_id}")
                absence_state = state.setdefault(state_key, {})
                if zone_id in matched_zone_ids:
                    absence_state["missing_since"] = None
                    absence_state["emitted"] = False
                    continue

                missing_since = absence_state.get("missing_since")
                if missing_since is None:
                    absence_state["missing_since"] = now_ts
                    absence_state["emitted"] = False
                    continue

                elapsed = max(int(now_ts - float(missing_since)), 0)
                if elapsed < threshold or absence_state.get("emitted"):
                    continue

                coordinates = region.get("coordinates") or []
                label_anchor = [16, 16]
                if coordinates and isinstance(coordinates[0], (list, tuple)) and len(coordinates[0]) >= 2:
                    label_anchor = [
                        max(int(coordinates[0][0]), 0),
                        max(int(coordinates[0][1]) - 28, 0),
                    ]
                absence_state["emitted"] = True
                events.append({
                    "class_name": target_class,
                    "conf": 1.0,
                    "absence_seconds": elapsed,
                    "zone_id": zone_id,
                    "zone_name": region.get("name") or zone_id,
                    "annotation_label": f"{algorithm.get('scene_name') or '离岗检测'} {elapsed}s",
                    "label_anchor": label_anchor,
                })
            return events

        return [dict(detection) for detection in detections]

    def _build_task_event_payload(
        self,
        task_id: str,
        task: dict,
        algorithm: dict,
        detection: dict,
        snapshot_path: str | None,
    ) -> dict:
        rule_type = algorithm.get("rule_type") or algorithm.get("algorithm_type") or "detection"
        event_type = "detection" if str(rule_type).lower() in DETECTION_RULE_TYPES else rule_type
        model_id = algorithm.get("model_id")
        algorithm_id = algorithm.get("id")
        persisted_detection = {
            key: value
            for key, value in detection.items()
            if key not in {"snapshot_detections", "snapshot_regions"}
        }
        return {
            "channel_id": task.get("channel_id"),
            "task_id": task_id,
            "algorithm_id": algorithm_id,
            "model_id": model_id,
            "event_type": event_type,
            "class_name": persisted_detection.get("class_name", "unknown"),
            "conf": persisted_detection.get("conf", 0),
            "snapshot_path": snapshot_path,
            "handling_status": 0,
            "remark": json.dumps({
                **persisted_detection,
                "task_id": task_id,
                "algorithm_id": algorithm_id,
                "model_id": model_id,
                "rule_type": rule_type,
            }, ensure_ascii=False),
            "status": "new",
        }

    def _count_classes(self, detections: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for detection in detections:
            class_name = str(detection.get("class_name") or "unknown")
            counts[class_name] = counts.get(class_name, 0) + 1
        return counts

    def _metric_type_for_algorithm(self, algorithm: dict) -> str:
        labels = [str(label) for label in list(algorithm.get("labels") or []) if label]
        if labels and all(label in {"car", "bus", "truck", "motorcycle", "bicycle"} for label in labels):
            return "vehicle_count"
        if labels == ["person"] or "person" in labels:
            return "person_count"
        return "count"

    def _update_count_metric(self, task: dict, algorithm: dict, detections: list[dict], now_ts: float) -> None:
        algorithm_id = algorithm.get("id")
        if not algorithm_id:
            return
        class_counts = self._count_classes(detections)
        metric_value = sum(class_counts.values())
        metric_service.upsert_task_algorithm_metric_sync(
            task_id=task["id"],
            algorithm_id=algorithm_id,
            channel_id=task.get("channel_id"),
            model_id=algorithm.get("model_id"),
            metric_type=self._metric_type_for_algorithm(algorithm),
            metric_value=metric_value,
            region_id=None,
            region_name=None,
            raw_json={
                "task_id": task.get("id"),
                "algorithm_id": algorithm_id,
                "algorithm_name": algorithm.get("scene_name") or algorithm.get("rule_type"),
                "model_id": algorithm.get("model_id"),
                "metric_type": self._metric_type_for_algorithm(algorithm),
                "metric_value": metric_value,
                "class_counts": class_counts,
                "detections": len(detections),
                "updated_ts": now_ts,
            },
        )

    def _normalize_task_algorithms(self, task: dict) -> list[dict]:
        algorithms = list(task.get("algorithms") or [])
        normalized: list[dict] = []
        for item in algorithms:
            if not isinstance(item, dict) or not item.get("model_id"):
                continue
            regions = [
                region
                for region in list(item.get("regions") or [])
                if isinstance(region, dict)
            ]
            region_ids = [
                region.get("id")
                for region in regions
                if region.get("id")
            ]
            normalized.append({
                "id": item.get("id"),
                "algorithm_type": item.get("algorithm_type", "detection"),
                "scene_key": item.get("scene_key"),
                "scene_name": item.get("scene_name"),
                "rule_type": item.get("rule_type") or item.get("algorithm_type", "detection"),
                "model_id": item.get("model_id"),
                "enabled": bool(item.get("enabled", True)),
                "labels": list(item.get("labels") or []),
                "detect_all_classes": bool(item.get("detect_all_classes", not list(item.get("labels") or []))),
                "label_source": item.get("label_source") or ("all" if item.get("detect_all_classes", not list(item.get("labels") or [])) else "manual"),
                "confidence": float(item.get("confidence", item.get("conf", 0.25)) or 0.25),
                "frame_interval": max(int(task.get("frame_interval", 10) or 10), 1),
                "alert_interval": max(int(item.get("alert_interval", 60) or 60), 0),
                "need_snapshot": bool(item.get("need_snapshot", True)),
                "need_video_clip": bool(item.get("need_video_clip", True)),
                "video_before_seconds": self._alarm_video_before_seconds() if bool(item.get("need_video_clip", True)) else 0,
                "video_after_seconds": self._alarm_video_after_seconds() if bool(item.get("need_video_clip", True)) else 0,
                "need_overlay": bool(item.get("need_overlay", False)),
                "requires_region": bool(item.get("requires_region", item.get("need_overlay", False))),
                "region_ids": [region_id for region_id in region_ids if region_id] or region_ids,
                "postprocess_config": dict(item.get("postprocess_config") or {}),
                "regions": regions,
            })
        return normalized

    def _run_worker(self, channel_id: str, stop_event: threading.Event) -> None:
        frame_count = 0
        event_count = 0
        cooldown_cache: dict[str, float] = {}
        pre_event_buffer: deque[tuple[float, object]] = deque(maxlen=self._alarm_video_pre_event_buffer_size())

        try:
            channel = self._load_channel(channel_id)
            if not channel:
                raise ValueError("Channel not found")

            ai_config = channel.get("ext", {}).get("ai_config", {})
            if not ai_config.get("enabled"):
                raise ValueError("AI is not enabled for this channel")

            model_configs = [
                item for item in self._normalize_model_configs(channel)
                if item.get("enabled") and item.get("model_id")
            ]
            if not model_configs:
                raise ValueError("No enabled AI model is configured for this channel")

            source_url = channel.get("source_url")
            if not source_url:
                raise ValueError("Channel source_url is empty")

            cap = cv2.VideoCapture(source_url)
            if not cap.isOpened():
                raise ValueError("Failed to open channel source for AI analysis")

            self._update_runtime(
                channel_id,
                status="running",
                started_at=now_iso(),
                clear_error=True,
                frame_count=0,
                event_count=0,
            )

            try:
                while not stop_event.is_set():
                    ok, frame = cap.read()
                    if not ok:
                        time.sleep(0.2)
                        continue

                    frame_count += 1
                    pre_event_buffer.append((time.time(), self._pack_alarm_video_frame(frame)))
                    self._update_runtime(
                        channel_id,
                        last_analysis_at=now_iso(),
                        frame_count=frame_count,
                        event_count=event_count,
                    )
                    runtime_by_model: dict[str, dict] = {}
                    for model_config in model_configs:
                        model_id = model_config["model_id"]
                        frame_interval = max(int(model_config.get("frame_interval", 10) or 10), 1)
                        if frame_count % frame_interval != 0:
                            continue

                        model, model_meta = self._load_model(model_id)
                        conf_threshold = float(model_config.get("conf", 0.25) or 0.25)
                        save_snapshot = bool(model_config.get("save_snapshot", True))
                        detections = self._detect_frame(model, model_meta, frame, conf_threshold)
                        detections = self._apply_model_scoped_filters(detections, channel, model_config)

                        runtime_by_model[model_id] = {
                            "last_analysis_at": now_iso(),
                            "detections": len(detections),
                        }

                        # 调试统计能力先保留实现痕迹，暂时停用
                        # scoped_channel = self._build_scoped_channel(channel, model_config)
                        # raw_detections = self._detect_frame(model, model_meta, frame, conf_threshold)
                        # label_filtered_detections = self._filter_detections_by_labels(raw_detections, scoped_channel)
                        # zone_filtered_detections = self._filter_detections_by_zones(label_filtered_detections, scoped_channel)
                        #
                        # model_runtime = {
                        #     "last_analysis_at": now_iso(),
                        #     "detections": len(zone_filtered_detections),
                        #     "raw_detections": len(raw_detections),
                        #     "label_filtered_detections": len(label_filtered_detections),
                        #     "zone_filtered_detections": len(zone_filtered_detections),
                        #     "cooldown_blocked_detections": 0,
                        #     "event_emitted_detections": 0,
                        #     "configured_labels": list(model_config.get("labels") or []),
                        #     "configured_zone_ids": list(model_config.get("zone_ids") or []),
                        #     "raw_classes": self._summarize_classes(raw_detections),
                        #     "label_filtered_classes": self._summarize_classes(label_filtered_detections),
                        #     "zone_filtered_classes": self._summarize_classes(zone_filtered_detections),
                        #     "debug_reason": "ready",
                        # }
                        #
                        # if not raw_detections:
                        #     model_runtime["debug_reason"] = "模型未检出目标"
                        # elif not label_filtered_detections:
                        #     model_runtime["debug_reason"] = "目标被类别过滤拦截"
                        # elif not zone_filtered_detections:
                        #     model_runtime["debug_reason"] = "目标被区域过滤拦截"
                        #
                        # runtime_by_model[model_id] = model_runtime

                        if not detections:
                            continue

                        for detection in detections:
                            cooldown_key = f"{model_id}::{self._event_cooldown_key_for_algorithm(model_config, detection)}"
                            now_ts = time.time()
                            alert_interval = max(int(model_config.get("alert_interval", 60) or 60), 0)
                            if alert_interval > 0 and now_ts - cooldown_cache.get(cooldown_key, 0) < alert_interval:
                                # runtime_by_model[model_id]["cooldown_blocked_detections"] += 1
                                # runtime_by_model[model_id]["debug_reason"] = "命中冷却时间，事件被抑制"
                                continue
                            cooldown_cache[cooldown_key] = now_ts

                            snapshot_path = self._save_event_snapshot(
                                channel_id,
                                frame,
                                [detection],
                                fallback_frame=self._latest_usable_frame(pre_event_buffer),
                            ) if save_snapshot else None
                            fps = 25.0
                            before_seconds = self._alarm_video_before_seconds()
                            after_seconds = self._alarm_video_after_seconds()
                            before_frames = list(pre_event_buffer)
                            payload = {
                                "channel_id": channel_id,
                                "model_id": model_id,
                                "event_type": "detection",
                                "class_name": detection.get("class_name", "unknown"),
                                "conf": detection.get("conf", 0),
                                "snapshot_path": snapshot_path,
                                "handling_status": 0,
                                "remark": json.dumps({
                                    **detection,
                                    "model_id": model_id,
                                }, ensure_ascii=False),
                                "status": "new",
                            }
                            event = asyncio.run(self.events_service.create_event(payload))
                            self._submit_channel_alarm_video_job(
                                channel_id=channel_id,
                                source_url=source_url,
                                event=event,
                                model_id=model_id,
                                detection=detection,
                                snapshot_path=snapshot_path,
                                before_frames=before_frames,
                                fps=fps,
                                before_seconds=before_seconds,
                                after_seconds=after_seconds,
                            )
                            event_count += 1
                            # runtime_by_model[model_id]["event_emitted_detections"] += 1
                            # runtime_by_model[model_id]["debug_reason"] = "已触发事件"
                            runtime_by_model.setdefault(model_id, {})["last_event_at"] = now_iso()
                            self._update_runtime(
                                channel_id,
                                last_event_at=now_iso(),
                                frame_count=frame_count,
                                event_count=event_count,
                            )

                    if runtime_by_model:
                        channel = self._load_channel(channel_id)
                        if channel:
                            runtime = channel.setdefault("ext", {}).setdefault("ai_runtime", {})
                            runtime["models"] = {
                                **runtime.get("models", {}),
                                **runtime_by_model,
                            }
                            # previous_models = runtime.get("models", {})
                            # merged_models = {}
                            # for model_id, model_runtime in runtime_by_model.items():
                            #     previous = previous_models.get(model_id, {})
                            #     merged_models[model_id] = {
                            #         **previous,
                            #         **model_runtime,
                            #         "total_event_emitted_detections": int(previous.get("total_event_emitted_detections", 0))
                            #         + int(model_runtime.get("event_emitted_detections", 0)),
                            #         "total_cooldown_blocked_detections": int(previous.get("total_cooldown_blocked_detections", 0))
                            #         + int(model_runtime.get("cooldown_blocked_detections", 0)),
                            #     }
                            # runtime["models"] = {
                            #     **previous_models,
                            #     **merged_models,
                            # }
                            self._save_channel(channel)
            finally:
                cap.release()
        except Exception as exc:
            self._update_runtime(channel_id, status="error", last_error=str(exc), stopped_at=now_iso())
        finally:
            with self._lock:
                self._workers.pop(channel_id, None)
            self._prune_model_cache()
            channel = self._load_channel(channel_id)
            if channel and channel.get("ext", {}).get("ai_runtime", {}).get("status") != "error":
                self._update_runtime(
                    channel_id,
                    status="stopped",
                    stopped_at=now_iso(),
                    frame_count=frame_count,
                    event_count=event_count,
                )

    async def start_channel_analysis(self, channel_id: str) -> dict | None:
        channel = self._load_channel(channel_id)
        if not channel:
            return None

        with self._lock:
            existing = self._workers.get(channel_id)
            if existing and existing["thread"].is_alive():
                return {
                    "channel_id": channel_id,
                    "status": "running",
                    "message": "AI analysis is already running",
                }

            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._run_worker,
                args=(channel_id, stop_event),
                daemon=True,
                name=f"analysis-{channel_id}",
            )
            self._workers[channel_id] = {
                "thread": thread,
                "stop_event": stop_event,
                "started_at": now_iso(),
            }
            thread.start()

        return {
            "channel_id": channel_id,
            "status": "starting",
            "started_at": self._workers[channel_id]["started_at"],
        }

    async def restart_channel_analysis(self, channel_id: str) -> dict | None:
        channel = self._load_channel(channel_id)
        if not channel:
            return None

        with self._lock:
            existing = self._workers.get(channel_id)
            if existing:
                existing["stop_event"].set()

        wait_deadline = time.time() + 5.0
        while time.time() < wait_deadline:
          with self._lock:
              existing = self._workers.get(channel_id)
              if not existing or not existing["thread"].is_alive():
                  break
          time.sleep(0.1)

        with self._lock:
            existing = self._workers.get(channel_id)
            if existing and existing["thread"].is_alive():
                raise RuntimeError("AI analysis worker did not stop in time")
            if existing and not existing["thread"].is_alive():
                self._workers.pop(channel_id, None)

        self._update_runtime(channel_id, status="restarting", stopped_at=now_iso())
        return await self.start_channel_analysis(channel_id)

    async def stop_channel_analysis(self, channel_id: str) -> dict | None:
        channel = self._load_channel(channel_id)
        if not channel:
            return None

        with self._lock:
            existing = self._workers.get(channel_id)
            if existing:
                existing["stop_event"].set()

        self._update_runtime(channel_id, status="stopping", stopped_at=now_iso())
        return {
            "channel_id": channel_id,
            "status": "stopping",
        }

    async def get_channel_analysis_status(self, channel_id: str) -> dict | None:
        channel = self._load_channel(channel_id)
        if not channel:
            return None

        runtime = channel.get("ext", {}).get("ai_runtime", {})
        with self._lock:
            worker = self._workers.get(channel_id)
            is_running = bool(worker and worker["thread"].is_alive())

        return {
            "channel_id": channel_id,
            "enabled": bool(channel.get("ext", {}).get("ai_config", {}).get("enabled")),
            "status": runtime.get("status", "running" if is_running else "stopped"),
            "is_running": is_running,
            "started_at": runtime.get("started_at"),
            "stopped_at": runtime.get("stopped_at"),
            "last_analysis_at": runtime.get("last_analysis_at"),
            "last_event_at": runtime.get("last_event_at"),
            "last_error": runtime.get("last_error"),
            "frame_count": runtime.get("frame_count", 0),
            "event_count": runtime.get("event_count", 0),
            "models": runtime.get("models", {}),
        }

    async def restore_enabled_channels(self) -> dict:
        restored = 0
        skipped = 0
        if settings.METADATA_DB_ENABLED:
            with SessionLocal() as session:
                channel_ids = [row[0] for row in session.query(ChannelMeta.id).all()]
            channels = [self._load_channel(channel_id) for channel_id in channel_ids if channel_id]
        else:
            channels = []
            for path in self.channels_dir.glob("*.json"):
                try:
                    channels.append(load_json(path))
                except Exception:
                    skipped += 1
                    continue

        for channel in channels:
            if not channel:
                skipped += 1
                continue
            ai_config = channel.get("ext", {}).get("ai_config", {})
            model_configs = [
                item for item in self._normalize_model_configs(channel)
                if item.get("enabled") and item.get("model_id")
            ]
            if not ai_config.get("enabled") or not model_configs:
                continue

            result = await self.start_channel_analysis(channel["id"])
            if result:
                restored += 1
            else:
                skipped += 1

        return {
            "restored": restored,
            "skipped": skipped,
        }

    def _run_task_worker(self, task_id: str, stop_event: threading.Event) -> None:
        frame_count = 0
        event_count = 0
        cooldown_cache: dict[str, float] = {}
        rule_state: dict[str, dict] = {}
        pre_event_buffer: deque[tuple[float, object]] = deque(maxlen=self._alarm_video_pre_event_buffer_size())
        capture = None
        read_fail_count = 0
        reconnect_count = 0

        try:
            task = self._load_task(task_id)
            if not task:
                raise ValueError("Task not found")

            source_url = task.get("source_url")
            if not source_url:
                raise ValueError("Task source_url is empty")

            algorithms = [
                item for item in self._normalize_task_algorithms(task)
                if item.get("enabled") and item.get("model_id")
            ]
            if not algorithms:
                raise ValueError("Task has no enabled algorithms")

            capture = self._build_task_capture(source_url)
            warmup_seconds = self._task_analysis_startup_warmup_seconds()
            warmup_until_ts = time.time() + warmup_seconds if warmup_seconds > 0 else 0.0

            self._update_task_runtime(
                task_id,
                status="running",
                started_at=now_iso(),
                stopped_at=None,
                clear_error=True,
                frame_count=0,
                event_count=0,
            )

            try:
                while not stop_event.is_set():
                    ok, frame = self._read_task_frame(capture)
                    if not ok:
                        read_fail_count += 1
                        if read_fail_count >= TASK_ANALYSIS_READ_FAIL_LIMIT:
                            reconnect_count += 1
                            self._release_task_capture(capture)
                            capture = None
                            last_error = f"task stream stalled, reconnect={reconnect_count}"
                            self._update_task_runtime(task_id, last_error=last_error)
                            if reconnect_count >= 10:
                                raise RuntimeError("Task source read failed repeatedly")
                            time.sleep(min(2 ** min(reconnect_count, 3), 5))
                            task = self._load_task(task_id) or task
                            source_url = task.get("source_url") or source_url
                            capture = self._build_task_capture(source_url)
                            warmup_until_ts = time.time() + warmup_seconds if warmup_seconds > 0 else 0.0
                            read_fail_count = 0
                        else:
                            time.sleep(0.2)
                        continue

                    recovered_after_stall = reconnect_count > 0
                    read_fail_count = 0
                    if recovered_after_stall:
                        self._update_task_runtime(task_id, clear_error=True)
                    reconnect_count = 0
                    frame_count += 1
                    now_ts = time.time()
                    is_warming_up = warmup_until_ts > 0 and now_ts < warmup_until_ts
                    if is_warming_up:
                        self._update_task_runtime(
                            task_id,
                            last_analysis_at=now_iso(),
                            frame_count=frame_count,
                            event_count=event_count,
                        )
                        continue
                    pre_event_buffer.append((now_ts, self._pack_alarm_video_frame(frame)))

                    runtime_by_model: dict[str, dict] = {}
                    task = self._load_task(task_id) or task
                    algorithms = [
                        item for item in self._normalize_task_algorithms(task)
                        if item.get("enabled") and item.get("model_id")
                    ]
                    inference_results = self._collect_task_algorithm_inferences(
                        task=task,
                        algorithms=algorithms,
                        frame=frame,
                        frame_count=frame_count,
                    )

                    for result in inference_results:
                        algorithm = result["algorithm"]
                        model_id = result["model_id"]
                        algorithm_id = result["algorithm_id"]
                        runtime_key = self._task_runtime_model_key(algorithm)
                        detections = list(result.get("detections") or [])
                        rule_type = result["rule_type"]
                        error = result.get("error")

                        runtime_by_model[runtime_key] = {
                            "algorithm_id": algorithm_id,
                            "model_id": model_id,
                            "last_analysis_at": now_iso(),
                            "detections": len(detections),
                        }
                        if error:
                            runtime_by_model[runtime_key]["last_error"] = error
                            continue

                        if rule_type == "count":
                            self._update_count_metric(task, algorithm, detections, now_ts)
                            runtime_by_model[runtime_key]["metric_value"] = len(detections)
                            runtime_by_model[runtime_key]["metric_type"] = self._metric_type_for_algorithm(algorithm)
                            continue

                        rule_events = self._build_rule_events(
                            task=task,
                            algorithm=algorithm,
                            detections=detections,
                            now_ts=now_ts,
                            state=rule_state,
                        )
                        if not rule_events:
                            continue

                        for detection in rule_events:
                            cooldown_key = f"{runtime_key}::{self._event_cooldown_key_for_algorithm(algorithm, detection)}"
                            alert_interval = max(int(algorithm.get("alert_interval", 60) or 60), 0)
                            if alert_interval > 0 and now_ts - cooldown_cache.get(cooldown_key, 0) < alert_interval:
                                continue
                            cooldown_cache[cooldown_key] = now_ts

                            snapshot_detections = list(detection.get("snapshot_detections") or [detection])
                            snapshot_regions = detection.get("snapshot_regions")
                            if snapshot_regions is None and detection.get("absence_seconds") is not None:
                                snapshot_regions = list(algorithm.get("regions") or [])
                            snapshot_path = self._save_event_snapshot(
                                task_id,
                                frame,
                                snapshot_detections,
                                regions=snapshot_regions,
                                fallback_frame=self._latest_usable_frame(pre_event_buffer),
                            ) if algorithm.get("need_snapshot", True) else None
                            payload = self._build_task_event_payload(
                                task_id=task_id,
                                task=task,
                                algorithm=algorithm,
                                detection=detection,
                                snapshot_path=snapshot_path,
                            )
                            event = asyncio.run(self.events_service.create_event(payload))

                            if algorithm.get("need_video_clip", True):
                                fps = 25.0
                                before_seconds = max(
                                    int(
                                        algorithm.get(
                                            "video_before_seconds",
                                            self._alarm_video_before_seconds(),
                                        )
                                        or self._alarm_video_before_seconds()
                                    ),
                                    0,
                                )
                                after_seconds = max(
                                    int(
                                        algorithm.get(
                                            "video_after_seconds",
                                            self._alarm_video_after_seconds(),
                                        )
                                        or self._alarm_video_after_seconds()
                                    ),
                                    0,
                                )
                                before_frames = [
                                    item for item in list(pre_event_buffer)
                                    if now_ts - item[0] <= before_seconds
                                ]
                                self._submit_task_alarm_video_job(
                                    task_id=task_id,
                                    task_channel_id=task.get("channel_id"),
                                    source_url=source_url,
                                    event=event,
                                    algorithm=algorithm,
                                    model_id=model_id,
                                    detection=detection,
                                    snapshot_path=snapshot_path,
                                    before_frames=before_frames,
                                    fps=fps,
                                    before_seconds=before_seconds,
                                    after_seconds=after_seconds,
                                )

                            event_count += 1
                            runtime_by_model.setdefault(runtime_key, {})["last_event_at"] = now_iso()
                            self._update_task_runtime(
                                task_id,
                                last_event_at=now_iso(),
                                last_analysis_at=now_iso(),
                                frame_count=frame_count,
                                event_count=event_count,
                            )

                    if runtime_by_model:
                        self._update_task_runtime(
                            task_id,
                            last_analysis_at=now_iso(),
                            frame_count=frame_count,
                            event_count=event_count,
                            models={
                                **((self._load_task(task_id) or {}).get("runtime", {}).get("models", {})),
                                **runtime_by_model,
                            },
                        )
                    else:
                        self._update_task_runtime(
                            task_id,
                            last_analysis_at=now_iso(),
                            frame_count=frame_count,
                            event_count=event_count,
                        )
            finally:
                self._release_task_capture(capture)
        except Exception as exc:
            self._update_task_runtime(task_id, status="error", last_error=str(exc), stopped_at=now_iso())
        finally:
            with self._lock:
                self._task_workers.pop(task_id, None)
            self._prune_model_cache()
            task = self._load_task(task_id)
            if task and task.get("runtime", {}).get("status") != "error":
                self._update_task_runtime(
                    task_id,
                    status="stopped",
                    stopped_at=now_iso(),
                    frame_count=frame_count,
                    event_count=event_count,
                )

    async def start_task_analysis(self, task_id: str) -> dict | None:
        task = self._load_task(task_id)
        if not task:
            return None

        with self._lock:
            existing = self._task_workers.get(task_id)
            if existing and existing["thread"].is_alive():
                return {
                    "task_id": task_id,
                    "status": "running",
                    "message": "Task analysis is already running",
                }

            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._run_task_worker,
                args=(task_id, stop_event),
                daemon=True,
                name=f"task-analysis-{task_id}",
            )
            self._task_workers[task_id] = {
                "thread": thread,
                "stop_event": stop_event,
                "started_at": now_iso(),
            }
            thread.start()

        return {
            "task_id": task_id,
            "status": "starting",
            "started_at": self._task_workers[task_id]["started_at"],
        }

    async def restart_task_analysis(self, task_id: str) -> dict | None:
        task = self._load_task(task_id)
        if not task:
            return None

        with self._lock:
            existing = self._task_workers.get(task_id)
            if existing:
                existing["stop_event"].set()

        wait_deadline = time.time() + 5.0
        while time.time() < wait_deadline:
            with self._lock:
                existing = self._task_workers.get(task_id)
                if not existing or not existing["thread"].is_alive():
                    break
            time.sleep(0.1)

        with self._lock:
            existing = self._task_workers.get(task_id)
            if existing and existing["thread"].is_alive():
                raise RuntimeError("Task analysis worker did not stop in time")
            if existing and not existing["thread"].is_alive():
                self._task_workers.pop(task_id, None)

        self._update_task_runtime(task_id, status="restarting", stopped_at=now_iso())
        return await self.start_task_analysis(task_id)

    async def stop_task_analysis(self, task_id: str) -> dict | None:
        task = self._load_task(task_id)
        if not task:
            return None

        with self._lock:
            existing = self._task_workers.get(task_id)
            if existing:
                existing["stop_event"].set()

        self._update_task_runtime(task_id, status="stopping", stopped_at=now_iso())
        return {
            "task_id": task_id,
            "status": "stopping",
        }

    async def get_task_analysis_status(self, task_id: str) -> dict | None:
        task = self._load_task(task_id)
        if not task:
            return None

        runtime = task.get("runtime", {})
        with self._lock:
            worker = self._task_workers.get(task_id)
            is_running = bool(worker and worker["thread"].is_alive())

        current_status = runtime.get("status", "running" if is_running else "stopped")
        if not is_running and current_status in {"starting", "running", "restarting", "stopping"}:
            self._update_task_runtime(
                task_id,
                status="stopped",
                stopped_at=runtime.get("stopped_at") or now_iso(),
            )
            task = self._load_task(task_id) or task
            runtime = task.get("runtime", {})
            current_status = runtime.get("status", "stopped")

        return {
            "task_id": task_id,
            "status": current_status,
            "is_running": is_running,
            "started_at": runtime.get("started_at"),
            "stopped_at": runtime.get("stopped_at"),
            "last_analysis_at": runtime.get("last_analysis_at"),
            "last_event_at": runtime.get("last_event_at"),
            "last_error": runtime.get("last_error"),
            "frame_count": runtime.get("frame_count", 0),
            "event_count": runtime.get("event_count", 0),
            "models": runtime.get("models", {}),
        }

    async def stop_all(self, timeout_seconds: float = 5.0) -> dict:
        safe_timeout = max(float(timeout_seconds or 0), 0.0)
        with self._lock:
            workers = [
                *self._workers.values(),
                *self._task_workers.values(),
            ]

        for worker in workers:
            stop_event = worker.get("stop_event")
            if stop_event:
                stop_event.set()

        deadline = time.time() + safe_timeout
        while time.time() < deadline:
            with self._lock:
                running_workers = [
                    worker
                    for worker in [
                        *self._workers.values(),
                        *self._task_workers.values(),
                    ]
                    if worker.get("thread") and worker["thread"].is_alive()
                ]
            if not running_workers:
                break
            await asyncio.sleep(0.1)

        with self._lock:
            remaining_workers = [
                worker
                for worker in [
                    *self._workers.values(),
                    *self._task_workers.values(),
                ]
                if worker.get("thread") and worker["thread"].is_alive()
            ]
            self._workers = {
                channel_id: worker
                for channel_id, worker in self._workers.items()
                if worker.get("thread") and worker["thread"].is_alive()
            }
            self._task_workers = {
                task_id: worker
                for task_id, worker in self._task_workers.items()
                if worker.get("thread") and worker["thread"].is_alive()
            }

        remaining_timeout = max(deadline - time.time(), 0.0)
        pending_alarm_video_jobs, _ = await self._wait_for_alarm_video_jobs(remaining_timeout)
        self.clear_model_cache()
        return {
            "stopped": len(workers) - len(remaining_workers),
            "remaining": len(remaining_workers),
            "pending_alarm_video_jobs": pending_alarm_video_jobs,
        }


analysis_service = AnalysisService()
