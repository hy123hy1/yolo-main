from __future__ import annotations

import copy
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from baseline.baseline_manager import BaselineManager
from config import HEALTH_DETECTOR_THRESHOLDS, settings
from detectors import DetectorResult
from detectors.analysis_cases import analysis_case_for
from detectors.blur import BlurDetector
from detectors.exposure import ExposureDetector
from detectors.freeze import FreezeDetector
from detectors.occlusion import OcclusionDetector
from detectors.resolution import ResolutionDetector
from detectors.shake import ShakeDetector
from detectors.view_shift import ViewShiftDetector
from detectors.video_loss import VideoLossDetector
from state.health_state_machine import HealthStateMachine
from stream.ffmpeg_reader import FFmpegStreamReader
from stream.frame_buffer import FrameBuffer
from storage.repository import CameraHealthRepository


logger = logging.getLogger(__name__)
metrics_logger = logging.getLogger("uvicorn.error")


class WVPChannelClient:
    def __init__(
        self,
        *,
        api_base: str | None = None,
        channel_list_path: str | None = None,
        channel_play_path: str | None = None,
        timeout_seconds: float | None = None,
        retry_attempts: int | None = None,
        retry_backoff_seconds: float | None = None,
    ):
        self.api_base = str(api_base or settings.wvp_api_base).rstrip("/")
        self.channel_list_path = channel_list_path or settings.wvp_channel_list_path
        self.channel_play_path = channel_play_path or settings.wvp_channel_play_path
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else settings.wvp_timeout_seconds
        )
        self.retry_attempts = max(
            int(
                retry_attempts
                if retry_attempts is not None
                else settings.wvp_retry_attempts
            ),
            1,
        )
        self.retry_backoff_seconds = max(
            float(
                retry_backoff_seconds
                if retry_backoff_seconds is not None
                else settings.wvp_retry_backoff_seconds
            ),
            0.0,
        )

    def list_channels(
        self,
        page: int = 1,
        count: int = 100,
        query: str | None = None,
    ) -> dict[str, Any]:
        if not self.api_base:
            return {"enabled": False, "items": [], "total": 0}
        payload = self._request_json(
            self.channel_list_path,
            {"page": page, "count": count, "query": query},
        )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        items = (
            data.get("list")
            or data.get("items")
            or payload.get("list")
            or payload.get("items")
            or []
        )
        total = data.get("total") or payload.get("total") or len(items)
        return {"enabled": True, "items": items, "total": total}

    def get_play_info(self, gb_id: str) -> dict[str, Any]:
        if not self.api_base:
            return {}
        payload = self._request_json(
            self.channel_play_path,
            {"channelId": gb_id},
        )
        return payload.get("data") if isinstance(payload.get("data"), dict) else {}

    def _request_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        route = path if path.startswith("/") else f"/{path}"
        url = f"{self.api_base}{route}"
        if params:
            query = urlencode(
                {key: value for key, value in params.items() if value is not None}
            )
            url = f"{url}?{query}"
        request = Request(url, headers={"Accept": "application/json"})
        for attempt in range(self.retry_attempts):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = response.read()
                return json.loads(payload.decode("utf-8")) if payload else {}
            except (OSError, TimeoutError):
                if attempt + 1 >= self.retry_attempts:
                    raise
                delay = self.retry_backoff_seconds * (2**attempt)
                if delay:
                    time.sleep(delay)
        return {}


class WVPChannelSyncService:
    def __init__(
        self,
        *,
        repository: CameraHealthRepository,
        client: WVPChannelClient,
        baseline_manager: BaselineManager | None = None,
        enqueue_refresh: Callable[[dict[str, Any]], Any] | None = None,
        worker_manager: Any | None = None,
    ):
        self.repository = repository
        self.client = client
        self.baseline_manager = baseline_manager or BaselineManager()
        self.worker_manager = worker_manager
        self._executor: ThreadPoolExecutor | None = None
        if enqueue_refresh is None:
            self._executor = ThreadPoolExecutor(
                max_workers=4,
                thread_name_prefix="camera-health-sync",
            )
            self.enqueue_refresh = lambda request: self._executor.submit(
                self.refresh_play_info,
                request,
            )
        else:
            self.enqueue_refresh = enqueue_refresh

    def sync_channels(
        self,
        page: int = 1,
        count: int = 100,
        query: str | None = None,
    ) -> dict[str, Any]:
        remote, remote_items, pagination_complete = self._fetch_all_channels(
            page=page,
            count=count,
            query=query,
        )
        synced_items: list[dict[str, Any]] = []
        refresh_requests: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        active_gb_ids: set[str] = set()
        failures.extend(remote.get("failures") or [])

        for remote_item in remote_items:
            gb_id = self.extract_gb_id(remote_item)
            if not gb_id:
                failures.append({"reason": "missing gb_id", "item": str(remote_item)})
                continue
            active_gb_ids.add(gb_id)
            try:
                channel = self.repository.upsert_channel(
                    {
                        "gb_id": gb_id,
                        "device_id": self.extract_device_id(remote_item),
                        "name": self.extract_name(remote_item, gb_id),
                        "type": "wvp",
                        "is_online": self.extract_online(remote_item),
                    }
                )
                synced_items.append(channel)
                request = {
                    "channel_id": channel["id"],
                    "gb_id": gb_id,
                    "remote_item": copy.deepcopy(remote_item),
                }
                refresh_requests.append(request)
                self.enqueue_refresh(request)
            except Exception as exc:
                logger.exception("WVP channel sync failed gb_id=%s", gb_id)
                failures.append({"gb_id": gb_id, "reason": str(exc)})

        can_cleanup = bool(
            remote.get("enabled", False)
            and pagination_complete
            and query is None
            and page == 1
            and not failures
        )
        missing_offline = (
            self.repository.mark_missing_offline(active_gb_ids)
            if can_cleanup
            else 0
        )
        stopped_workers = 0
        if can_cleanup and self.worker_manager is not None:
            active_channel_ids = {
                str(item["id"]).strip()
                for item in synced_items
                if item.get("id")
            }
            stopped_workers = self.worker_manager.stop_missing(active_channel_ids)
        return {
            "enabled": bool(remote.get("enabled", False)),
            "synced": len(synced_items),
            "total": remote.get("total", len(remote_items)),
            "items": synced_items,
            "failures": failures,
            "missing_cleanup": {
                "marked_offline": missing_offline,
                "deleted": stopped_workers,
            },
            "play_info_refresh": {
                "mode": "background",
                "queued": len(refresh_requests),
            },
        }

    def _fetch_all_channels(
        self,
        *,
        page: int,
        count: int,
        query: str | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
        first = self.client.list_channels(page=page, count=count, query=query)
        remote = dict(first or {})
        if not remote.get("enabled", False):
            return remote, [], False
        total_raw = remote.get("total")
        try:
            total = max(int(total_raw), 0)
        except (TypeError, ValueError):
            total = 0
        page_size = max(int(count), 1)
        expected_pages = max((total + page_size - 1) // page_size, 1)
        items_by_gb_id: dict[str, dict[str, Any]] = {}
        pagination_complete = True
        current_page = page
        current = list(remote.get("items") or [])
        pages_seen = 0
        while True:
            pages_seen += 1
            for item in current:
                if isinstance(item, dict):
                    gb_id = self.extract_gb_id(item)
                    if gb_id:
                        items_by_gb_id[gb_id] = item
            if pages_seen >= expected_pages or not current:
                if total > len(items_by_gb_id) and pages_seen >= expected_pages:
                    pagination_complete = False
                break
            current_page += 1
            try:
                next_page = self.client.list_channels(
                    page=current_page,
                    count=count,
                    query=query,
                )
                current = list((next_page or {}).get("items") or [])
            except Exception as exc:
                remote.setdefault("failures", []).append(
                    {"reason": f"pagination failed: {exc}", "page": str(current_page)}
                )
                pagination_complete = False
                break
        if pages_seen < expected_pages:
            pagination_complete = False
        remote["total"] = total or len(items_by_gb_id)
        return remote, list(items_by_gb_id.values()), pagination_complete

    def refresh_play_info(self, request: dict[str, Any]) -> dict[str, Any] | None:
        channel_id = str(request.get("channel_id") or "").strip()
        gb_id = str(request.get("gb_id") or "").strip()
        if not channel_id or not gb_id:
            return None
        try:
            play_info = self.client.get_play_info(gb_id)
            source_url = self.pick_source_url(play_info)
            updated = self.repository.update_playback(
                channel_id,
                stream=self.extract_stream(play_info),
                source_url=source_url,
                flv=str(play_info.get("flv") or "").strip() or None,
                is_online=bool(source_url or self.extract_online(play_info)),
                error=None if play_info else "WVP play endpoint returned no data",
            )
            if not updated:
                return None
            if updated.get("baseline_required"):
                try:
                    baseline = self.baseline_manager.ensure(updated, force=True)
                    updated = (
                        self.repository.update_snapshot_path(
                            channel_id,
                            baseline["snapshot_path"],
                        )
                        or updated
                    )
                    if self.worker_manager is not None:
                        self.worker_manager.reload_baseline(
                            channel_id,
                            updated["snapshot_path"],
                        )
                except Exception as exc:
                    logger.warning(
                        "baseline capture failed channel_id=%s error=%s",
                        channel_id,
                        exc,
                    )
            if self.worker_manager is not None:
                self.worker_manager.reconcile(updated)
            return updated
        except Exception as exc:
            logger.warning(
                "WVP play refresh failed channel_id=%s error=%s",
                channel_id,
                exc,
            )
            self.repository.update_playback(
                channel_id,
                is_online=False,
                error=str(exc),
            )
            return None

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    @staticmethod
    def extract_gb_id(item: dict[str, Any]) -> str:
        return str(
            item.get("gb_id")
            or item.get("gbId")
            or item.get("id")
            or item.get("channelId")
            or item.get("channel_id")
            or ""
        ).strip()

    @staticmethod
    def extract_device_id(item: dict[str, Any]) -> str | None:
        return (
            str(
                item.get("device_id")
                or item.get("deviceId")
                or item.get("gbDeviceId")
                or ""
            ).strip()
            or None
        )

    @staticmethod
    def extract_name(item: dict[str, Any], fallback: str) -> str:
        return str(
            item.get("name")
            or item.get("gbName")
            or item.get("channelName")
            or fallback
        ).strip()

    @staticmethod
    def extract_stream(play_info: dict[str, Any]) -> str | None:
        media_info = play_info.get("mediaInfo") or {}
        return (
            str(
                play_info.get("stream")
                or play_info.get("streamId")
                or play_info.get("stream_id")
                or media_info.get("stream")
                or media_info.get("streamId")
                or media_info.get("stream_id")
                or ""
            ).strip()
            or None
        )

    @staticmethod
    def pick_source_url(play_info: dict[str, Any]) -> str | None:
        for key in ("rtsp", "rtmp", "hls", "fmp4", "rtc", "flv"):
            value = str(play_info.get(key) or "").strip()
            if value:
                return value
        return None

    @staticmethod
    def extract_online(item: dict[str, Any]) -> bool:
        for key in ("online", "isOnline", "gbOnline", "is_online"):
            value = item.get(key)
            if value is True or value == 1:
                return True
            if isinstance(value, str) and value.strip().lower() in {
                "true",
                "1",
                "on",
                "online",
            }:
                return True
        return False


class ChannelWorker:
    """Run one channel's stream reader and health analyzers independently."""

    ALGORITHM_NAMES = (
        "occlusion",
        "view_shift",
        "blur",
        "exposure",
        "freeze",
        "video_loss",
        "shake",
        "resolution",
    )

    def __init__(
        self,
        channel: dict[str, Any],
        *,
        repository: CameraHealthRepository | None = None,
        baseline_manager: BaselineManager | None = None,
        reader_factory: Callable[..., Any] = FFmpegStreamReader,
        frame_buffer: FrameBuffer | None = None,
        detectors: dict[str, Any] | None = None,
        analysis_interval_seconds: float | None = None,
        frame_timeout_seconds: float | None = None,
        alarm_hold_seconds: float | None = None,
        recovery_hold_seconds: float | None = None,
        baseline_stable_frames: int | None = None,
        detector_intervals: dict[str, float] | None = None,
    ):
        self.channel = dict(channel)
        self.repository = repository
        self.baseline_manager = baseline_manager or BaselineManager()
        self.frame_buffer = frame_buffer or FrameBuffer()
        self.reader_factory = reader_factory
        self.analysis_interval_seconds = max(
            float(
                analysis_interval_seconds
                if analysis_interval_seconds is not None
                else settings.analysis_interval_seconds
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
        self.baseline_stable_frames = max(
            int(
                baseline_stable_frames
                if baseline_stable_frames is not None
                else settings.baseline_stable_frames
            ),
            1,
        )
        self.detectors = detectors or self._build_detectors()
        configured_intervals = detector_intervals or settings.health_detector_intervals
        self.detector_intervals = {
            name: max(
                float(configured_intervals.get(name, self.analysis_interval_seconds)),
                0.001,
            )
            for name in self.ALGORITHM_NAMES
        }
        self.state_machines = {
            name: HealthStateMachine(
                alarm_hold_seconds=(
                    (
                        alarm_hold_seconds
                        if alarm_hold_seconds is not None
                        else settings.alarm_hold_seconds
                    )
                    if name != "video_loss"
                    else settings.video_loss_alarm_hold_seconds
                ),
                recovery_hold_seconds=(
                    (
                        recovery_hold_seconds
                        if recovery_hold_seconds is not None
                        else settings.recovery_hold_seconds
                    )
                    if name != "video_loss"
                    else settings.video_loss_recovery_hold_seconds
                ),
            )
            for name in self.ALGORITHM_NAMES
        }
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._reader: Any | None = None
        self._lock = threading.Lock()
        self._running = False
        self._baseline: Any | None = self._load_baseline()
        self._stable_frame_count = 0
        self._baseline_candidates: list[Any] = []
        self._baseline_previous_frame: Any | None = None
        self._baseline_calibration_started_at: float | None = None
        self._last_analysis_at: float | None = None
        self._last_detector_analysis_at: dict[str, float | None] = {
            name: None for name in self.ALGORITHM_NAMES
        }
        self._restore_health_state()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            sources = self._source_candidates()
            if not sources:
                raise ValueError("channel source is not configured")
            self._reader = self.reader_factory(
                sources,
                frame_buffer=self.frame_buffer,
            )
            self._stop_event.clear()
            self._reader.start()
            self._running = True
            self._thread = threading.Thread(
                target=self._analyzer_loop,
                name=f"camera-health-analyzer-{self.channel.get('id')}",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        reader = self._reader
        if reader is not None:
            reader.stop()
        with self._lock:
            self._running = False

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        reader = self._reader
        if reader is not None:
            reader.join(timeout=timeout)

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = self._running and bool(
                self._thread and self._thread.is_alive()
            )
        result = {
            "channel_id": self.channel.get("id"),
            "running": running,
            "last_analysis_at": self._last_analysis_at,
            "baseline_available": self._baseline is not None,
            "detector_intervals": dict(self.detector_intervals),
            "detector_last_analysis_at": dict(self._last_detector_analysis_at),
        }
        result["reader"] = self._reader.status() if self._reader is not None else None
        return result

    def _analyzer_loop(self) -> None:
        while not self._stop_event.is_set():
            now = time.time()
            snapshot = self.frame_buffer.snapshot(now=now)
            frame = snapshot["frame"]
            captured_at = snapshot["captured_at"]
            stale = (
                frame is None
                or captured_at is None
                or snapshot["age_seconds"] > self.frame_timeout_seconds
            )
            if stale:
                if self._detector_due("video_loss", now):
                    self._last_detector_analysis_at["video_loss"] = now
                    self._handle_result(
                        "video_loss",
                        self.detectors["video_loss"].analyze(
                            None,
                            captured_at=captured_at,
                            now=now,
                        ),
                        now,
                        frame=None,
                    )
            else:
                self._analyze_frame(frame, captured_at, now)
            self._last_analysis_at = now
            self._stop_event.wait(self.analysis_interval_seconds)
        with self._lock:
            self._running = False

    def _analyze_frame(
        self,
        frame: Any,
        captured_at: float | None,
        now: float,
    ) -> None:
        results: dict[str, DetectorResult] = {}
        for name in self.ALGORITHM_NAMES:
            if not self._detector_due(name, now):
                continue
            self._last_detector_analysis_at[name] = now
            detector = self.detectors[name]
            try:
                if name in {"occlusion", "view_shift"}:
                    result = detector.analyze(frame, self._baseline)
                elif name == "video_loss":
                    result = detector.analyze(
                        frame,
                        captured_at=captured_at,
                        now=now,
                    )
                else:
                    result = detector.analyze(frame)
                results[name] = result
            except Exception as exc:
                logger.exception(
                    "health detector failed channel_id=%s algorithm=%s",
                    self.channel.get("id"),
                    name,
                )
                results[name] = DetectorResult(
                    is_bad=False,
                    score=0.0,
                    reason=f"detector_error: {exc}",
                    quality_status="unknown",
                )

        for name, result in results.items():
            self._handle_result(name, result, now, frame=frame)
        baseline_algorithms = ("occlusion", "blur", "exposure", "freeze")
        if all(name in results for name in baseline_algorithms):
            self._maybe_establish_baseline(frame, results)

    def _detector_due(self, algorithm_type: str, now: float) -> bool:
        previous = self._last_detector_analysis_at.get(algorithm_type)
        return previous is None or now - previous >= self.detector_intervals[algorithm_type]

    def _handle_result(
        self,
        algorithm_type: str,
        result: DetectorResult,
        now: float,
        *,
        frame: Any | None = None,
    ) -> None:
        transition = self.state_machines[algorithm_type].update(
            is_bad=result.is_bad,
            quality_status=result.quality_status,
            now=now,
            reason=result.reason,
            score=result.score,
            metrics=result.metrics,
        )
        if self.repository is None:
            return
        channel_id = str(self.channel.get("id") or "").strip()
        try:
            state_payload = {
                "channel_id": channel_id,
                "algorithm_type": algorithm_type,
                "status": transition.status,
                "current_score": result.score,
                "last_reason": result.reason,
                "last_metrics_json": result.metrics,
                "bad_started_at": transition.bad_started_at,
                "recovery_started_at": transition.recovery_started_at,
            }
            event_payload: dict[str, Any] | None = None
            if transition.create_event:
                analysis_case = analysis_case_for(
                    algorithm_type,
                    result.reason,
                    result.metrics,
                )
                if not analysis_case:
                    logger.warning(
                        "health event has no analysis case channel_id=%s algorithm=%s reason=%s",
                        channel_id,
                        algorithm_type,
                        result.reason,
                    )
                else:
                    event_path = None
                    if frame is not None:
                        try:
                            event_path = self.baseline_manager.save_event_frame(
                                channel_id,
                                analysis_case,
                                frame,
                            )["event_path"]
                        except Exception as exc:
                            logger.warning(
                                "health event snapshot failed channel_id=%s case=%s error=%s",
                                channel_id,
                                analysis_case,
                                exc,
                            )
                    event_payload = {
                        "analysis_case": analysis_case,
                        "status": "new",
                        "metrics_json": result.metrics,
                        "event_path": event_path,
                        "started_at": transition.bad_started_at or now,
                    }
            self.repository.persist_health_transition(
                state_payload,
                event_payload=event_payload,
                close_event=transition.close_event,
                ended_at=now,
            )
        except Exception:
            logger.exception(
                "health persistence failed channel_id=%s algorithm=%s",
                channel_id,
                algorithm_type,
            )

    def _maybe_establish_baseline(
        self,
        frame: Any,
        results: dict[str, DetectorResult],
    ) -> None:
        if self._baseline is not None:
            return
        stable = all(
            not results[name].is_bad
            for name in ("occlusion", "blur", "exposure", "freeze")
        )
        if not stable:
            self._reset_baseline_candidates()
            return

        if self._baseline_calibration_started_at is None:
            self._baseline_calibration_started_at = time.perf_counter()
        quality = self.baseline_manager.validate_frame(
            frame,
            previous_frame=self._baseline_previous_frame,
        )
        self._baseline_previous_frame = frame.copy()
        if quality.accepted:
            self._baseline_candidates.append(frame.copy())
            self._stable_frame_count += 1
        elif quality.reason == "frame_unstable":
            self._baseline_candidates = [frame.copy()]
            self._stable_frame_count = 1
            self._baseline_calibration_started_at = time.perf_counter()
            logger.info(
                "baseline candidate window restarted channel_id=%s reason=%s metrics=%s",
                self.channel.get("id"),
                quality.reason,
                quality.metrics,
            )
            return
        else:
            logger.info(
                "baseline candidate rejected channel_id=%s reason=%s metrics=%s",
                self.channel.get("id"),
                quality.reason,
                quality.metrics,
            )
            self._reset_baseline_candidates()
            return

        required_candidates = max(
            int(getattr(self.baseline_manager, "candidate_frames", 1)),
            1,
        )
        if (
            self._stable_frame_count < self.baseline_stable_frames
            or len(self._baseline_candidates) < required_candidates
        ):
            return
        try:
            selected_frame = self.baseline_manager.select_best_frame(
                self._baseline_candidates
            )
            result = self.baseline_manager.save_frame(
                str(self.channel.get("id") or ""),
                selected_frame,
            )
            import cv2

            self._baseline = cv2.imread(result["snapshot_path"])
            if self._baseline is None or not self._baseline.size:
                raise ValueError("saved baseline snapshot is unreadable")
            if self.repository is not None:
                self.repository.update_snapshot_path(
                    str(self.channel.get("id") or ""),
                    result["snapshot_path"],
                )
            elapsed_ms = (
                (time.perf_counter() - self._baseline_calibration_started_at) * 1000
                if self._baseline_calibration_started_at is not None
                else 0.0
            )
            metrics_logger.info(
                "automatic baseline generated channel_id=%s candidates=%d "
                "stable_rounds=%d generation_elapsed_ms=%.1f snapshot_path=%s",
                self.channel.get("id"),
                len(self._baseline_candidates),
                self._stable_frame_count,
                elapsed_ms,
                result["snapshot_path"],
            )
        except Exception:
            logger.exception(
                "stable baseline capture failed channel_id=%s",
                self.channel.get("id"),
            )
        finally:
            self._reset_baseline_candidates()

    def _reset_baseline_candidates(self) -> None:
        self._stable_frame_count = 0
        self._baseline_candidates.clear()
        self._baseline_previous_frame = None
        self._baseline_calibration_started_at = None

    def _load_baseline(self) -> Any | None:
        path = str(self.channel.get("snapshot_path") or "").strip()
        if not path or not Path(path).is_file():
            return None
        import cv2

        baseline = cv2.imread(path)
        return baseline if baseline is not None and baseline.size else None

    def reload_baseline(self, snapshot_path: str) -> None:
        """Replace the in-memory baseline after a successful persisted refresh."""
        path = Path(str(snapshot_path or "").strip())
        if not path.is_file():
            raise FileNotFoundError(f"baseline snapshot not found: {path}")
        import cv2

        baseline = cv2.imread(str(path))
        if baseline is None or not baseline.size:
            raise ValueError(f"baseline snapshot is unreadable: {path}")
        self._baseline = baseline

    def capture_baseline_from_buffer(self) -> dict[str, Any]:
        """Capture a new validated baseline from this Worker's live frame buffer."""
        freeze_status = self.state_machines["freeze"].status
        if freeze_status != "normal":
            raise RuntimeError(
                "Worker freeze detector is not healthy: "
                f"status={freeze_status}"
            )
        return self.baseline_manager.capture_from_frame_buffer(
            str(self.channel.get("id") or ""),
            self.frame_buffer,
            max_frame_age_seconds=self.frame_timeout_seconds,
            allow_frozen_frames=True,
        )

    def _restore_health_state(self) -> None:
        if self.repository is None:
            return
        channel_id = str(self.channel.get("id") or "").strip()
        if not channel_id:
            return
        try:
            context = self.repository.get_health_context(channel_id)
        except Exception:
            logger.exception("health state restore failed channel_id=%s", channel_id)
            return
        for algorithm_type, machine in self.state_machines.items():
            entry = context.get(algorithm_type) or {}
            state = entry.get("state") or {}
            event = entry.get("event")
            status = state.get("status") or "normal"
            bad_started_at = state.get("bad_started_at")
            recovery_started_at = state.get("recovery_started_at")
            event_started_at = None
            if event:
                status = "offline" if status == "offline" else "alarming"
                event_started_at = event.get("started_at")
                bad_started_at = bad_started_at or event_started_at
                recovery_started_at = None
            elif status in {"alarming", "offline"}:
                analysis_case = analysis_case_for(
                    algorithm_type,
                    state.get("last_reason"),
                    state.get("last_metrics_json") or {},
                )
                if analysis_case:
                    try:
                        event = self.repository.ensure_health_event_for_state(
                            channel_id,
                            algorithm_type,
                            analysis_case=analysis_case,
                        )
                        if event:
                            event_started_at = event.get("started_at")
                            bad_started_at = bad_started_at or event_started_at
                    except Exception:
                        logger.exception(
                            "health event repair failed channel_id=%s algorithm=%s",
                            channel_id,
                            algorithm_type,
                        )
            machine.restore(
                status=status,
                bad_started_at=bad_started_at,
                recovery_started_at=recovery_started_at,
                event_started_at=event_started_at,
            )

    def _source_candidates(self) -> list[tuple[str, str]]:
        source_url = str(self.channel.get("source_url") or "").strip()
        flv = str(self.channel.get("flv") or "").strip()
        sources: list[tuple[str, str]] = []
        if source_url:
            sources.append(("rtsp", source_url))
        if flv and flv != source_url:
            sources.append(("flv", flv))
        return sources

    @staticmethod
    def _build_detectors() -> dict[str, Any]:
        return {
            "occlusion": OcclusionDetector(HEALTH_DETECTOR_THRESHOLDS["occlusion"]),
            "view_shift": ViewShiftDetector(
                HEALTH_DETECTOR_THRESHOLDS["view_shift"]
            ),
            "blur": BlurDetector(HEALTH_DETECTOR_THRESHOLDS["blur"]),
            "exposure": ExposureDetector(HEALTH_DETECTOR_THRESHOLDS["exposure"]),
            "freeze": FreezeDetector(HEALTH_DETECTOR_THRESHOLDS["freeze"]),
            "video_loss": VideoLossDetector(
                HEALTH_DETECTOR_THRESHOLDS["video_loss"]
            ),
            "shake": ShakeDetector(HEALTH_DETECTOR_THRESHOLDS["shake"]),
            "resolution": ResolutionDetector(
                HEALTH_DETECTOR_THRESHOLDS["resolution"]
            ),
        }
