import asyncio
import logging
import queue
import subprocess
import sys
import threading
import time
from copy import deepcopy
from pathlib import Path

import cv2
import numpy as np

from src.core.settings import settings
from src.services.platform_storage import load_json, now_iso, save_json


logger = logging.getLogger("uvicorn.error")

ZONE_PREVIEW_PUSH_QUEUE_TIMEOUT_SECONDS = 0.1
ZONE_PREVIEW_WORKER_MONITOR_INTERVAL_SECONDS = 0.1
ZONE_PREVIEW_STOP_JOIN_TIMEOUT_SECONDS = 0.5
ZONE_PREVIEW_EXPECTED_SHUTDOWN_ERROR_KEYWORDS = (
    "broken pipe",
    "error writing trailer",
    "error closing file",
    "failed to update header",
    "error muxing a packet",
    "task finished with error code: -32",
)


class FFmpegCapture:
    def __init__(self, source_url: str, width: int, height: int, init_wait: float = 1.0):
        self.source_url = source_url
        self.width = width
        self.height = height
        self.frame_size = width * height * 3
        self.process: subprocess.Popen | None = None
        self._pending_frame_bytes = bytearray()
        self.start()
        if init_wait > 0:
            time.sleep(init_wait)

    def start(self):
        self._pending_frame_bytes.clear()
        cmd = [
            settings.FFMPEG_BIN,
            "-loglevel",
            "error",
        ]
        if str(self.source_url).lower().startswith("rtsp://"):
            cmd.extend([
                "-rtsp_transport",
                "tcp",
                "-fflags",
                "nobuffer+discardcorrupt",
                "-flags",
                "low_delay",
                "-max_delay",
                "500000",
                "-probesize",
                "32",
                "-analyzeduration",
                "0",
            ])
        cmd.extend([
            "-i",
            self.source_url,
            "-an",
            "-c:v",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-f",
            "rawvideo",
            "pipe:1",
        ])

        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=10**8,
            **kwargs,
        )

    def read(self, retry: int = 3):
        for _ in range(retry):
            if self.process is None or self.process.poll() is not None or self.process.stdout is None:
                return False, None
            try:
                while len(self._pending_frame_bytes) < self.frame_size:
                    raw = self.process.stdout.read(self.frame_size - len(self._pending_frame_bytes))
                    if len(raw) == 0:
                        break
                    self._pending_frame_bytes.extend(raw)
            except Exception as exc:
                logger.warning("FFmpegCapture read error: %s", exc)
                return False, None

            if len(self._pending_frame_bytes) >= self.frame_size:
                raw = bytes(self._pending_frame_bytes[: self.frame_size])
                del self._pending_frame_bytes[: self.frame_size]
                frame = np.frombuffer(raw, dtype=np.uint8).reshape((self.height, self.width, 3)).copy()
                return True, frame
            time.sleep(0.05)
        return False, None

    def release(self):
        if self.process is None:
            return
        try:
            self.process.kill()
            self.process.wait(timeout=2)
        except Exception:
            pass
        finally:
            self.process = None
            self._pending_frame_bytes.clear()

    def is_healthy(self) -> bool:
        return self.process is not None and self.process.poll() is None


class ZonePreviewService:
    def __init__(self):
        self.channels_dir: Path = settings.CHANNELS_DIR
        self._lock = threading.Lock()
        self._workers: dict[str, dict] = {}
        self._channel_cache: dict[str, dict] = {}

    def _get_channel_path(self, channel_id: str) -> Path:
        return self.channels_dir / f"{channel_id}.json"

    def _load_channel(self, channel_id: str) -> dict | None:
        path = self._get_channel_path(channel_id)
        if not path.exists():
            return self._channel_cache.get(channel_id)
        try:
            channel = load_json(path)
        except FileNotFoundError:
            logger.warning("[ZonePreview] channel file disappeared while reading: %s", path)
            return self._channel_cache.get(channel_id)
        except Exception as exc:
            logger.warning("[ZonePreview] failed to read channel file %s: %s", path, exc)
            return self._channel_cache.get(channel_id)
        self._channel_cache[channel_id] = channel
        return channel

    def _save_channel(self, channel: dict) -> None:
        channel["updated_at"] = now_iso()
        self._channel_cache[channel["id"]] = channel
        save_json(self._get_channel_path(channel["id"]), channel)

    def _update_runtime(
        self,
        channel_id: str,
        *,
        enabled: bool | None = None,
        status: str | None = None,
        stream: str | None = None,
        source: str | None = None,
        target: str | None = None,
        urls: dict | None = None,
        last_error: str | None = None,
        started_at: str | None = None,
        stopped_at: str | None = None,
        zones: list[dict] | None = None,
        zone_source: str | None = None,
        owner_task_id: str | None = None,
        clear_error: bool = False,
    ) -> bool:
        channel = self._load_channel(channel_id)
        if not channel:
            return False

        runtime = channel.setdefault("ext", {}).setdefault("zone_preview", {})
        original_runtime = deepcopy(runtime)
        if enabled is not None:
            runtime["enabled"] = enabled
        if status is not None:
            runtime["status"] = status
        if stream is not None:
            runtime["stream"] = stream
        if source is not None:
            runtime["source"] = source
        if target is not None:
            runtime["target"] = target
        if urls is not None:
            runtime["urls"] = urls
        if zones is not None:
            runtime["zones"] = zones
            runtime["zone_count"] = len(zones)
        if zone_source is not None:
            runtime["zone_source"] = zone_source
        if owner_task_id is not None:
            runtime["owner_task_id"] = owner_task_id
        if clear_error:
            runtime["last_error"] = None
        elif last_error is not None:
            runtime["last_error"] = last_error
        if started_at is not None:
            runtime["started_at"] = started_at
        if stopped_at is not None:
            runtime["stopped_at"] = stopped_at
        if runtime == original_runtime:
            return False
        self._save_channel(channel)
        return True

    @staticmethod
    def _normalize_zones(zones: list[dict] | None) -> list[dict]:
        normalized: list[dict] = []
        for zone in list(zones or []):
            if not isinstance(zone, dict):
                continue
            normalized.append(
                {
                    "id": zone.get("id"),
                    "name": zone.get("name"),
                    "type": zone.get("type"),
                    "color": zone.get("color"),
                    "full_frame": bool(zone.get("full_frame")),
                    "coordinates": zone.get("coordinates") or [],
                }
            )
        return normalized

    def _can_reuse_running_worker(
        self,
        channel_id: str,
        *,
        source_url: str,
        stream: str,
        target: str,
        zones: list[dict],
        zone_source: str | None,
        owner_task_id: str | None,
    ) -> tuple[bool, dict]:
        current = self.status(channel_id) or {}
        if str(current.get("status") or "").lower() != "running":
            return False, {"reason": "status_not_running", "current_status": current.get("status")}
        if not current.get("process_alive"):
            return False, {"reason": "process_not_alive"}

        channel = self._load_channel(channel_id)
        if not channel:
            return False, {"reason": "channel_not_found"}
        runtime = channel.get("ext", {}).get("zone_preview", {}) or {}
        with self._lock:
            worker = self._workers.get(channel_id) or {}

        current_zone_source = runtime.get("zone_source") or worker.get("zone_source")
        current_owner_task_id = runtime.get("owner_task_id") or worker.get("owner_task_id")
        current_source = runtime.get("source") or worker.get("source")
        current_stream = runtime.get("stream") or worker.get("stream")
        current_target = runtime.get("target") or worker.get("target")
        current_zones = runtime.get("zones") or worker.get("zones") or []

        normalized_current_zones = self._normalize_zones(current_zones)
        normalized_expected_zones = self._normalize_zones(zones)
        comparisons = {
            "source_match": str(current_source or "") == str(source_url or ""),
            "stream_match": str(current_stream or "") == str(stream or ""),
            "target_match": str(current_target or "") == str(target or ""),
            "zone_source_match": str(current_zone_source or "") == str(zone_source or ""),
            "owner_task_id_match": str(current_owner_task_id or "") == str(owner_task_id or ""),
            "zones_match": normalized_current_zones == normalized_expected_zones,
        }
        reusable = all(comparisons.values())
        return reusable, {
            "reason": "matched" if reusable else "config_changed",
            "comparisons": comparisons,
            "current": {
                "source": current_source,
                "stream": current_stream,
                "target": current_target,
                "zone_source": current_zone_source,
                "owner_task_id": current_owner_task_id,
                "zones": normalized_current_zones,
            },
            "expected": {
                "source": source_url,
                "stream": stream,
                "target": target,
                "zone_source": zone_source,
                "owner_task_id": owner_task_id,
                "zones": normalized_expected_zones,
            },
        }

    def _probe_stream(self, source_url: str) -> tuple[int, int, int]:
        cap = cv2.VideoCapture(source_url)
        if not cap.isOpened():
            raise ValueError(f"无法打开区域流输入源: {source_url}")

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = int(cap.get(cv2.CAP_PROP_FPS) or 25) or 25

        if width <= 0 or height <= 0:
            ok, frame = cap.read()
            if not ok or frame is None:
                cap.release()
                raise ValueError("无法读取区域流首帧")
            height, width = frame.shape[:2]

        cap.release()
        return width, height, fps

    def _draw_zones(self, frame: np.ndarray, zones: list[dict]) -> np.ndarray:
        output = frame.copy()
        frame_height, frame_width = frame.shape[:2]
        for zone in zones:
            if zone.get("type") == "fullscreen" or zone.get("full_frame"):
                color_hex = (zone.get("color") or "#3b82f6").lstrip("#")
                try:
                    rgb = tuple(int(color_hex[i:i + 2], 16) for i in (0, 2, 4)) if len(color_hex) == 6 else (59, 130, 246)
                except Exception:
                    rgb = (59, 130, 246)
                bgr = (rgb[2], rgb[1], rgb[0])
                cv2.rectangle(output, (0, 0), (max(frame_width - 1, 0), max(frame_height - 1, 0)), bgr, 3)
                continue

            coordinates = zone.get("coordinates") or []
            if len(coordinates) < 2:
                continue

            points = []
            for point in coordinates:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                points.append((int(point[0]), int(point[1])))
            if len(points) < 2:
                continue

            pts = np.array(points, dtype=np.int32).reshape((-1, 1, 2))
            color_hex = (zone.get("color") or "#3b82f6").lstrip("#")
            try:
                rgb = tuple(int(color_hex[i:i + 2], 16) for i in (0, 2, 4)) if len(color_hex) == 6 else (59, 130, 246)
            except Exception:
                rgb = (59, 130, 246)
            bgr = (rgb[2], rgb[1], rgb[0])

            overlay = output.copy()
            cv2.fillPoly(overlay, [pts], bgr)
            output = cv2.addWeighted(overlay, 0.16, output, 0.84, 0)
            cv2.polylines(output, [pts], isClosed=True, color=bgr, thickness=2)
        return output

    def _start_ffmpeg_stderr_logger(
        self,
        proc: subprocess.Popen,
        stderr_lines: "queue.Queue[str]",
        stderr_lock: threading.Lock,
        channel_id: str,
        stop_event: threading.Event,
    ) -> None:
        def _stderr_reader():
            if proc.stderr is None:
                return
            try:
                for raw_line in proc.stderr:
                    line = raw_line.decode("utf-8", errors="replace").strip() if isinstance(raw_line, bytes) else str(raw_line).strip()
                    if not line:
                        continue
                    with stderr_lock:
                        if stderr_lines.full():
                            try:
                                stderr_lines.get_nowait()
                            except queue.Empty:
                                pass
                        stderr_lines.put_nowait(line)
                    lowered = line.lower()
                    if stop_event.is_set() and any(keyword in lowered for keyword in ZONE_PREVIEW_EXPECTED_SHUTDOWN_ERROR_KEYWORDS):
                        continue
                    logger.warning("[ZonePreview][FFmpeg:%s] %s", channel_id, line)
            except Exception as exc:
                logger.debug("[ZonePreview] stderr logger stopped for %s: %s", channel_id, exc)

        threading.Thread(
            target=_stderr_reader,
            name=f"ZonePreviewErr-{channel_id}",
            daemon=True,
        ).start()

    def _get_ffmpeg_error_summary(self, stderr_lines: "queue.Queue[str]", stderr_lock: threading.Lock) -> str:
        with stderr_lock:
            lines = list(stderr_lines.queue)
        if not lines:
            return "no stderr captured"
        return " | ".join(lines[-3:])

    def _start_pusher(
        self,
        width: int,
        height: int,
        fps: int,
        target: str,
        stderr_lines: "queue.Queue[str]",
        stderr_lock: threading.Lock,
        channel_id: str,
        stop_event: threading.Event,
    ) -> subprocess.Popen:
        cmd = [
            settings.FFMPEG_BIN,
            "-y",
            "-loglevel",
            "warning",
            "-fflags",
            "+genpts+discardcorrupt",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{width}x{height}",
            "-r",
            str(fps),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-g",
            str(max(int(fps), 1)),
            "-keyint_min",
            str(max(int(fps), 1)),
            "-sc_threshold",
            "0",
            "-pix_fmt",
            "yuv420p",
            "-f",
            "flv",
            target,
        ]

        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            bufsize=0,
            **kwargs,
        )
        self._start_ffmpeg_stderr_logger(proc, stderr_lines, stderr_lock, channel_id, stop_event)
        return proc

    def _stop_pusher(self, proc: subprocess.Popen | None) -> None:
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=1)
            return
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=1)
            return
        except Exception:
            pass
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass

    async def _wait_until_started(
        self,
        channel_id: str,
        timeout_seconds: float = 0.8,
        interval_seconds: float = 0.05,
    ) -> dict | None:
        deadline = time.perf_counter() + max(timeout_seconds, 0)
        last_status = self.status(channel_id)
        while time.perf_counter() < deadline:
            if not last_status:
                return None
            normalized = str(last_status.get("status") or "").lower()
            if normalized in {"running", "error"}:
                return last_status
            await asyncio.sleep(interval_seconds)
            last_status = self.status(channel_id)
        return last_status

    def _run_worker(
        self,
        channel_id: str,
        source_url: str,
        stream: str,
        target: str,
        stop_event: threading.Event,
        zones: list[dict] | None = None,
    ) -> None:
        frame_queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=8)
        stderr_lines: "queue.Queue[str]" = queue.Queue(maxsize=20)
        stderr_lock = threading.Lock()
        fatal_error: dict[str, str | None] = {"message": None}

        capture: FFmpegCapture | None = None
        push_proc: subprocess.Popen | None = None
        ffmpeg_restart_times: list[float] = []
        width = height = fps = 0

        def stop_capture():
            nonlocal capture
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    pass
                capture = None

        def restart_pusher():
            nonlocal push_proc
            if stop_event.is_set():
                return
            now = time.time()
            ffmpeg_restart_times.append(now)
            ffmpeg_restart_times[:] = [ts for ts in ffmpeg_restart_times if now - ts <= 10]
            if len(ffmpeg_restart_times) > 8:
                raise RuntimeError(
                    f"区域流 FFmpeg 在 10 秒内重启超过 8 次，最近错误: {self._get_ffmpeg_error_summary(stderr_lines, stderr_lock)}"
                )
            self._stop_pusher(push_proc)
            time.sleep(1.5)
            push_proc = self._start_pusher(width, height, fps, target, stderr_lines, stderr_lock, channel_id, stop_event)

        def read_loop():
            nonlocal capture
            bad_count = 0
            reconnect_count = 0
            explicit_runtime_zones = zones is not None
            runtime_zones = list(zones or [])
            cached_zones = runtime_zones if explicit_runtime_zones else ((self._load_channel(channel_id) or {}).get("ext", {}).get("zones", []) or [])

            while not stop_event.is_set():
                if capture is None or not capture.is_healthy():
                    try:
                        stop_capture()
                        time.sleep(min(2 ** min(reconnect_count, 3), 5))
                        capture = FFmpegCapture(source_url, width, height, init_wait=0.5)
                        bad_count = 0
                    except Exception as exc:
                        reconnect_count += 1
                        logger.warning("[ZonePreview] %s 重连拉流失败(%s): %s", channel_id, reconnect_count, exc)
                        if reconnect_count >= 10:
                            fatal_error["message"] = f"区域流连续重连失败: {exc}"
                            stop_event.set()
                        continue

                ok, frame = capture.read()
                if not ok or frame is None:
                    bad_count += 1
                    if bad_count > 10:
                        logger.warning("[ZonePreview] %s 连续读帧失败，准备重建拉流", channel_id)
                        stop_capture()
                        reconnect_count += 1
                        bad_count = 0
                    continue

                reconnect_count = 0
                bad_count = 0
                current_channel = self._load_channel(channel_id) or {}
                runtime = current_channel.get("ext", {}).get("zone_preview", {}) or {}
                if "zones" in runtime:
                    cached_zones = list(runtime.get("zones") or [])
                elif explicit_runtime_zones:
                    cached_zones = runtime_zones
                else:
                    cached_zones = list(current_channel.get("ext", {}).get("zones", []) or [])
                rendered = self._draw_zones(frame, cached_zones)

                if frame_queue.full():
                    try:
                        frame_queue.get_nowait()
                    except queue.Empty:
                        pass
                try:
                    frame_queue.put_nowait(rendered)
                except queue.Full:
                    pass

        def push_loop():
            nonlocal push_proc
            while not stop_event.is_set():
                try:
                    frame = frame_queue.get(timeout=ZONE_PREVIEW_PUSH_QUEUE_TIMEOUT_SECONDS)
                except queue.Empty:
                    continue

                try:
                    if push_proc is None or push_proc.poll() is not None:
                        if stop_event.is_set():
                            break
                        logger.warning(
                            "[ZonePreview] %s 推流进程已退出，准备重启。最近错误: %s",
                            channel_id,
                            self._get_ffmpeg_error_summary(stderr_lines, stderr_lock),
                        )
                        restart_pusher()
                        if stop_event.is_set():
                            break

                    if push_proc is None or push_proc.stdin is None:
                        raise RuntimeError("区域流推流进程不可用")
                    push_proc.stdin.write(frame.tobytes())
                except BrokenPipeError:
                    if stop_event.is_set():
                        break
                    logger.warning(
                        "[ZonePreview] %s 推流 BrokenPipe，准备重启。最近错误: %s",
                        channel_id,
                        self._get_ffmpeg_error_summary(stderr_lines, stderr_lock),
                    )
                    restart_pusher()
                except Exception as exc:
                    if stop_event.is_set():
                        break
                    logger.error(
                        "[ZonePreview] %s 推流异常: %s，最近错误: %s",
                        channel_id,
                        exc,
                        self._get_ffmpeg_error_summary(stderr_lines, stderr_lock),
                    )
                    restart_pusher()

        try:
            width, height, fps = self._probe_stream(source_url)
            capture = FFmpegCapture(source_url, width, height, init_wait=0.5)
            push_proc = self._start_pusher(width, height, fps, target, stderr_lines, stderr_lock, channel_id, stop_event)

            self._update_runtime(
                channel_id,
                enabled=True,
                status="running",
                stream=stream,
                source=source_url,
                target=target,
                started_at=now_iso(),
                clear_error=True,
            )

            reader = threading.Thread(target=read_loop, name=f"ZonePreviewRead-{channel_id}", daemon=True)
            writer = threading.Thread(target=push_loop, name=f"ZonePreviewPush-{channel_id}", daemon=True)
            reader.start()
            writer.start()

            while not stop_event.is_set():
                if fatal_error["message"]:
                    raise RuntimeError(fatal_error["message"])
                if not reader.is_alive():
                    raise RuntimeError(
                        f"区域流读帧线程异常退出，最近推流错误: {self._get_ffmpeg_error_summary(stderr_lines, stderr_lock)}"
                    )
                if not writer.is_alive():
                    raise RuntimeError(
                        f"区域流推流线程异常退出，最近推流错误: {self._get_ffmpeg_error_summary(stderr_lines, stderr_lock)}"
                    )
                time.sleep(ZONE_PREVIEW_WORKER_MONITOR_INTERVAL_SECONDS)

        except Exception as exc:
            logger.warning(
                "[ZonePreviewExit] channel_id=%s reason=worker_exception error=%s",
                channel_id,
                str(exc),
            )
            self._update_runtime(
                channel_id,
                enabled=False,
                status="error",
                last_error=str(exc),
                stopped_at=now_iso(),
            )
        finally:
            stop_event.set()
            stop_capture()
            self._stop_pusher(push_proc)
            with self._lock:
                self._workers.pop(channel_id, None)

    async def start(
        self,
        channel_id: str,
        source_url: str,
        stream: str,
        target: str,
        urls: dict | None = None,
        zones: list[dict] | None = None,
        zone_source: str | None = None,
        owner_task_id: str | None = None,
    ):
        started_at = time.perf_counter()
        channel = self._load_channel(channel_id)
        if not channel:
            return None
        explicit_zones = zones is not None
        effective_zones = list(zones or [])
        if not explicit_zones and not effective_zones:
            effective_zones = list(channel.get("ext", {}).get("zones", []) or [])

        effective_zone_source = zone_source or ("task" if explicit_zones else "channel")
        can_reuse, reuse_detail = self._can_reuse_running_worker(
            channel_id,
            source_url=source_url,
            stream=stream,
            target=target,
            zones=effective_zones,
            zone_source=effective_zone_source,
            owner_task_id=owner_task_id,
        )
        logger.info(
            "[ZonePreviewReuse] channel_id=%s reuse_hit=%s reason=%s",
            channel_id,
            can_reuse,
            reuse_detail.get("reason"),
        )
        if can_reuse:
            self._update_runtime(
                channel_id,
                enabled=True,
                status="running",
                stream=stream,
                source=source_url,
                target=target,
                urls=urls or {},
                zones=effective_zones,
                zone_source=effective_zone_source,
                owner_task_id=owner_task_id,
                clear_error=True,
            )
            result = self.status(channel_id)
            logger.debug(
                "[ZonePreviewWorker] reuse channel_id=%s zone_count=%s zone_source=%s owner_task_id=%s elapsed_ms=%s status=%s",
                channel_id,
                len(effective_zones),
                effective_zone_source,
                owner_task_id,
                round((time.perf_counter() - started_at) * 1000, 2),
                (result or {}).get("status"),
            )
            return result
        logger.debug(
            "[ZonePreviewWorker] reuse-skip channel_id=%s reason=%s comparisons=%s",
            channel_id,
            reuse_detail.get("reason"),
            reuse_detail.get("comparisons"),
        )

        stop_started_at = time.perf_counter()
        await self.stop(channel_id)
        stop_elapsed_ms = round((time.perf_counter() - stop_started_at) * 1000, 2)

        stop_event = threading.Event()
        worker = threading.Thread(
            target=self._run_worker,
            args=(channel_id, source_url, stream, target, stop_event, effective_zones),
            daemon=True,
        )

        with self._lock:
            self._workers[channel_id] = {
                "thread": worker,
                "stop_event": stop_event,
                "stream": stream,
                "source": source_url,
                "target": target,
                "zones": effective_zones,
                "zone_source": effective_zone_source,
                "owner_task_id": owner_task_id,
            }

        self._update_runtime(
            channel_id,
            enabled=True,
            status="starting",
            stream=stream,
            source=source_url,
            target=target,
            urls=urls or {},
            zones=effective_zones,
            zone_source=effective_zone_source,
            owner_task_id=owner_task_id,
            started_at=now_iso(),
            clear_error=True,
        )
        worker_started_at = time.perf_counter()
        worker.start()
        worker_launch_elapsed_ms = round((time.perf_counter() - worker_started_at) * 1000, 2)
        wait_started_at = time.perf_counter()
        result = await self._wait_until_started(channel_id)
        wait_elapsed_ms = round((time.perf_counter() - wait_started_at) * 1000, 2)
        total_elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        logger.debug(
            "[ZonePreviewWorker] start channel_id=%s zone_count=%s zone_source=%s owner_task_id=%s stop_elapsed_ms=%s worker_launch_elapsed_ms=%s wait_elapsed_ms=%s elapsed_ms=%s status=%s",
            channel_id,
            len(effective_zones),
            effective_zone_source,
            owner_task_id,
            stop_elapsed_ms,
            worker_launch_elapsed_ms,
            wait_elapsed_ms,
            total_elapsed_ms,
            (result or {}).get("status"),
        )
        return result

    async def stop(self, channel_id: str):
        with self._lock:
            worker = self._workers.get(channel_id)
        if worker:
            worker["stop_event"].set()
            thread = worker.get("thread")
            if thread and thread.is_alive():
                thread.join(timeout=ZONE_PREVIEW_STOP_JOIN_TIMEOUT_SECONDS)

        channel = self._load_channel(channel_id)
        if not channel:
            return None

        runtime = channel.setdefault("ext", {}).setdefault("zone_preview", {})
        runtime["enabled"] = False
        runtime["status"] = "stopped"
        runtime["stopped_at"] = now_iso()
        self._save_channel(channel)
        return self.status(channel_id)

    async def stop_all(self):
        with self._lock:
            channel_ids = list(self._workers.keys())
        for channel_id in channel_ids:
            try:
                await self.stop(channel_id)
            except Exception as exc:
                logger.warning("[ZonePreviewShutdown] channel_id=%s stop_all_error=%s", channel_id, exc)

    def status(self, channel_id: str):
        channel = self._load_channel(channel_id)
        if not channel:
            return None
        runtime = channel.get("ext", {}).get("zone_preview", {}) or {}
        with self._lock:
            worker = self._workers.get(channel_id)
        process_alive = bool(worker and worker.get("thread") and worker["thread"].is_alive())
        runtime_status = runtime.get("status")
        if runtime_status == "running" and not process_alive:
            logger.warning(
                "[ZonePreviewOrphaned] channel_id=%s reason=runtime_running_but_worker_missing owner_task_id=%s stream=%s",
                channel_id,
                runtime.get("owner_task_id"),
                runtime.get("stream"),
            )
            self._update_runtime(
                channel_id,
                enabled=False,
                status="stopped",
                stopped_at=runtime.get("stopped_at") or now_iso(),
            )
            channel = self._load_channel(channel_id) or channel
            runtime = channel.get("ext", {}).get("zone_preview", {}) or {}
            runtime_status = "stopped"
        return {
            "channel_id": channel_id,
            "enabled": bool(runtime.get("enabled")) and process_alive,
            "status": runtime_status or ("running" if process_alive else "stopped"),
            "stream": runtime.get("stream"),
            "source": runtime.get("source"),
            "target": runtime.get("target"),
            "urls": runtime.get("urls") or {},
            "last_error": runtime.get("last_error"),
            "started_at": runtime.get("started_at"),
            "stopped_at": runtime.get("stopped_at"),
            "process_alive": process_alive,
            "zone_count": int(runtime.get("zone_count") or len(runtime.get("zones") or [])),
            "zone_source": runtime.get("zone_source"),
            "owner_task_id": runtime.get("owner_task_id"),
        }


zone_preview_service = ZonePreviewService()
