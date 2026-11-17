from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Callable

from sqlalchemy.orm import Session

from src.db.database import SessionLocal
from src.db.models import (
    CameraHealthBaselineMeta,
    CameraHealthConfigMeta,
    CameraHealthEventMeta,
    CameraHealthStateMeta,
)
from src.services.platform_storage import format_datetime, generate_id


def _config_dict(row: CameraHealthConfigMeta) -> dict:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "algorithm_type": row.algorithm_type,
        "enabled": bool(row.enabled),
        "sample_interval_ms": row.sample_interval_ms,
        "warmup_seconds": row.warmup_seconds,
        "alarm_hold_seconds": row.alarm_hold_seconds,
        "recovery_hold_seconds": row.recovery_hold_seconds,
        "alert_interval_seconds": row.alert_interval_seconds,
        "need_snapshot": bool(row.need_snapshot),
        "need_video_clip": bool(row.need_video_clip),
        "threshold_config_json": deepcopy(row.threshold_config_json or {}),
        "created_at": format_datetime(row.created_at),
        "updated_at": format_datetime(row.updated_at),
    }


def _baseline_dict(row: CameraHealthBaselineMeta) -> dict:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "algorithm_type": row.algorithm_type,
        "image_path": row.image_path,
        "width": row.width,
        "height": row.height,
        "quality_score": row.quality_score,
        "calibration_params_json": deepcopy(row.calibration_params_json or {}),
        "captured_at": format_datetime(row.captured_at),
        "version": row.version,
        "status": row.status,
    }


def _state_dict(row: CameraHealthStateMeta) -> dict:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "algorithm_type": row.algorithm_type,
        "status": row.status,
        "current_score": row.current_score,
        "last_metrics_json": deepcopy(row.last_metrics_json or {}),
        "bad_since": format_datetime(row.bad_since),
        "last_good_at": format_datetime(row.last_good_at),
        "last_event_id": row.last_event_id,
        "updated_at": format_datetime(row.updated_at),
    }


def _event_dict(row: CameraHealthEventMeta) -> dict:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "algorithm_type": row.algorithm_type,
        "event_type": row.event_type,
        "severity": row.severity,
        "started_at": format_datetime(row.started_at),
        "ended_at": format_datetime(row.ended_at),
        "duration_seconds": row.duration_seconds,
        "score": row.score,
        "metrics_json": deepcopy(row.metrics_json or {}),
        "snapshot_path": row.snapshot_path,
        "video_path": row.video_path,
        "handling_status": row.handling_status,
        "status": row.status,
        "created_at": format_datetime(row.created_at),
        "updated_at": format_datetime(row.updated_at),
    }


class CameraHealthStore:
    def __init__(self, session_factory: Callable[[], Session] = SessionLocal):
        self.session_factory = session_factory

    def list_configs(self, channel_id: str | None = None) -> list[dict]:
        with self.session_factory() as session:
            query = session.query(CameraHealthConfigMeta)
            if channel_id:
                query = query.filter(CameraHealthConfigMeta.channel_id == channel_id)
            rows = query.order_by(CameraHealthConfigMeta.channel_id, CameraHealthConfigMeta.algorithm_type).all()
            return [_config_dict(row) for row in rows]

    def get_config(self, channel_id: str, algorithm_type: str) -> dict | None:
        with self.session_factory() as session:
            row = (
                session.query(CameraHealthConfigMeta)
                .filter(
                    CameraHealthConfigMeta.channel_id == channel_id,
                    CameraHealthConfigMeta.algorithm_type == algorithm_type,
                )
                .one_or_none()
            )
            return _config_dict(row) if row else None

    def upsert_config(self, channel_id: str, algorithm_type: str, payload: dict) -> dict:
        now = datetime.now()
        with self.session_factory() as session:
            row = (
                session.query(CameraHealthConfigMeta)
                .filter(
                    CameraHealthConfigMeta.channel_id == channel_id,
                    CameraHealthConfigMeta.algorithm_type == algorithm_type,
                )
                .one_or_none()
            )
            if row is None:
                row = CameraHealthConfigMeta(
                    id=generate_id("chc"),
                    channel_id=channel_id,
                    algorithm_type=algorithm_type,
                    enabled=False,
                    sample_interval_ms=1000,
                    warmup_seconds=30,
                    alarm_hold_seconds=10,
                    recovery_hold_seconds=15,
                    alert_interval_seconds=300,
                    need_snapshot=True,
                    need_video_clip=False,
                    threshold_config_json={},
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)

            for key in (
                "enabled",
                "sample_interval_ms",
                "warmup_seconds",
                "alarm_hold_seconds",
                "recovery_hold_seconds",
                "alert_interval_seconds",
                "need_snapshot",
                "need_video_clip",
                "threshold_config_json",
            ):
                if key in payload:
                    setattr(row, key, deepcopy(payload[key]))
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return _config_dict(row)

    def get_active_baseline(self, channel_id: str, algorithm_type: str) -> dict | None:
        with self.session_factory() as session:
            row = (
                session.query(CameraHealthBaselineMeta)
                .filter(
                    CameraHealthBaselineMeta.channel_id == channel_id,
                    CameraHealthBaselineMeta.algorithm_type == algorithm_type,
                    CameraHealthBaselineMeta.status == "active",
                )
                .order_by(CameraHealthBaselineMeta.captured_at.desc())
                .first()
            )
            return _baseline_dict(row) if row else None

    def replace_baseline(self, channel_id: str, algorithm_type: str, payload: dict) -> dict:
        now = datetime.now()
        with self.session_factory() as session:
            session.query(CameraHealthBaselineMeta).filter(
                CameraHealthBaselineMeta.channel_id == channel_id,
                CameraHealthBaselineMeta.algorithm_type == algorithm_type,
                CameraHealthBaselineMeta.status == "active",
            ).update({"status": "superseded"}, synchronize_session=False)
            row = CameraHealthBaselineMeta(
                id=generate_id("chb"),
                channel_id=channel_id,
                algorithm_type=algorithm_type,
                image_path=payload["image_path"],
                width=int(payload["width"]),
                height=int(payload["height"]),
                quality_score=payload.get("quality_score"),
                calibration_params_json=deepcopy(payload.get("calibration_params_json") or {}),
                captured_at=payload.get("captured_at") or now,
                version=int(payload.get("version") or 1),
                status="active",
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _baseline_dict(row)

    def invalidate_baseline(self, channel_id: str, algorithm_type: str) -> dict:
        with self.session_factory() as session:
            session.query(CameraHealthBaselineMeta).filter(
                CameraHealthBaselineMeta.channel_id == channel_id,
                CameraHealthBaselineMeta.algorithm_type == algorithm_type,
                CameraHealthBaselineMeta.status == "active",
            ).update({"status": "superseded"}, synchronize_session=False)
            session.commit()
        return {
            "channel_id": channel_id,
            "algorithm_type": algorithm_type,
            "status": "superseded",
        }

    def get_state(self, channel_id: str, algorithm_type: str) -> dict | None:
        with self.session_factory() as session:
            row = (
                session.query(CameraHealthStateMeta)
                .filter(
                    CameraHealthStateMeta.channel_id == channel_id,
                    CameraHealthStateMeta.algorithm_type == algorithm_type,
                )
                .one_or_none()
            )
            return _state_dict(row) if row else None

    def save_state(self, payload: dict) -> dict:
        now = datetime.now()
        with self.session_factory() as session:
            row = (
                session.query(CameraHealthStateMeta)
                .filter(
                    CameraHealthStateMeta.channel_id == payload["channel_id"],
                    CameraHealthStateMeta.algorithm_type == payload["algorithm_type"],
                )
                .one_or_none()
            )
            if row is None:
                row = CameraHealthStateMeta(
                    id=generate_id("chs"),
                    channel_id=payload["channel_id"],
                    algorithm_type=payload["algorithm_type"],
                    status="calibrating",
                    updated_at=now,
                )
                session.add(row)
            for key in (
                "status",
                "current_score",
                "last_metrics_json",
                "bad_since",
                "last_good_at",
                "last_event_id",
            ):
                if key in payload:
                    setattr(row, key, deepcopy(payload[key]))
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return _state_dict(row)

    def create_event(self, payload: dict) -> dict:
        now = datetime.now()
        with self.session_factory() as session:
            row = CameraHealthEventMeta(
                id=generate_id("che"),
                channel_id=payload["channel_id"],
                algorithm_type=payload["algorithm_type"],
                event_type=payload.get("event_type", payload["algorithm_type"]),
                severity=payload.get("severity", "warning"),
                started_at=payload.get("started_at") or now,
                score=payload.get("score"),
                metrics_json=deepcopy(payload.get("metrics_json") or {}),
                snapshot_path=payload.get("snapshot_path"),
                video_path=payload.get("video_path"),
                handling_status=int(payload.get("handling_status") or 0),
                status=payload.get("status", "new"),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _event_dict(row)

    def close_event(self, event_id: str, ended_at: datetime, duration_seconds: float) -> dict | None:
        with self.session_factory() as session:
            row = session.query(CameraHealthEventMeta).filter(CameraHealthEventMeta.id == event_id).one_or_none()
            if row is None:
                return None
            row.ended_at = ended_at
            row.duration_seconds = float(duration_seconds)
            row.status = "closed"
            row.updated_at = datetime.now()
            session.commit()
            session.refresh(row)
            return _event_dict(row)

    def get_event(self, event_id: str) -> dict | None:
        with self.session_factory() as session:
            row = session.query(CameraHealthEventMeta).filter(CameraHealthEventMeta.id == event_id).one_or_none()
            return _event_dict(row) if row else None

    def update_event(self, event_id: str, payload: dict) -> dict | None:
        with self.session_factory() as session:
            row = session.query(CameraHealthEventMeta).filter(CameraHealthEventMeta.id == event_id).one_or_none()
            if row is None:
                return None
            for key in ("handling_status", "status", "snapshot_path", "video_path"):
                if key in payload:
                    setattr(row, key, payload[key])
            row.updated_at = datetime.now()
            session.commit()
            session.refresh(row)
            return _event_dict(row)

    def list_events(
        self,
        channel_id: str | None,
        algorithm_type: str | None,
        page: int,
        page_size: int,
    ) -> dict:
        safe_page = max(int(page or 1), 1)
        safe_page_size = min(max(int(page_size or 10), 1), 100)
        with self.session_factory() as session:
            query = session.query(CameraHealthEventMeta)
            if channel_id:
                query = query.filter(CameraHealthEventMeta.channel_id == channel_id)
            if algorithm_type:
                query = query.filter(CameraHealthEventMeta.algorithm_type == algorithm_type)
            total = query.count()
            rows = (
                query.order_by(CameraHealthEventMeta.created_at.desc(), CameraHealthEventMeta.id.desc())
                .offset((safe_page - 1) * safe_page_size)
                .limit(safe_page_size)
                .all()
            )
            return {
                "items": [_event_dict(row) for row in rows],
                "total": total,
                "page": safe_page,
                "page_size": safe_page_size,
            }
