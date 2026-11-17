from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import time
from typing import Any

import cv2

from config import settings
from detectors.person_count import PersonCountResult
from person_count.entry_tracker import PersonEntryEvent


class PersonCountSnapshotManager:
    def __init__(self, *, snapshot_dir: Path | str | None = None):
        self.snapshot_dir = Path(snapshot_dir or settings.person_count_snapshot_dir)

    def save(
        self,
        channel: dict[str, Any],
        result: PersonCountResult,
        *,
        created_at: datetime | None = None,
    ) -> dict[str, Any]:
        camera_code = str(channel.get("camera_code") or "").strip()
        if not re.fullmatch(r"\d{20}", camera_code):
            raise ValueError("camera_code must contain exactly 20 digits")
        if result.person_count <= 0:
            raise ValueError("person count must be greater than 0")
        timestamp = created_at or datetime.now()
        day_dir = self.snapshot_dir / str(channel.get("id") or "").strip() / timestamp.strftime(
            "%Y%m%d"
        )
        day_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{camera_code}_01_{timestamp.strftime('%Y%m%d%H%M%S')}.jpg"
        output_path = (day_dir / filename).resolve()
        temporary_path = output_path.with_name(
            f".{output_path.stem}.{time.time_ns()}.tmp.jpg"
        )
        image = result.source_frame.copy()
        for detection in result.detections:
            x1, y1, x2, y2 = detection.box
            cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(
                image,
                f"person {detection.confidence:.2f}",
                (x1, max(y1 - 8, 16)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )
        cv2.putText(
            image,
            f"Person Count: {result.person_count}",
            (16, 32),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
        try:
            if not cv2.imwrite(str(temporary_path), image):
                raise RuntimeError("failed to encode person count snapshot")
            temporary_path.replace(output_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return {
            "channel_id": str(channel.get("id") or "").strip(),
            "analysis_type": "01",
            "analysis_case": f"{result.person_count:04d}",
            "snapshot_path": str(output_path),
        }

    def save_entry(
        self,
        channel: dict[str, Any],
        result: PersonCountResult,
        event: PersonEntryEvent,
        *,
        created_at: datetime | None = None,
    ) -> dict[str, Any]:
        camera_code = str(channel.get("camera_code") or "").strip()
        if not re.fullmatch(r"\d{20}", camera_code):
            raise ValueError("camera_code must contain exactly 20 digits")
        timestamp = created_at or datetime.now()
        day_dir = self.snapshot_dir / str(channel.get("id") or "").strip() / timestamp.strftime(
            "%Y%m%d"
        )
        day_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{camera_code}_01_{timestamp.strftime('%Y%m%d%H%M%S')}.jpg"
        output_path = (day_dir / filename).resolve()
        temporary_path = output_path.with_name(
            f".{output_path.stem}.{time.time_ns()}.tmp.jpg"
        )
        image = result.source_frame.copy()
        x1, y1, x2, y2 = [int(round(value)) for value in event.bbox]
        cv2.rectangle(image, (x1, y1), (x2, y2), (255, 0, 0), 2)
        cv2.putText(
            image,
            f"ENTRY {event.track_id} {event.confidence:.2f}",
            (x1, max(y1 - 8, 16)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 0, 0),
            2,
            cv2.LINE_AA,
        )
        try:
            if not cv2.imwrite(str(temporary_path), image):
                raise RuntimeError("failed to encode person entry snapshot")
            temporary_path.replace(output_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return {
            "channel_id": str(channel.get("id") or "").strip(),
            "snapshot_path": str(output_path),
        }

    def save_behavior(
        self,
        channel: dict[str, Any],
        result: PersonCountResult,
        event: Any,
        *,
        created_at: datetime | None = None,
    ) -> dict[str, Any]:
        camera_code = str(channel.get("camera_code") or "").strip()
        if not re.fullmatch(r"\d{20}", camera_code):
            raise ValueError("camera_code must contain exactly 20 digits")
        timestamp = created_at or datetime.now()
        day_dir = self.snapshot_dir / str(channel.get("id") or "").strip() / timestamp.strftime(
            "%Y%m%d"
        )
        day_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{camera_code}_01_{timestamp.strftime('%Y%m%d%H%M%S')}.jpg"
        output_path = (day_dir / filename).resolve()
        temporary_path = output_path.with_name(
            f".{output_path.stem}.{time.time_ns()}.tmp.jpg"
        )
        image = result.source_frame.copy()
        for detection in result.detections:
            x1, y1, x2, y2 = detection.box
            cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 2)
        x1, y1, x2, y2 = [int(round(value)) for value in event.bbox]
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 0, 255), 3)
        behavior_label = str(getattr(event, "behavior_code", "behavior")).upper()
        event_track_id = getattr(
            event,
            "follower_track_id",
            getattr(event, "track_id", ""),
        )
        cv2.putText(
            image,
            f"{behavior_label} {event_track_id}",
            (x1, max(y1 - 8, 16)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
        try:
            if not cv2.imwrite(str(temporary_path), image):
                raise RuntimeError("failed to encode behavior snapshot")
            temporary_path.replace(output_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return {
            "channel_id": str(channel.get("id") or "").strip(),
            "snapshot_path": str(output_path),
        }
