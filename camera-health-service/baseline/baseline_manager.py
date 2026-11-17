from __future__ import annotations

from hashlib import sha256
import logging
from pathlib import Path
import time
from typing import Any, Callable

import cv2

from config import settings
from config import HEALTH_DETECTOR_THRESHOLDS
from baseline.quality_validator import BaselineQualityValidator
from stream.ffmpeg_reader import FFmpegReader


logger = logging.getLogger(__name__)
metrics_logger = logging.getLogger("uvicorn.error")


class BaselineManager:
    def __init__(
        self,
        *,
        snapshot_dir: Path | str | None = None,
        reader_factory: Callable[..., Any] = FFmpegReader,
        quality_validator: BaselineQualityValidator | None = None,
        warmup_seconds: float | None = None,
        candidate_frames: int | None = None,
        max_capture_attempts: int | None = None,
        sample_interval_seconds: float | None = None,
        sleep_func: Callable[[float], Any] = time.sleep,
    ):
        self.snapshot_dir = Path(snapshot_dir or settings.snapshot_dir)
        self.reader_factory = reader_factory
        exposure = HEALTH_DETECTOR_THRESHOLDS["exposure"]
        blur = HEALTH_DETECTOR_THRESHOLDS["blur"]
        freeze = HEALTH_DETECTOR_THRESHOLDS["freeze"]
        self.quality_validator = quality_validator or BaselineQualityValidator(
            expected_width=settings.frame_width,
            expected_height=settings.frame_height,
            low_brightness=float(exposure["low_brightness"]),
            high_brightness=float(exposure["high_brightness"]),
            max_extreme_ratio=float(exposure["min_extreme_ratio"]),
            min_blur_variance=float(blur["min_blur_variance"]),
            min_frame_difference=float(freeze["max_difference"]),
            max_frame_difference=settings.baseline_max_frame_difference,
        )
        self.warmup_seconds = max(
            float(
                settings.baseline_warmup_seconds
                if warmup_seconds is None
                else warmup_seconds
            ),
            0.0,
        )
        self.candidate_frames = max(
            int(
                settings.baseline_candidate_frames
                if candidate_frames is None
                else candidate_frames
            ),
            2,
        )
        self.max_capture_attempts = max(
            int(
                settings.baseline_max_capture_attempts
                if max_capture_attempts is None
                else max_capture_attempts
            ),
            self.candidate_frames,
        )
        self.sample_interval_seconds = max(
            float(
                settings.baseline_sample_interval_seconds
                if sample_interval_seconds is None
                else sample_interval_seconds
            ),
            0.0,
        )
        self.sleep_func = sleep_func

    def capture(self, channel: dict[str, Any]) -> dict[str, Any]:
        started_at = time.perf_counter()
        channel_id = str(channel.get("id") or "").strip()
        if not channel_id:
            raise ValueError("channel id is required")

        candidates = self._source_candidates(channel)
        if not candidates:
            raise ValueError("channel source is not configured")

        errors: list[str] = []
        selected_frame = None
        capture_source = ""
        accepted_candidates = 0
        capture_attempts = 0
        for capture_source, source_url in candidates:
            try:
                reader = self.reader_factory(source_url)
                capture = self._capture_stable_candidates(reader)
                selected_frame = capture["frame"]
                accepted_candidates = capture["accepted_candidates"]
                capture_attempts = capture["capture_attempts"]
                break
            except Exception as exc:
                errors.append(f"{capture_source}: {exc}")

        if selected_frame is None:
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            logger.warning(
                "baseline capture rejected channel_id=%s capture_elapsed_ms=%.1f errors=%s",
                channel_id,
                elapsed_ms,
                "; ".join(errors) or "no frame captured",
            )
            raise RuntimeError("; ".join(errors) or "no frame captured")

        output_path = self._write_baseline(channel_id, selected_frame)
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        metrics_logger.info(
            "baseline capture succeeded channel_id=%s source=%s "
            "accepted_candidates=%d capture_attempts=%d capture_elapsed_ms=%.1f",
            channel_id,
            capture_source,
            accepted_candidates,
            capture_attempts,
            elapsed_ms,
        )

        return {
            "channel_id": channel_id,
            "capture_source": capture_source,
            "snapshot_path": str(output_path),
            "accepted_candidates": accepted_candidates,
            "capture_attempts": capture_attempts,
        }

    def capture_from_frame_buffer(
        self,
        channel_id: str,
        frame_buffer: Any,
        *,
        max_frame_age_seconds: float,
        sample_interval_seconds: float = 0.2,
        allow_frozen_frames: bool = False,
    ) -> dict[str, Any]:
        """Build a baseline from the already-running Worker frame stream."""
        channel_id = str(channel_id or "").strip()
        if not channel_id:
            raise ValueError("channel id is required")
        started_at = time.perf_counter()
        accepted: list[Any] = []
        previous_frame = None
        previous_sequence: int | None = None
        attempts = 0
        last_reason = "no_new_worker_frame"
        interval = max(float(sample_interval_seconds), 0.0)

        while attempts < self.max_capture_attempts:
            attempts += 1
            snapshot = frame_buffer.snapshot()
            frame = snapshot.get("frame")
            sequence = int(snapshot.get("sequence") or 0)
            age_seconds = snapshot.get("age_seconds")
            if (
                frame is None
                or age_seconds is None
                or float(age_seconds) > float(max_frame_age_seconds)
            ):
                raise RuntimeError(
                    "worker frame is unavailable or stale: "
                    f"age_seconds={age_seconds}"
                )
            if previous_sequence is not None and sequence == previous_sequence:
                last_reason = "no_new_worker_frame"
            else:
                result = self.validate_frame(
                    frame,
                    previous_frame=previous_frame,
                )
                if result.accepted or (
                    allow_frozen_frames and result.reason == "frame_frozen"
                ):
                    accepted.append(frame.copy())
                    last_reason = result.reason
                elif result.reason == "frame_unstable":
                    accepted = [frame.copy()]
                    last_reason = result.reason
                else:
                    accepted.clear()
                    last_reason = result.reason
                previous_frame = frame.copy()
                previous_sequence = sequence
                if len(accepted) >= self.candidate_frames:
                    selected = self.select_best_frame(accepted)
                    output_path = self._write_baseline(channel_id, selected)
                    elapsed_ms = (time.perf_counter() - started_at) * 1000
                    metrics_logger.info(
                        "baseline buffer capture succeeded channel_id=%s "
                        "accepted_candidates=%d capture_attempts=%d "
                        "capture_elapsed_ms=%.1f",
                        channel_id,
                        len(accepted),
                        attempts,
                        elapsed_ms,
                    )
                    return {
                        "channel_id": channel_id,
                        "capture_source": "worker_buffer",
                        "snapshot_path": str(output_path),
                        "accepted_candidates": len(accepted),
                        "capture_attempts": attempts,
                    }
            if attempts < self.max_capture_attempts and interval:
                self.sleep_func(interval)

        raise RuntimeError(
            "stable Worker baseline candidates unavailable: "
            f"accepted={len(accepted)}/{self.candidate_frames}, "
            f"attempts={attempts}, last_reason={last_reason}"
        )

    def ensure(self, channel: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        current_path = str(channel.get("snapshot_path") or "").strip()
        if current_path and not force and Path(current_path).is_file():
            return {
                "channel_id": str(channel.get("id") or "").strip(),
                "capture_source": "existing",
                "snapshot_path": current_path,
                "changed": False,
            }
        result = self.capture(channel)
        result["changed"] = True
        return result

    def save_frame(self, channel_id: str, frame: Any) -> dict[str, Any]:
        channel_id = str(channel_id or "").strip()
        if not channel_id:
            raise ValueError("channel id is required")
        if frame is None or not hasattr(frame, "size") or frame.size == 0:
            raise ValueError("frame is empty")
        quality = self.quality_validator.inspect(frame)
        if not quality.accepted:
            raise ValueError(f"baseline frame rejected: {quality.reason}")
        started_at = time.perf_counter()
        output_path = self._write_baseline(channel_id, frame)
        metrics_logger.info(
            "baseline frame saved channel_id=%s save_elapsed_ms=%.1f quality_metrics=%s",
            channel_id,
            (time.perf_counter() - started_at) * 1000,
            quality.metrics,
        )
        return {
            "channel_id": channel_id,
            "capture_source": "stable_frame",
            "snapshot_path": str(output_path),
        }

    def validate_frame(
        self,
        frame: Any,
        *,
        previous_frame: Any | None = None,
    ):
        return self.quality_validator.inspect(
            frame,
            previous_frame=previous_frame,
        )

    def select_best_frame(self, frames: list[Any]) -> Any:
        return self.quality_validator.select_best(frames)

    def _capture_stable_candidates(self, reader: Any) -> dict[str, Any]:
        # Probe before warming up so an unavailable source falls back immediately.
        # The probe is intentionally not a candidate: calibration starts only after
        # the source has remained reachable for the configured warmup period.
        reader.read_first_frame()
        if self.warmup_seconds:
            self.sleep_func(self.warmup_seconds)
        accepted: list[Any] = []
        previous_frame = None
        attempts = 0
        last_reason = "no_frame_captured"
        while attempts < self.max_capture_attempts:
            attempts += 1
            frame = reader.read_first_frame()
            result = self.validate_frame(
                frame,
                previous_frame=previous_frame,
            )
            if result.accepted:
                accepted.append(frame.copy())
                last_reason = result.reason
            elif result.reason == "frame_unstable":
                accepted = [frame.copy()]
                last_reason = result.reason
            else:
                accepted.clear()
                last_reason = result.reason
            previous_frame = frame.copy()
            if len(accepted) >= self.candidate_frames:
                return {
                    "frame": self.select_best_frame(accepted),
                    "accepted_candidates": len(accepted),
                    "capture_attempts": attempts,
                }
            if attempts < self.max_capture_attempts and self.sample_interval_seconds:
                self.sleep_func(self.sample_interval_seconds)
        raise RuntimeError(
            "stable baseline candidates unavailable: "
            f"accepted={len(accepted)}/{self.candidate_frames}, "
            f"attempts={attempts}, last_reason={last_reason}"
        )

    def _write_baseline(self, channel_id: str, frame: Any) -> Path:
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        filename = f"channel_{sha256(channel_id.encode('utf-8')).hexdigest()[:20]}.jpg"
        output_path = (self.snapshot_dir / filename).resolve()
        temporary_path = output_path.with_name(
            f".{output_path.stem}.{time.time_ns()}.tmp.jpg"
        )
        try:
            if not cv2.imwrite(str(temporary_path), frame):
                raise RuntimeError("failed to encode baseline snapshot")
            temporary_path.replace(output_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return output_path

    def save_event_frame(
        self,
        channel_id: str,
        analysis_case: str,
        frame: Any,
    ) -> dict[str, Any]:
        channel_id = str(channel_id or "").strip()
        analysis_case = str(analysis_case or "").strip()
        if not channel_id:
            raise ValueError("channel id is required")
        if len(analysis_case) != 4:
            raise ValueError("analysis case must be a four-character code")
        if frame is None or not hasattr(frame, "size") or frame.size == 0:
            raise ValueError("frame is empty")

        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        safe_channel_id = "".join(
            char if char.isalnum() or char in "-_" else "_"
            for char in channel_id
        )
        filename = (
            f"event_{safe_channel_id}_{analysis_case}_{time.time_ns()}.jpg"
        )
        output_path = (self.snapshot_dir / filename).resolve()
        temporary_path = output_path.with_name(
            f".{output_path.stem}.{time.time_ns()}.tmp.jpg"
        )
        try:
            if not cv2.imwrite(str(temporary_path), frame):
                raise RuntimeError("failed to encode event snapshot")
            temporary_path.replace(output_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return {
            "channel_id": channel_id,
            "analysis_case": analysis_case,
            "event_path": str(output_path),
        }

    @staticmethod
    def _source_candidates(channel: dict[str, Any]) -> list[tuple[str, str]]:
        rtsp = str(channel.get("source_url") or "").strip()
        flv = str(channel.get("flv") or "").strip()
        candidates: list[tuple[str, str]] = []
        if rtsp:
            candidates.append(("rtsp", rtsp))
        if flv and flv != rtsp:
            candidates.append(("flv", flv))
        return candidates
