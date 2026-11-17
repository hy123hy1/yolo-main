from __future__ import annotations

import logging
import re
import subprocess
import sys
import threading
import time
from collections import deque

import numpy as np

from src.core.settings import settings


logger = logging.getLogger("uvicorn.error")

FFMPEG_STDERR_TAIL_LINES = 20
_URL_CREDENTIALS_PATTERN = re.compile(r"(rtsp://)([^/@\s]+):([^@\s]+)@", re.IGNORECASE)


def _redact_diagnostic_text(value: str) -> str:
    return _URL_CREDENTIALS_PATTERN.sub(r"\1***:***@", str(value))


class FFmpegCapture:
    def __init__(
        self,
        source_url: str,
        width: int,
        height: int,
        init_wait: float = 1.0,
        latest_only: bool = False,
    ):
        self.source_url = source_url
        self.width = width
        self.height = height
        self.frame_size = width * height * 3
        self._latest_only = bool(latest_only)
        self.process: subprocess.Popen | None = None
        self._pending_frame_bytes = bytearray()
        self._latest_condition = threading.Condition()
        self._latest_frame = None
        self._latest_frame_sequence = 0
        self._last_read_sequence = 0
        self._latest_frame_timestamp: float | None = None
        self._reader_error: str | None = None
        self._reader_stop_event = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_tail = deque(maxlen=FFMPEG_STDERR_TAIL_LINES)
        self._stderr_lock = threading.Lock()
        self._started_at = time.time()
        self._first_frame_at: float | None = None
        self.last_frame_timestamp: float | None = None
        self.last_frame_sequence = 0
        self.start()
        if init_wait > 0:
            time.sleep(init_wait)

    def start(self):
        self._pending_frame_bytes.clear()
        self._reader_error = None
        self._started_at = time.time()
        self._first_frame_at = None
        with self._stderr_lock:
            self._stderr_tail.clear()
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
                "+genpts+discardcorrupt",
                "-flags",
                "low_delay",
                "-max_delay",
                "500000",
                "-probesize",
                "5M",
                "-analyzeduration",
                "1M",
            ])
        cmd.extend([
            "-i",
            self.source_url,
            "-an",
            "-vf",
            f"scale={self.width}:{self.height}:flags=fast_bilinear",
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
            stderr=subprocess.PIPE,
            bufsize=10**8,
            **kwargs,
        )
        stderr_stream = getattr(self.process, "stderr", None)
        if stderr_stream is not None:
            self._stderr_thread = threading.Thread(
                target=self._collect_stderr,
                args=(stderr_stream,),
                name="FFmpegCaptureStderrReader",
                daemon=True,
            )
            self._stderr_thread.start()
        if getattr(self, "_latest_only", False):
            self._reader_stop_event.clear()
            self._reader_thread = threading.Thread(
                target=self._read_latest_frames,
                name="FFmpegCaptureLatestReader",
                daemon=True,
            )
            self._reader_thread.start()

    def _collect_stderr(self, stderr_stream) -> None:
        try:
            while True:
                line = stderr_stream.readline()
                if not line:
                    break
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                line = _redact_diagnostic_text(str(line).strip())
                if line:
                    with self._stderr_lock:
                        self._stderr_tail.append(line)
        except Exception as exc:
            self._reader_error = self._reader_error or f"stderr reader failed: {exc}"

    def _read_raw_frame(self):
        if self.process is None or self.process.stdout is None:
            return None

        raw_frame = bytearray()
        while len(raw_frame) < self.frame_size and not self._reader_stop_event.is_set():
            raw = self.process.stdout.read(self.frame_size - len(raw_frame))
            if len(raw) == 0:
                return None
            raw_frame.extend(raw)

        if len(raw_frame) < self.frame_size:
            return None
        return np.frombuffer(raw_frame, dtype=np.uint8).reshape((self.height, self.width, 3)).copy()

    def _store_latest_frame(self, frame, timestamp: float) -> None:
        with self._latest_condition:
            if self._reader_stop_event.is_set():
                return
            self._latest_frame = frame
            self._latest_frame_sequence += 1
            self._latest_frame_timestamp = float(timestamp)
            if getattr(self, "_first_frame_at", None) is None:
                self._first_frame_at = time.time()
            self._latest_condition.notify_all()

    def _read_latest_frames(self) -> None:
        try:
            while not self._reader_stop_event.is_set():
                frame = self._read_raw_frame()
                if frame is None:
                    if not self._reader_stop_event.is_set():
                        process = self.process
                        returncode = process.poll() if process is not None else None
                        self._reader_error = (
                            f"ffmpeg stdout closed; returncode={returncode}"
                        )
                    break
                self._store_latest_frame(frame, time.time())
        except Exception as exc:
            self._reader_error = str(exc)
            logger.warning("FFmpeg latest-frame reader stopped: %s", exc)
        finally:
            with self._latest_condition:
                self._latest_condition.notify_all()

    def _read_latest(self, retry: int) -> tuple[bool, object | None]:
        wait_seconds = max(float(retry or 1) * 0.2, 0.2)
        deadline = time.monotonic() + wait_seconds
        with self._latest_condition:
            while self._latest_frame_sequence <= self._last_read_sequence:
                if self.process is None or self.process.poll() is not None:
                    if self._reader_error is None:
                        returncode = self.process.poll() if self.process is not None else None
                        self._reader_error = f"ffmpeg exited; returncode={returncode}"
                    return False, None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False, None
                self._latest_condition.wait(timeout=min(remaining, 0.2))

            frame = self._latest_frame.copy()
            self._last_read_sequence = self._latest_frame_sequence
            self.last_frame_sequence = self._latest_frame_sequence
            self.last_frame_timestamp = self._latest_frame_timestamp
            return True, frame

    def read(self, retry: int = 3):
        if getattr(self, "_latest_only", False):
            return self._read_latest(retry)

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
                if getattr(self, "_first_frame_at", None) is None:
                    self._first_frame_at = time.time()
                self.last_frame_timestamp = time.time()
                self.last_frame_sequence = getattr(self, "last_frame_sequence", 0) + 1
                return True, frame
            time.sleep(0.05)
        return False, None

    def release(self):
        if self.process is None:
            return
        self._reader_stop_event.set()
        with self._latest_condition:
            self._latest_condition.notify_all()
        try:
            self.process.kill()
            self.process.wait(timeout=2)
        except Exception:
            pass
        finally:
            self.process = None
            self._pending_frame_bytes.clear()
            if self._reader_thread and self._reader_thread.is_alive():
                self._reader_thread.join(timeout=1)
            self._reader_thread = None
            if self._stderr_thread and self._stderr_thread.is_alive():
                self._stderr_thread.join(timeout=1)
            self._stderr_thread = None
            with self._latest_condition:
                self._latest_frame = None
                self._latest_condition.notify_all()

    def diagnostics(self) -> dict:
        process = self.process
        returncode = process.poll() if process is not None else None
        stderr_lock = getattr(self, "_stderr_lock", None)
        if stderr_lock is None:
            stderr_tail = [
                _redact_diagnostic_text(line)
                for line in list(getattr(self, "_stderr_tail", []))
            ]
        else:
            with stderr_lock:
                stderr_tail = [
                    _redact_diagnostic_text(line)
                    for line in list(getattr(self, "_stderr_tail", []))
                ]
        started_at = getattr(self, "_started_at", None)
        first_frame_at = getattr(self, "_first_frame_at", None)
        first_frame_latency = None
        if first_frame_at is not None and started_at is not None:
            first_frame_latency = max(first_frame_at - started_at, 0.0)
        return {
            "pid": getattr(process, "pid", None) if process is not None else None,
            "process_alive": process is not None and returncode is None,
            "process_returncode": returncode,
            "reader_alive": bool(getattr(self, "_reader_thread", None) and self._reader_thread.is_alive()),
            "stderr_reader_alive": bool(getattr(self, "_stderr_thread", None) and self._stderr_thread.is_alive()),
            "reader_error": getattr(self, "_reader_error", None),
            "stderr_tail": stderr_tail,
            "started_at": started_at,
            "first_frame_at": first_frame_at,
            "first_frame_latency_seconds": first_frame_latency,
            "last_frame_timestamp": self.last_frame_timestamp,
            "last_frame_sequence": self.last_frame_sequence,
        }

    def is_healthy(self) -> bool:
        if self.process is None or self.process.poll() is not None:
            return False
        if getattr(self, "_latest_only", False) and self._reader_thread is not None:
            return self._reader_thread.is_alive()
        return True
