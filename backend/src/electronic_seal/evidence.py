from __future__ import annotations

from datetime import datetime
from pathlib import Path
import math
import re
import subprocess
import sys
import time
from typing import Any, Callable

import cv2
import numpy as np

from src.core.settings import settings
from src.electronic_seal.evidence_buffer import EvidenceBuffer


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_") or "unknown"


def _fixed_code(value: str, width: int, field_name: str) -> str:
    code = str(value).strip()
    if len(code) != width or not code.isdigit():
        raise ValueError(f"{field_name} must contain exactly {width} digits")
    return code


def _event_datetime(value: Any | None) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone().replace(tzinfo=None)
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value))
    if value is not None:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
    return datetime.now()


def _timestamp_seconds(value: Any) -> float:
    if isinstance(value, datetime):
        result = value.timestamp()
    else:
        result = float(value)
    if not math.isfinite(result):
        raise ValueError("frame timestamp must be finite")
    return result


def _points(value: Any) -> np.ndarray | None:
    try:
        points = np.asarray(value, dtype=np.int32).reshape(-1, 1, 2)
    except (TypeError, ValueError):
        return None
    return points if len(points) >= 2 else None


class EvidenceService:
    """Create electronic-seal evidence without using the AI event directory."""

    def __init__(
        self,
        *,
        evidence_dir: Path | str,
        mine_code: str = "140421007866",
        camera_code: str = "20",
        analysis_type_code: str = "04",
    ):
        self.evidence_dir = Path(evidence_dir)
        self.mine_code = _fixed_code(mine_code, 12, "mine_code")
        self.camera_code = _fixed_code(camera_code, 2, "camera_code")
        self.analysis_type_code = _fixed_code(analysis_type_code, 2, "analysis_type_code")

    def create_snapshot(
        self,
        *,
        channel_id: str,
        event_id: str,
        frame: np.ndarray,
        annotations: dict[str, Any] | None = None,
    ) -> str:
        image = self.overlay_frame(frame, annotations or {})
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        path = self.evidence_dir / (
            f"snapshot_{_safe_name(channel_id)}_{_safe_name(event_id)}_{time.time_ns()}.jpg"
        )
        if not cv2.imwrite(str(path), image):
            raise RuntimeError(f"failed to write electronic-seal snapshot: {path}")
        return str(path)

    @staticmethod
    def encode_frame(frame: np.ndarray, quality: int = 75) -> bytes | np.ndarray:
        if not isinstance(frame, np.ndarray) or frame.size == 0:
            raise ValueError("frame must be a non-empty numpy image")
        safe_quality = min(max(int(quality), 10), 95)
        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, safe_quality],
        )
        return encoded.tobytes() if ok else frame.copy()

    @staticmethod
    def decode_frame(frame: Any) -> np.ndarray:
        if isinstance(frame, (bytes, bytearray, memoryview)):
            encoded = np.frombuffer(frame, dtype=np.uint8)
            decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
            if decoded is None:
                raise ValueError("failed to decode compressed evidence frame")
            return decoded
        if not isinstance(frame, np.ndarray) or frame.size == 0:
            raise ValueError("evidence frame must be a non-empty numpy image")
        image = frame.copy()
        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        return image

    def create_video(
        self,
        *,
        channel_id: str,
        event_id: str,
        buffer: EvidenceBuffer,
        started_at: Any,
        ended_at: Any,
        fps: float = 10.0,
        event_timestamp: Any | None = None,
        overlay_callback: Callable[[np.ndarray, Any], np.ndarray] | None = None,
    ) -> str:
        clip = buffer.build_clip(started_at, ended_at)
        return self.write_clip(
            channel_id=channel_id,
            event_id=event_id,
            clip=clip,
            fps=fps,
            event_timestamp=event_timestamp,
            overlay_callback=overlay_callback,
        )

    def write_clip(
        self,
        *,
        channel_id: str,
        event_id: str,
        clip,
        fps: float = 10.0,
        event_timestamp: Any | None = None,
        overlay_callback: Callable[[np.ndarray, Any], np.ndarray] | None = None,
        preserve_duration: bool = False,
    ) -> str:
        if fps <= 0:
            raise ValueError("fps must be positive")
        if not clip.frames:
            raise ValueError("evidence clip contains no frames")
        first = self.decode_frame(clip.frames[0].frame)
        height, width = first.shape[:2]
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        event_dir = self.evidence_dir / _safe_name(event_id)
        event_dir.mkdir(parents=True, exist_ok=True)
        event_time = _event_datetime(event_timestamp)
        path = event_dir / (
            f"{self.mine_code}"
            f"{self.camera_code}"
            f"{self.analysis_type_code}"
            f"{event_time:%Y%m%d%H%M%S}.MP4"
        )
        output_frames = self._resample_clip_frames(clip, fps) if preserve_duration else [
            (item, item.timestamp) for item in clip.frames
        ]
        ffmpeg_bin = str(getattr(settings, "FFMPEG_BIN", "ffmpeg") or "ffmpeg")
        command = [
            ffmpeg_bin,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{int(width)}x{int(height)}",
            "-r",
            f"{float(fps):.6f}",
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(path),
        ]
        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.PIPE,
            "bufsize": 0,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        process: subprocess.Popen | None = None
        completed = False
        try:
            process = subprocess.Popen(command, **popen_kwargs)
            if process.stdin is None:
                raise RuntimeError("FFmpeg stdin is unavailable")
            for item, output_timestamp in output_frames:
                frame = self.decode_frame(item.frame)
                if frame.shape[:2] != (height, width):
                    raise ValueError("all evidence frames must have the same dimensions")
                if overlay_callback is not None:
                    frame = overlay_callback(frame, output_timestamp)
                if frame.ndim != 3 or frame.shape[2] != 3:
                    raise ValueError("all evidence frames must be BGR images")
                process.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())
            process.stdin.close()
            # ``communicate()`` attempts to flush stdin on Python 3.12 even
            # after it has been closed. Wait for FFmpeg directly, then read
            # its small error pipe after the encoder exits.
            process.wait()
            stderr = process.stderr.read() if process.stderr is not None else b""
            if process.returncode != 0:
                detail = self._ffmpeg_stderr_text(stderr)
                raise RuntimeError(
                    f"FFmpeg failed to encode electronic-seal video: "
                    f"{detail or f'exit code {process.returncode}'}"
                )
            completed = True
        except BrokenPipeError as exc:
            detail = self._read_ffmpeg_stderr(process)
            raise RuntimeError(
                f"FFmpeg stopped while encoding electronic-seal video: "
                f"{detail or str(exc)}"
            ) from exc
        except OSError as exc:
            raise RuntimeError(f"failed to start FFmpeg for electronic-seal video: {exc}") from exc
        finally:
            if process is not None:
                if process.stdin is not None and not process.stdin.closed:
                    process.stdin.close()
                if not completed:
                    try:
                        if process.poll() is None:
                            process.kill()
                    except (AttributeError, OSError):
                        pass
                try:
                    process.wait()
                except (OSError, subprocess.SubprocessError):
                    pass
                if process.stderr is not None and not process.stderr.closed:
                    process.stderr.close()
            if not completed:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        return str(path)

    @staticmethod
    def _ffmpeg_stderr_text(stderr: Any) -> str:
        if isinstance(stderr, bytes):
            return stderr.decode("utf-8", errors="replace").strip()
        return str(stderr or "").strip()

    @classmethod
    def _read_ffmpeg_stderr(cls, process: subprocess.Popen | None) -> str:
        if process is None or process.stderr is None:
            return ""
        try:
            _, stderr = process.communicate()
        except (OSError, subprocess.SubprocessError):
            return ""
        return cls._ffmpeg_stderr_text(stderr)

    @staticmethod
    def _resample_clip_frames(clip, fps: float) -> list[tuple[Any, float]]:
        """Keep the output timeline at the configured FPS when input frames are sparse."""
        frames = list(clip.frames)
        if not frames:
            return []
        try:
            started_at = _timestamp_seconds(clip.started_at)
            ended_at = _timestamp_seconds(clip.ended_at)
        except (TypeError, ValueError, OverflowError):
            return [(item, item.timestamp) for item in frames]

        duration = max(ended_at - started_at, 0.0)
        output_count = max(1, int(round(duration * float(fps))))
        timestamped = []
        for item in frames:
            try:
                timestamped.append((_timestamp_seconds(item.timestamp), item))
            except (TypeError, ValueError, OverflowError):
                continue
        if not timestamped:
            return [(item, item.timestamp) for item in frames]

        timestamped.sort(key=lambda value: value[0])
        cursor = 0
        output: list[tuple[Any, float]] = []
        for index in range(output_count):
            target = started_at + index / float(fps)
            while (
                cursor + 1 < len(timestamped)
                and timestamped[cursor + 1][0] <= target
            ):
                cursor += 1
            output.append((timestamped[cursor][1], target))
        return output

    @staticmethod
    def overlay_frame(frame: np.ndarray, annotations: dict[str, Any]) -> np.ndarray:
        if not isinstance(frame, np.ndarray) or frame.size == 0:
            raise ValueError("frame must be a non-empty numpy image")
        image = frame.copy()
        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

        region = _points(annotations.get("region"))
        if region is not None:
            cv2.polylines(image, [region], True, (255, 0, 0), 2)
        line = _points(annotations.get("line"))
        if line is not None:
            cv2.polylines(image, [line], False, (0, 255, 0), 3)

        for detection in annotations.get("detections") or ():
            try:
                x1, y1, x2, y2 = (int(float(value)) for value in detection["bbox"])
            except (KeyError, TypeError, ValueError):
                continue
            label = detection.get("label") or detection.get("class_name") or "object"
            confidence = detection.get("confidence")
            text = str(label)
            if confidence is not None:
                try:
                    text = f"{text} {float(confidence):.2f}"
                except (TypeError, ValueError):
                    pass
            cv2.rectangle(image, (x1, y1), (x2, y2), (0, 0, 255), 2)
            text_y = max(y1 - 6, 14)
            cv2.putText(
                image,
                text,
                (max(x1, 0), text_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 0, 255),
                1,
                cv2.LINE_AA,
            )
        return image


__all__ = ["EvidenceService"]
