import asyncio
import logging
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from src.core.settings import settings
from src.services.platform_storage import load_json, now_iso, save_json


logger = logging.getLogger(__name__)


class FFmpegCapture:
    def __init__(self, source_url: str, width: int, height: int, init_wait: float = 1.0):
        self.source_url = source_url
        self.width = width
        self.height = height
        self.frame_size = width * height * 3
        self.process: subprocess.Popen | None = None
        self.start()
        if init_wait > 0:
            time.sleep(init_wait)

    def start(self):
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
                raw = self.process.stdout.read(self.frame_size)
            except Exception as exc:
                logger.warning("FFmpegCapture read error: %s", exc)
                return False, None

            if len(raw) == self.frame_size:
                frame = np.frombuffer(raw, dtype=np.uint8).reshape((self.height, self.width, 3)).copy()
                return True, frame
            if len(raw) == 0:
                time.sleep(0.05)
                continue
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
        clear_error: bool = False,
    ) -> None:
        channel = self._load_channel(channel_id)
        if not channel:
            return

        runtime = channel.setdefault("ext", {}).setdefault("zone_preview", {})
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
        if clear_error:
            runtime["last_error"] = None
        elif last_error is not None:
            runtime["last_error"] = last_error
        if started_at is not None:
            runtime["started_at"] = started_at
        if stopped_at is not None:
            runtime["stopped_at"] = stopped_at
        self._save_channel(channel)

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
        for zone in zones:
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
        self._start_ffmpeg_stderr_logger(proc, stderr_lines, stderr_lock, channel_id)
        return proc

    def _stop_pusher(self, proc: subprocess.Popen | None) -> None:
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass

    def _run_worker(
        self,
        channel_id: str,
        source_url: str,
        stream: str,
        target: str,
        stop_event: threading.Event,
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
            now = time.time()
            ffmpeg_restart_times.append(now)
            ffmpeg_restart_times[:] = [ts for ts in ffmpeg_restart_times if now - ts <= 10]
            if len(ffmpeg_restart_times) > 8:
                raise RuntimeError(
                    f"区域流 FFmpeg 在 10 秒内重启超过 8 次，最近错误: {self._get_ffmpeg_error_summary(stderr_lines, stderr_lock)}"
                )
            self._stop_pusher(push_proc)
            time.sleep(1.5)
            push_proc = self._start_pusher(width, height, fps, target, stderr_lines, stderr_lock, channel_id)

        def read_loop():
            nonlocal capture
            bad_count = 0
            reconnect_count = 0
            cached_zones = (self._load_channel(channel_id) or {}).get("ext", {}).get("zones", []) or []

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
                current_zones = current_channel.get("ext", {}).get("zones", []) or []
                if current_zones:
                    cached_zones = current_zones
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
                    frame = frame_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                try:
                    if push_proc is None or push_proc.poll() is not None:
                        logger.warning(
                            "[ZonePreview] %s 推流进程已退出，准备重启。最近错误: %s",
                            channel_id,
                            self._get_ffmpeg_error_summary(stderr_lines, stderr_lock),
                        )
                        restart_pusher()

                    if push_proc is None or push_proc.stdin is None:
                        raise RuntimeError("区域流推流进程不可用")
                    push_proc.stdin.write(frame.tobytes())
                except BrokenPipeError:
                    logger.warning(
                        "[ZonePreview] %s 推流 BrokenPipe，准备重启。最近错误: %s",
                        channel_id,
                        self._get_ffmpeg_error_summary(stderr_lines, stderr_lock),
                    )
                    restart_pusher()
                except Exception as exc:
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
            push_proc = self._start_pusher(width, height, fps, target, stderr_lines, stderr_lock, channel_id)

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
                time.sleep(1)

        except Exception as exc:
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

    async def start(self, channel_id: str, source_url: str, stream: str, target: str, urls: dict | None = None):
        channel = self._load_channel(channel_id)
        if not channel:
            return None
        if not (channel.get("ext", {}).get("zones") or []):
            raise ValueError("当前通道还没有配置区域")

        await self.stop(channel_id)

        stop_event = threading.Event()
        worker = threading.Thread(
            target=self._run_worker,
            args=(channel_id, source_url, stream, target, stop_event),
            daemon=True,
        )

        with self._lock:
            self._workers[channel_id] = {
                "thread": worker,
                "stop_event": stop_event,
                "stream": stream,
                "source": source_url,
                "target": target,
            }

        self._update_runtime(
            channel_id,
            enabled=True,
            status="starting",
            stream=stream,
            source=source_url,
            target=target,
            urls=urls or {},
            started_at=now_iso(),
            clear_error=True,
        )
        worker.start()
        await asyncio.sleep(2)
        return self.status(channel_id)

    async def stop(self, channel_id: str):
        with self._lock:
            worker = self._workers.get(channel_id)
        if worker:
            worker["stop_event"].set()
            thread = worker.get("thread")
            if thread and thread.is_alive():
                thread.join(timeout=4)

        channel = self._load_channel(channel_id)
        if not channel:
            return None

        runtime = channel.setdefault("ext", {}).setdefault("zone_preview", {})
        runtime["enabled"] = False
        runtime["status"] = "stopped"
        runtime["stopped_at"] = now_iso()
        self._save_channel(channel)
        return self.status(channel_id)

    def status(self, channel_id: str):
        channel = self._load_channel(channel_id)
        if not channel:
            return None
        runtime = channel.get("ext", {}).get("zone_preview", {}) or {}
        with self._lock:
            worker = self._workers.get(channel_id)
        return {
            "channel_id": channel_id,
            "enabled": bool(runtime.get("enabled")),
            "status": runtime.get("status") or ("running" if worker else "stopped"),
            "stream": runtime.get("stream"),
            "source": runtime.get("source"),
            "target": runtime.get("target"),
            "urls": runtime.get("urls") or {},
            "last_error": runtime.get("last_error"),
            "started_at": runtime.get("started_at"),
            "stopped_at": runtime.get("stopped_at"),
            "process_alive": bool(worker and worker.get("thread") and worker["thread"].is_alive()),
        }


zone_preview_service = ZonePreviewService()
