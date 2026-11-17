from __future__ import annotations

import subprocess
import threading
import time
from typing import Any

import cv2
import numpy as np

from config import settings


class FFmpegReadError(RuntimeError):
    """Raised when FFmpeg cannot produce a decodable frame."""


class FFmpegReader:
    def __init__(
        self,
        source_url: str,
        *,
        ffmpeg_bin: str | None = None,
        timeout_seconds: float | None = None,
    ):
        self.source_url = str(source_url).strip()
        self.ffmpeg_bin = ffmpeg_bin or settings.ffmpeg_bin
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else settings.ffmpeg_timeout_seconds
        )

    def read_first_frame(self) -> np.ndarray:
        if not self.source_url:
            raise FFmpegReadError("empty source URL")

        command = [self.ffmpeg_bin, "-hide_banner", "-loglevel", "error"]
        if self.source_url.lower().startswith("rtsp://"):
            command.extend(
                [
                    "-rtsp_transport",
                    "tcp",
                    "-fflags",
                    "+genpts+discardcorrupt",
                    "-flags",
                    "low_delay",
                    "-max_delay",
                    "500000",
                ]
            )
        command.extend(
            [
                "-i",
                self.source_url,
                "-an",
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "pipe:1",
            ]
        )
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise FFmpegReadError(f"ffmpeg not found: {self.ffmpeg_bin}") from exc
        except subprocess.TimeoutExpired as exc:
            raise FFmpegReadError("ffmpeg frame read timed out") from exc

        if completed.returncode != 0 or not completed.stdout:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise FFmpegReadError(
                f"ffmpeg failed with return code {completed.returncode}: {detail[-500:]}"
            )
        frame = cv2.imdecode(
            np.frombuffer(completed.stdout, dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        if frame is None or frame.size == 0:
            raise FFmpegReadError("ffmpeg returned an undecodable frame")
        return frame

    def diagnostics(self) -> dict[str, Any]:
        return {
            "source_url": self.source_url,
            "ffmpeg_bin": self.ffmpeg_bin,
            "timeout_seconds": self.timeout_seconds,
        }


class FFmpegStreamReader:
    """Continuously decode MJPEG frames from one channel's source candidates."""

    def __init__(
        self,
        sources: list[tuple[str, str]],
        *,
        frame_buffer: Any,
        ffmpeg_bin: str | None = None,
        process_factory: Any | None = None,
        reconnect_delay_seconds: float = 1.0,
        max_reconnect_delay_seconds: float | None = None,
        read_chunk_size: int = 64 * 1024,
    ):
        self.sources = [
            (str(name).strip(), str(url).strip())
            for name, url in sources
            if str(url).strip()
        ]
        self.frame_buffer = frame_buffer
        self.ffmpeg_bin = ffmpeg_bin or settings.ffmpeg_bin
        self.process_factory = process_factory or self._default_process_factory
        self.reconnect_delay_seconds = max(float(reconnect_delay_seconds), 0.0)
        self.max_reconnect_delay_seconds = max(
            float(
                max_reconnect_delay_seconds
                if max_reconnect_delay_seconds is not None
                else settings.stream_reconnect_max_delay_seconds
            ),
            self.reconnect_delay_seconds,
        )
        self._reconnect_delay_seconds = self.reconnect_delay_seconds
        self.read_chunk_size = max(int(read_chunk_size), 1024)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: Any | None = None
        self._lock = threading.Lock()
        self._connected = False
        self._source_name: str | None = None
        self._last_frame_at: float | None = None
        self._last_error: str | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            if not self.sources:
                raise ValueError("at least one stream source is required")
            self._stop_event.clear()
            self._reconnect_delay_seconds = self.reconnect_delay_seconds
            self._thread = threading.Thread(
                target=self._run,
                name="camera-health-ffmpeg-reader",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            process = self._process
        if process is not None:
            self._terminate_process(process)

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    def status(self) -> dict[str, Any]:
        with self._lock:
            last_frame_at = self._last_frame_at
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "connected": self._connected,
                "source_name": self._source_name,
                "last_frame_at": last_frame_at,
                "last_error": self._last_error,
                "reconnect_delay_seconds": self._reconnect_delay_seconds,
            }

    def _run(self) -> None:
        source_index = 0
        while not self._stop_event.is_set():
            if not self.sources:
                break
            source_name, source_url = self.sources[source_index]
            source_index = (source_index + 1) % len(self.sources)
            process = None
            with self._lock:
                frame_before = self._last_frame_at
            try:
                command = self._build_command(source_name, source_url)
                process = self.process_factory(command)
                with self._lock:
                    self._process = process
                    self._source_name = source_name
                    self._connected = True
                    self._last_error = None
                self._read_process(process)
            except Exception as exc:
                with self._lock:
                    self._last_error = str(exc)
            finally:
                if process is not None:
                    self._terminate_process(process)
                with self._lock:
                    if self._process is process:
                        self._process = None
                    self._connected = False

            if not self._stop_event.is_set():
                with self._lock:
                    frame_after = self._last_frame_at
                    if frame_after is not None and frame_after != frame_before:
                        self._reconnect_delay_seconds = self.reconnect_delay_seconds
                    else:
                        self._reconnect_delay_seconds = min(
                            max(
                                self._reconnect_delay_seconds * 2,
                                self.reconnect_delay_seconds,
                            ),
                            self.max_reconnect_delay_seconds,
                        )
                    delay = self._reconnect_delay_seconds
                self._stop_event.wait(delay)

        with self._lock:
            self._connected = False
            self._process = None

    def _read_process(self, process: Any) -> None:
        pending = bytearray()
        while not self._stop_event.is_set():
            chunk = process.stdout.read(self.read_chunk_size)
            if not chunk:
                return
            pending.extend(chunk)
            while True:
                start = pending.find(b"\xff\xd8")
                if start < 0:
                    if len(pending) > self.read_chunk_size:
                        del pending[:-2]
                    break
                end = pending.find(b"\xff\xd9", start + 2)
                if end < 0:
                    if start:
                        del pending[:start]
                    break
                jpeg = bytes(pending[start : end + 2])
                del pending[: end + 2]
                frame = cv2.imdecode(
                    np.frombuffer(jpeg, dtype=np.uint8),
                    cv2.IMREAD_COLOR,
                )
                if frame is None or frame.size == 0:
                    continue
                captured_at = time.time()
                self.frame_buffer.put(frame, captured_at=captured_at)
                with self._lock:
                    self._last_frame_at = captured_at

    def _build_command(self, source_name: str, source_url: str) -> list[str]:
        command = [self.ffmpeg_bin, "-hide_banner", "-loglevel", "error"]
        if source_name == "rtsp" or source_url.lower().startswith("rtsp://"):
            command.extend(
                [
                    "-rtsp_transport",
                    "tcp",
                    "-fflags",
                    "+genpts+discardcorrupt",
                    "-flags",
                    "low_delay",
                    "-max_delay",
                    "500000",
                ]
            )
        command.extend(
            [
                "-i",
                source_url,
                "-an",
                "-f",
                "mjpeg",
                "-q:v",
                "5",
                "pipe:1",
            ]
        )
        return command

    @staticmethod
    def _default_process_factory(command: list[str]) -> Any:
        return subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

    @staticmethod
    def _terminate_process(process: Any) -> None:
        try:
            process.terminate()
            process.wait(timeout=1.0)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
