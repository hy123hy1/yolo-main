from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import time
from typing import Any, Callable

import cv2
import numpy as np

from src.electronic_seal.store import ElectronicSealStore
from src.vision_core.ffmpeg_capture import FFmpegCapture


class ElectronicSealSnapshotService:
    def __init__(
        self,
        *,
        store: ElectronicSealStore,
        snapshot_dir: Path,
        worker_provider: Callable[[str], Any | None],
        capture_factory: Callable[..., Any] = FFmpegCapture,
        width: int = 1280,
        height: int = 720,
    ) -> None:
        self.store = store
        self.snapshot_dir = Path(snapshot_dir)
        self.worker_provider = worker_provider
        self.capture_factory = capture_factory
        self.width = int(width)
        self.height = int(height)

    def capture(self, channel_id: str) -> dict[str, Any]:
        channel_id = str(channel_id or "").strip()
        channel = self.store.get_channel(channel_id)
        if not channel:
            raise ValueError(f"electronic-seal channel not found: {channel_id}")

        frame = self._worker_frame(channel_id)
        capture_source = "worker"
        if frame is None:
            candidates = self._source_candidates(channel)
            if not candidates:
                raise ValueError("electronic-seal channel source is not configured")
            frame = None
            last_error: ValueError | None = None
            for capture_source, source_url in candidates:
                try:
                    frame = self._capture_source_frame(source_url)
                    break
                except ValueError as exc:
                    last_error = exc
            if frame is None:
                raise last_error or ValueError(
                    "failed to read electronic-seal channel frame"
                )

        if not isinstance(frame, np.ndarray) or frame.size == 0:
            raise ValueError("electronic-seal snapshot frame is unavailable")

        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        filename = f"channel_{sha256(channel_id.encode('utf-8')).hexdigest()[:20]}.jpg"
        output_path = (self.snapshot_dir / filename).resolve()
        temporary_path = output_path.with_name(f".{output_path.stem}.{time.time_ns()}.tmp.jpg")
        try:
            if not cv2.imwrite(str(temporary_path), frame):
                raise ValueError("failed to encode electronic-seal snapshot")
            temporary_path.replace(output_path)
        finally:
            temporary_path.unlink(missing_ok=True)

        version = time.time_ns()
        snapshot_url = (
            f"/electronic-seal/channels/{channel_id}/snapshot/image?v={version}"
        )
        updated = self.store.update_channel_snapshot(
            channel_id,
            snapshot_url,
            str(output_path),
        )
        if not updated:
            raise ValueError(f"electronic-seal channel not found: {channel_id}")
        return {
            "channel_id": channel_id,
            "status": "captured",
            "capture_source": capture_source,
            "snapshot_url": snapshot_url,
            "snapshot_path": str(output_path),
        }

    def latest(self, channel_id: str) -> dict[str, Any]:
        channel_id = str(channel_id or "").strip()
        channel = self.store.get_channel(channel_id)
        if not channel:
            raise ValueError(f"electronic-seal channel not found: {channel_id}")
        return {
            "channel_id": channel_id,
            "snapshot_url": channel.get("snapshot_url"),
            "snapshot_path": channel.get("snapshot_path"),
        }

    def resolve_image(self, channel_id: str) -> Path:
        channel_id = str(channel_id or "").strip()
        channel = self.store.get_channel(channel_id)
        if not channel:
            raise ValueError(f"electronic-seal channel not found: {channel_id}")
        path_value = channel.get("snapshot_path")
        if not path_value:
            raise ValueError("electronic-seal snapshot not found")
        path = self._confined_path(path_value)
        if not path.is_file():
            raise ValueError("electronic-seal snapshot not found")
        return path

    def _worker_frame(self, channel_id: str) -> Any | None:
        worker = self.worker_provider(channel_id)
        if worker is None:
            return None
        frame_loader = getattr(worker, "snapshot_frame", None)
        if not callable(frame_loader):
            return None
        return frame_loader()

    def _capture_source_frame(self, source_url: str) -> Any:
        capture = self.capture_factory(
            source_url,
            self.width,
            self.height,
            init_wait=0.0,
        )
        try:
            ok, frame = capture.read(retry=3)
            if not ok or frame is None:
                raise ValueError("failed to read electronic-seal channel frame")
            return frame
        finally:
            capture.release()

    def _source_candidates(self, channel: dict[str, Any]) -> list[tuple[str, str]]:
        rtsp = str(channel.get("source_url") or "").strip()
        flv = str(channel.get("flv") or "").strip()
        if not flv:
            upstream = ((channel.get("ext") or {}).get("upstream") or {})
            play_info = upstream.get("play_info") or {}
            flv = str(play_info.get("flv") or "").strip()

        candidates: list[tuple[str, str]] = []
        if rtsp:
            candidates.append(("rtsp", rtsp))
        if flv and flv != rtsp:
            candidates.append(("flv", flv))
        return candidates

    def _confined_path(self, path_value: Any) -> Path:
        path = Path(str(path_value)).resolve()
        root = self.snapshot_dir.resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                "snapshot path is outside electronic-seal snapshot directory"
            ) from exc
        return path


__all__ = ["ElectronicSealSnapshotService"]
