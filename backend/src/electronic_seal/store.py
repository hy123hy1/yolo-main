from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from secrets import token_urlsafe
import threading
from typing import Callable
from weakref import WeakValueDictionary

from sqlalchemy import inspect, or_, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.database import SessionLocal, engine as default_engine
from src.db.models import (
    Base,
    ChannelMeta,
    ElectronicSealChannelProfileMeta,
    ElectronicSealBaselineMeta,
    ElectronicSealConfigMeta,
    ElectronicSealCameraOcclusionEventMeta,
    ElectronicSealCameraViewShiftEventMeta,
    ElectronicSealEventMeta,
    ElectronicSealEventIndexMeta,
    ElectronicSealRoomEmptyEventMeta,
    ElectronicSealTruckCountMeta,
    ElectronicSealTruckDepartureEventMeta,
    ElectronicSealStateMeta,
    ElectronicSealUploadJobMeta,
)
from src.services.platform_storage import format_datetime, generate_id


ELECTRONIC_SEAL_TABLES = [
    ElectronicSealChannelProfileMeta.__table__,
    ElectronicSealConfigMeta.__table__,
    ElectronicSealBaselineMeta.__table__,
    ElectronicSealStateMeta.__table__,
    ElectronicSealEventMeta.__table__,
    ElectronicSealEventIndexMeta.__table__,
    ElectronicSealTruckDepartureEventMeta.__table__,
    ElectronicSealRoomEmptyEventMeta.__table__,
    ElectronicSealCameraOcclusionEventMeta.__table__,
    ElectronicSealCameraViewShiftEventMeta.__table__,
    ElectronicSealTruckCountMeta.__table__,
    ElectronicSealUploadJobMeta.__table__,
]

_UPLOAD_JOB_LOCK = threading.RLock()
_CHANNEL_LOCKS: WeakValueDictionary[str, threading.RLock] = WeakValueDictionary()
_CHANNEL_LOCKS_GUARD = threading.Lock()

_CONFIG_PAYLOAD_FIELDS = (
    "enabled",
    "model_id",
    "sample_interval_ms",
    "camera_health_interval_seconds",
    "alarm_hold_seconds",
    "recovery_hold_seconds",
    "need_snapshot",
    "need_video_clip",
    "line_config_json",
    "region_config_json",
    "threshold_config_json",
)
_CHANNEL_PROFILE_ROLES = frozenset({"vehicle_entry", "dispatch_room"})
_STATE_PAYLOAD_FIELDS = (
    "status",
    "current_score",
    "last_metrics_json",
    "runtime_json",
    "last_run_at",
    "bad_since",
    "last_good_at",
    "last_event_id",
)


def _channel_lock(channel_id: str) -> threading.RLock:
    with _CHANNEL_LOCKS_GUARD:
        lock = _CHANNEL_LOCKS.get(channel_id)
        if lock is None:
            lock = threading.RLock()
            _CHANNEL_LOCKS[channel_id] = lock
        return lock


def _format_electronic_seal_datetime(value: datetime | str | None) -> str | None:
    """Keep nonzero microseconds while retaining the existing second format."""
    if isinstance(value, datetime):
        if value.microsecond:
            return f"{value:%Y-%m-%d %H:%M:%S}.{value.microsecond:06d}"
        return format_datetime(value)

    if value is not None:
        text = str(value).strip()
        if text:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return format_datetime(value)
            if parsed.microsecond:
                return f"{parsed:%Y-%m-%d %H:%M:%S}.{parsed.microsecond:06d}"
    return format_datetime(value)


def _lock_channel_row(session: Session, channel_id: str) -> ChannelMeta:
    return (
        session.query(ChannelMeta)
        .filter(ChannelMeta.id == channel_id)
        .with_for_update()
        .one()
    )


def _apply_payload_fields(row, payload: dict, fields: tuple[str, ...]) -> None:
    for key in fields:
        if key in payload:
            setattr(row, key, deepcopy(payload[key]))


def _merge_upload_payload(row: ElectronicSealUploadJobMeta, payload: dict) -> None:
    if not payload:
        return
    merged_payload = deepcopy(row.payload_json) if isinstance(row.payload_json, dict) else {}
    merged_payload.update(deepcopy(payload))
    row.payload_json = merged_payload


def ensure_electronic_seal_schema(bind: Engine | None = None) -> None:
    engine = bind or default_engine
    Base.metadata.create_all(bind=engine, tables=ELECTRONIC_SEAL_TABLES)
    # ``create_all`` does not alter a table created by the previous module
    # version. Keep startup compatible with an already deployed worker while
    # retaining the explicit SQL migration for controlled deployments.
    inspector = inspect(engine)
    columns = {item["name"] for item in inspector.get_columns("electronic_seal_configs")}
    if "model_id" not in columns:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE electronic_seal_configs "
                "ADD COLUMN model_id VARCHAR(64) NULL"
            ))
    index_names = {item["name"] for item in inspect(engine).get_indexes("electronic_seal_configs")}
    if "ix_electronic_seal_configs_model_id" not in index_names:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE INDEX ix_electronic_seal_configs_model_id "
                "ON electronic_seal_configs (model_id)"
            ))


def _config_dict(row: ElectronicSealConfigMeta) -> dict:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "algorithm_type": row.algorithm_type,
        "model_id": row.model_id,
        "enabled": bool(row.enabled),
        "sample_interval_ms": row.sample_interval_ms,
        "camera_health_interval_seconds": row.camera_health_interval_seconds,
        "alarm_hold_seconds": row.alarm_hold_seconds,
        "recovery_hold_seconds": row.recovery_hold_seconds,
        "need_snapshot": bool(row.need_snapshot),
        "need_video_clip": bool(row.need_video_clip),
        "line_config_json": deepcopy(row.line_config_json or {}),
        "region_config_json": deepcopy(row.region_config_json or {}),
        "threshold_config_json": deepcopy(row.threshold_config_json or {}),
        "created_at": _format_electronic_seal_datetime(row.created_at),
        "updated_at": _format_electronic_seal_datetime(row.updated_at),
    }


def _channel_profile_dict(row: ElectronicSealChannelProfileMeta) -> dict:
    return {
        "channel_id": row.channel_id,
        "role": row.role,
        "bound": bool(row.bound),
        "profile_version": row.profile_version,
        "initialized_at": _format_electronic_seal_datetime(row.initialized_at),
        "created_at": _format_electronic_seal_datetime(row.created_at),
        "updated_at": _format_electronic_seal_datetime(row.updated_at),
    }


def _baseline_dict(row: ElectronicSealBaselineMeta) -> dict:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "algorithm_type": row.algorithm_type,
        "image_path": row.image_path,
        "width": row.width,
        "height": row.height,
        "quality_score": row.quality_score,
        "calibration_params_json": deepcopy(row.calibration_params_json or {}),
        "captured_at": _format_electronic_seal_datetime(row.captured_at),
        "version": row.version,
        "status": row.status,
    }


def _state_dict(row: ElectronicSealStateMeta) -> dict:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "algorithm_type": row.algorithm_type,
        "status": row.status,
        "current_score": row.current_score,
        "last_metrics_json": deepcopy(row.last_metrics_json or {}),
        "runtime_json": deepcopy(row.runtime_json or {}),
        "last_run_at": _format_electronic_seal_datetime(row.last_run_at),
        "bad_since": _format_electronic_seal_datetime(row.bad_since),
        "last_good_at": _format_electronic_seal_datetime(row.last_good_at),
        "last_event_id": row.last_event_id,
        "updated_at": _format_electronic_seal_datetime(row.updated_at),
    }


def _event_dict(row: ElectronicSealEventMeta) -> dict:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "algorithm_type": row.algorithm_type,
        "event_type": row.event_type,
        "severity": row.severity,
        "started_at": _format_electronic_seal_datetime(row.started_at),
        "ended_at": _format_electronic_seal_datetime(row.ended_at),
        "duration_seconds": row.duration_seconds,
        "score": row.score,
        "metrics_json": deepcopy(row.metrics_json or {}),
        "evidence_snapshot_path": row.evidence_snapshot_path,
        "evidence_video_path": row.evidence_video_path,
        "handling_status": row.handling_status,
        "status": row.status,
        "created_at": _format_electronic_seal_datetime(row.created_at),
        "updated_at": _format_electronic_seal_datetime(row.updated_at),
    }


_EVENT_DETAIL_MODELS = {
    "truck_departure": ("electronic_seal_truck_departure_events", ElectronicSealTruckDepartureEventMeta),
    "room_empty": ("electronic_seal_room_empty_events", ElectronicSealRoomEmptyEventMeta),
    "camera_occlusion": ("electronic_seal_camera_occlusion_events", ElectronicSealCameraOcclusionEventMeta),
    "camera_view_shift": ("electronic_seal_camera_view_shift_events", ElectronicSealCameraViewShiftEventMeta),
}


def _event_detail_table(algorithm_type: str):
    try:
        return _EVENT_DETAIL_MODELS[str(algorithm_type)]
    except KeyError as exc:
        raise ValueError(f"unsupported electronic-seal event algorithm: {algorithm_type}") from exc


def _event_datetime_value(value: datetime | str | float | int | None, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value))
    text_value = str(value).strip()
    if not text_value:
        return fallback
    return datetime.fromisoformat(text_value.replace("Z", "+00:00")).replace(tzinfo=None)


def _indexed_event_dict(index_row: ElectronicSealEventIndexMeta, detail_row=None) -> dict:
    metrics = deepcopy(getattr(detail_row, "metrics_json", None) or {})
    return {
        "id": index_row.id,
        "channel_id": index_row.channel_id,
        "algorithm_type": index_row.algorithm_type,
        "event_type": index_row.event_type,
        "detail_table": index_row.detail_table,
        "severity": index_row.severity,
        "started_at": _format_electronic_seal_datetime(index_row.started_at),
        "detected_at": _format_electronic_seal_datetime(index_row.detected_at),
        "ended_at": _format_electronic_seal_datetime(index_row.ended_at),
        "uploaded_at": _format_electronic_seal_datetime(index_row.uploaded_at),
        "upload_status": index_row.upload_status,
        "duration_seconds": (
            (index_row.ended_at - index_row.started_at).total_seconds()
            if index_row.ended_at is not None and index_row.started_at is not None
            else None
        ),
        "score": index_row.score,
        "metrics_json": metrics,
        "evidence_snapshot_path": getattr(detail_row, "evidence_snapshot_path", None),
        "evidence_video_path": getattr(detail_row, "evidence_video_path", None),
        "handling_status": index_row.handling_status,
        "status": index_row.status,
        "created_at": _format_electronic_seal_datetime(index_row.created_at),
        "updated_at": _format_electronic_seal_datetime(index_row.updated_at),
    }


def _upload_job_dict(row: ElectronicSealUploadJobMeta) -> dict:
    return {
        "id": row.id,
        "event_id": row.event_id,
        "target": row.target,
        "status": row.status,
        "attempts": row.attempts,
        "payload_json": deepcopy(row.payload_json) if row.payload_json is not None else None,
        "next_retry_at": _format_electronic_seal_datetime(row.next_retry_at),
        "claimed_at": _format_electronic_seal_datetime(row.claimed_at),
        "lease_expires_at": _format_electronic_seal_datetime(row.lease_expires_at),
        "claim_token": row.claim_token,
        "remote_id": row.remote_id,
        "last_error": row.last_error,
        "idempotency_key": row.idempotency_key,
        "created_at": _format_electronic_seal_datetime(row.created_at),
        "updated_at": _format_electronic_seal_datetime(row.updated_at),
    }


class ElectronicSealStore:
    def __init__(self, session_factory: Callable[[], Session] = SessionLocal):
        self.session_factory = session_factory

    def get_channel_profile(self, channel_id: str) -> dict | None:
        with self.session_factory() as session:
            row = (
                session.query(ElectronicSealChannelProfileMeta)
                .filter(ElectronicSealChannelProfileMeta.channel_id == str(channel_id))
                .one_or_none()
            )
            return _channel_profile_dict(row) if row else None

    def list_channel_profiles(
        self,
        channel_id: str | None = None,
        *,
        bound_only: bool = False,
    ) -> list[dict]:
        with self.session_factory() as session:
            query = session.query(ElectronicSealChannelProfileMeta)
            if channel_id:
                query = query.filter(ElectronicSealChannelProfileMeta.channel_id == str(channel_id))
            if bound_only:
                query = query.filter(ElectronicSealChannelProfileMeta.bound.is_(True))
            rows = query.order_by(ElectronicSealChannelProfileMeta.channel_id).all()
            return [_channel_profile_dict(row) for row in rows]

    def list_bound_channel_profiles(self) -> list[dict]:
        return self.list_channel_profiles(bound_only=True)

    def upsert_channel_profile(self, channel_id: str, payload: dict) -> dict:
        channel_id = str(channel_id).strip()
        payload = payload or {}
        role = str(payload.get("role") or "").strip()
        if role not in _CHANNEL_PROFILE_ROLES:
            raise ValueError(
                f"unsupported electronic-seal channel role: {role or '<empty>'}"
            )
        with _channel_lock(channel_id):
            with self.session_factory() as session:
                _lock_channel_row(session, channel_id)
                now = datetime.now()
                row = (
                    session.query(ElectronicSealChannelProfileMeta)
                    .filter(ElectronicSealChannelProfileMeta.channel_id == channel_id)
                    .one_or_none()
                )
                if row is None:
                    row = ElectronicSealChannelProfileMeta(
                        channel_id=channel_id,
                        role=role,
                        bound=bool(payload.get("bound", True)),
                        profile_version=str(payload.get("profile_version") or "role-v1"),
                        initialized_at=payload.get("initialized_at"),
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(row)
                else:
                    row.role = role
                    if "bound" in payload:
                        row.bound = bool(payload["bound"])
                    if payload.get("profile_version"):
                        row.profile_version = str(payload["profile_version"])
                    if "initialized_at" in payload:
                        row.initialized_at = payload["initialized_at"]
                    row.updated_at = now
                session.commit()
                session.refresh(row)
                return _channel_profile_dict(row)

    def ensure_default_configs(
        self,
        channel_id: str,
        defaults: list[dict],
        *,
        model_id: str | None,
        preserve_existing: bool = True,
    ) -> list[dict]:
        """Create role rows once and fill only missing model IDs on old rows."""
        channel_id = str(channel_id).strip()
        with _channel_lock(channel_id):
            with self.session_factory() as session:
                _lock_channel_row(session, channel_id)
                now = datetime.now()
                result: list[dict] = []
                for default in defaults:
                    algorithm_type = str(default["algorithm_type"])
                    row = (
                        session.query(ElectronicSealConfigMeta)
                        .filter(
                            ElectronicSealConfigMeta.channel_id == channel_id,
                            ElectronicSealConfigMeta.algorithm_type == algorithm_type,
                        )
                        .one_or_none()
                    )
                    created = row is None
                    if created:
                        row = ElectronicSealConfigMeta(
                            id=generate_id("esc"),
                            channel_id=channel_id,
                            algorithm_type=algorithm_type,
                            model_id=model_id,
                            enabled=True,
                            sample_interval_ms=1000,
                            camera_health_interval_seconds=3600,
                            alarm_hold_seconds=10,
                            recovery_hold_seconds=15,
                            need_snapshot=True,
                            need_video_clip=False,
                            line_config_json={},
                            region_config_json={},
                            threshold_config_json={},
                            created_at=now,
                            updated_at=now,
                        )
                        session.add(row)
                    else:
                        if not row.model_id and model_id:
                            row.model_id = model_id
                        if not preserve_existing:
                            _apply_payload_fields(row, default, _CONFIG_PAYLOAD_FIELDS)
                        row.updated_at = now
                    if created:
                        _apply_payload_fields(row, default, _CONFIG_PAYLOAD_FIELDS)
                        if model_id and not row.model_id:
                            row.model_id = model_id
                    result.append(row)
                session.commit()
                return [_config_dict(row) for row in result]

    def list_configs(self, channel_id: str | None = None) -> list[dict]:
        with self.session_factory() as session:
            query = session.query(ElectronicSealConfigMeta)
            if channel_id:
                query = query.filter(ElectronicSealConfigMeta.channel_id == channel_id)
            rows = query.order_by(
                ElectronicSealConfigMeta.channel_id,
                ElectronicSealConfigMeta.algorithm_type,
            ).all()
            return [_config_dict(row) for row in rows]

    def get_config(self, channel_id: str, algorithm_type: str) -> dict | None:
        with self.session_factory() as session:
            row = (
                session.query(ElectronicSealConfigMeta)
                .filter(
                    ElectronicSealConfigMeta.channel_id == channel_id,
                    ElectronicSealConfigMeta.algorithm_type == algorithm_type,
                )
                .one_or_none()
            )
            return _config_dict(row) if row else None

    def upsert_config(self, channel_id: str, algorithm_type: str, payload: dict) -> dict:
        payload = payload or {}
        with _channel_lock(channel_id):
            with self.session_factory() as session:
                _lock_channel_row(session, channel_id)
                now = datetime.now()
                row = (
                    session.query(ElectronicSealConfigMeta)
                    .filter(
                        ElectronicSealConfigMeta.channel_id == channel_id,
                        ElectronicSealConfigMeta.algorithm_type == algorithm_type,
                    )
                    .one_or_none()
                )
                created = row is None
                if created:
                    row = ElectronicSealConfigMeta(
                        id=generate_id("esc"),
                        channel_id=channel_id,
                        algorithm_type=algorithm_type,
                        model_id=None,
                        enabled=False,
                        sample_interval_ms=1000,
                        camera_health_interval_seconds=3600,
                        alarm_hold_seconds=10,
                        recovery_hold_seconds=15,
                        need_snapshot=True,
                        need_video_clip=False,
                        line_config_json={},
                        region_config_json={},
                        threshold_config_json={},
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(row)

                _apply_payload_fields(row, payload, _CONFIG_PAYLOAD_FIELDS)
                row.updated_at = now
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    if not created:
                        raise
                    row = (
                        session.query(ElectronicSealConfigMeta)
                        .filter(
                            ElectronicSealConfigMeta.channel_id == channel_id,
                            ElectronicSealConfigMeta.algorithm_type == algorithm_type,
                        )
                        .one_or_none()
                    )
                    if row is None:
                        raise
                    _apply_payload_fields(row, payload, _CONFIG_PAYLOAD_FIELDS)
                    row.updated_at = datetime.now()
                    session.commit()
                session.refresh(row)
                return _config_dict(row)

    def get_active_baseline(self, channel_id: str, algorithm_type: str) -> dict | None:
        with self.session_factory() as session:
            row = (
                session.query(ElectronicSealBaselineMeta)
                .filter(
                    ElectronicSealBaselineMeta.channel_id == channel_id,
                    ElectronicSealBaselineMeta.algorithm_type == algorithm_type,
                    ElectronicSealBaselineMeta.status == "active",
                )
                .order_by(ElectronicSealBaselineMeta.captured_at.desc())
                .first()
            )
            return _baseline_dict(row) if row else None

    def replace_baseline(self, channel_id: str, algorithm_type: str, payload: dict) -> dict:
        with _channel_lock(channel_id):
            with self.session_factory() as session:
                _lock_channel_row(session, channel_id)
                now = datetime.now()
                session.query(ElectronicSealBaselineMeta).filter(
                    ElectronicSealBaselineMeta.channel_id == channel_id,
                    ElectronicSealBaselineMeta.algorithm_type == algorithm_type,
                    ElectronicSealBaselineMeta.status == "active",
                ).update({"status": "superseded"}, synchronize_session=False)
                row = ElectronicSealBaselineMeta(
                    id=generate_id("esb"),
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
        with _channel_lock(channel_id):
            with self.session_factory() as session:
                _lock_channel_row(session, channel_id)
                session.query(ElectronicSealBaselineMeta).filter(
                    ElectronicSealBaselineMeta.channel_id == channel_id,
                    ElectronicSealBaselineMeta.algorithm_type == algorithm_type,
                    ElectronicSealBaselineMeta.status == "active",
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
                session.query(ElectronicSealStateMeta)
                .filter(
                    ElectronicSealStateMeta.channel_id == channel_id,
                    ElectronicSealStateMeta.algorithm_type == algorithm_type,
                )
                .one_or_none()
            )
            return _state_dict(row) if row else None

    def save_state(self, payload: dict) -> dict:
        channel_id = payload["channel_id"]
        algorithm_type = payload["algorithm_type"]
        with _channel_lock(channel_id):
            with self.session_factory() as session:
                _lock_channel_row(session, channel_id)
                now = datetime.now()
                row = (
                    session.query(ElectronicSealStateMeta)
                    .filter(
                        ElectronicSealStateMeta.channel_id == channel_id,
                        ElectronicSealStateMeta.algorithm_type == algorithm_type,
                    )
                    .one_or_none()
                )
                created = row is None
                if created:
                    row = ElectronicSealStateMeta(
                        id=generate_id("ess"),
                        channel_id=channel_id,
                        algorithm_type=algorithm_type,
                        status="calibrating",
                        last_metrics_json={},
                        runtime_json={},
                        updated_at=now,
                    )
                    session.add(row)
                _apply_payload_fields(row, payload, _STATE_PAYLOAD_FIELDS)
                row.updated_at = now
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    if not created:
                        raise
                    row = (
                        session.query(ElectronicSealStateMeta)
                        .filter(
                            ElectronicSealStateMeta.channel_id == channel_id,
                            ElectronicSealStateMeta.algorithm_type == algorithm_type,
                        )
                        .one_or_none()
                    )
                    if row is None:
                        raise
                    _apply_payload_fields(row, payload, _STATE_PAYLOAD_FIELDS)
                    row.updated_at = datetime.now()
                    session.commit()
                session.refresh(row)
                return _state_dict(row)

    def _create_upload_job_row(
        self,
        session: Session,
        event_id: str,
        target: str,
        payload: dict | None,
        now: datetime,
    ) -> ElectronicSealUploadJobMeta:
        payload = deepcopy(payload or {})
        row = (
            session.query(ElectronicSealUploadJobMeta)
            .filter(
                ElectronicSealUploadJobMeta.event_id == event_id,
                ElectronicSealUploadJobMeta.target == target,
            )
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            row = ElectronicSealUploadJobMeta(
                id=generate_id("esu"),
                event_id=event_id,
                target=target,
                status="pending",
                attempts=0,
                payload_json=payload or None,
                idempotency_key=f"{event_id}:{target}",
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        elif row.status in {"pending", "retry"}:
            _merge_upload_payload(row, payload)
            row.updated_at = now
        return row

    def create_event(self, payload: dict) -> dict:
        payload = payload or {}
        channel_id = str(payload["channel_id"])
        algorithm_type = str(payload["algorithm_type"])
        detail_table, detail_model = _event_detail_table(algorithm_type)
        metrics = deepcopy(payload.get("metrics_json") or {})
        rule = metrics.get("rule") if isinstance(metrics.get("rule"), dict) else {}
        if algorithm_type == "truck_departure":
            direction = str(payload.get("direction") or rule.get("direction") or "inside_to_outside")
            if direction != "inside_to_outside":
                raise ValueError("truck departure events must use inside_to_outside direction")
        now = datetime.now()
        event_id = str(payload.get("id") or payload.get("event_id") or generate_id("ese"))
        enqueue_upload = bool(payload.get("enqueue_upload", False))
        with self.session_factory() as session:
            if session.query(ChannelMeta.id).filter(ChannelMeta.id == channel_id).one_or_none() is not None:
                _lock_channel_row(session, channel_id)
            existing = (
                session.query(ElectronicSealEventIndexMeta)
                .filter(ElectronicSealEventIndexMeta.id == event_id)
                .one_or_none()
            )
            if existing is not None:
                detail = session.get(detail_model, existing.id)
                return _indexed_event_dict(existing, detail)

            started_at = _event_datetime_value(payload.get("started_at"), now)
            detected_at = _event_datetime_value(payload.get("detected_at"), started_at)
            ended_at = payload.get("ended_at")
            if ended_at is not None:
                ended_at = _event_datetime_value(ended_at, detected_at)
            event_type = str(payload.get("event_type") or algorithm_type)
            severity = str(payload.get("severity") or "warning")
            status = str(payload.get("status") or "new")
            handling_status = int(payload.get("handling_status") or 0)
            score = payload.get("score")

            index_row = ElectronicSealEventIndexMeta(
                id=event_id,
                channel_id=channel_id,
                algorithm_type=algorithm_type,
                event_type=event_type,
                detail_table=detail_table,
                started_at=started_at,
                detected_at=detected_at,
                ended_at=ended_at,
                uploaded_at=None,
                upload_status="pending" if algorithm_type == "truck_departure" and enqueue_upload else "not_required",
                severity=severity,
                score=score,
                handling_status=handling_status,
                status=status,
                created_at=now,
                updated_at=now,
            )
            session.add(index_row)
            session.flush()

            common = {
                "id": event_id,
                "channel_id": channel_id,
                "event_type": event_type,
                "started_at": started_at,
                "ended_at": ended_at,
                "score": score,
                "severity": severity,
                "status": status,
                "handling_status": handling_status,
                "evidence_snapshot_path": payload.get("evidence_snapshot_path"),
                "evidence_video_path": payload.get("evidence_video_path"),
                "created_at": now,
                "updated_at": now,
            }
            if algorithm_type == "truck_departure":
                detail_row = detail_model(
                    **common,
                    direction=direction,
                    track_id=(payload.get("track_id") or rule.get("track_id")),
                    vehicle_label=str(
                        payload.get("vehicle_label")
                        or payload.get("label")
                        or rule.get("vehicle_label")
                        or rule.get("label")
                        or "vehicle"
                    ),
                    confidence=payload.get("confidence", rule.get("confidence")),
                    line_id=payload.get("line_id") or rule.get("line_id"),
                    crossing_timestamp=_event_datetime_value(
                        payload.get("crossing_timestamp") or rule.get("crossing_timestamp"),
                        detected_at,
                    ),
                    bbox_json=deepcopy(payload.get("bbox") or rule.get("bbox") or []),
                    metrics_json=metrics,
                )
            elif algorithm_type == "room_empty":
                detail_row = detail_model(
                    **common,
                    person_count=int(payload.get("person_count", rule.get("person_count", 0)) or 0),
                    empty_seconds=payload.get("empty_seconds", rule.get("empty_seconds")),
                    metrics_json=metrics,
                )
            else:
                detail_row = detail_model(**common, metrics_json=metrics)
            session.add(detail_row)

            # Keep the previous table populated during the migration window so
            # old readers and existing upload-job rows remain usable.
            session.add(
                ElectronicSealEventMeta(
                    id=event_id,
                    channel_id=channel_id,
                    algorithm_type=algorithm_type,
                    event_type=event_type,
                    severity=severity,
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_seconds=payload.get("duration_seconds"),
                    score=score,
                    metrics_json=metrics,
                    evidence_snapshot_path=payload.get("evidence_snapshot_path"),
                    evidence_video_path=payload.get("evidence_video_path"),
                    handling_status=handling_status,
                    status=status,
                    created_at=now,
                    updated_at=now,
                )
            )
            if algorithm_type == "truck_departure":
                count_row = (
                    session.query(ElectronicSealTruckCountMeta)
                    .filter(ElectronicSealTruckCountMeta.channel_id == channel_id)
                    .with_for_update()
                    .one_or_none()
                )
                if count_row is None:
                    count_row = ElectronicSealTruckCountMeta(
                        channel_id=channel_id,
                        out_count=1,
                        last_event_id=event_id,
                        last_event_at=_event_datetime_value(
                            payload.get("crossing_timestamp") or rule.get("crossing_timestamp"),
                            detected_at,
                        ),
                        updated_at=now,
                    )
                    session.add(count_row)
                else:
                    count_row.out_count = int(count_row.out_count or 0) + 1
                    count_row.last_event_id = event_id
                    count_row.last_event_at = _event_datetime_value(
                        payload.get("crossing_timestamp") or rule.get("crossing_timestamp"),
                        detected_at,
                    )
                    count_row.updated_at = now
                if enqueue_upload:
                    self._create_upload_job_row(
                        session,
                        event_id,
                        str(payload.get("upload_target") or "supervision"),
                        {"evidence_paths": []},
                        now,
                    )
            session.commit()
            session.refresh(index_row)
            session.refresh(detail_row)
            return _indexed_event_dict(index_row, detail_row)

    def close_event(self, event_id: str, ended_at: datetime, duration_seconds: float) -> dict | None:
        with self.session_factory() as session:
            index_row = (
                session.query(ElectronicSealEventIndexMeta)
                .filter(ElectronicSealEventIndexMeta.id == event_id)
                .one_or_none()
            )
            if index_row is None:
                row = session.query(ElectronicSealEventMeta).filter(ElectronicSealEventMeta.id == event_id).one_or_none()
                if row is None:
                    return None
                row.ended_at = ended_at
                row.duration_seconds = float(duration_seconds)
                row.status = "closed"
                row.updated_at = datetime.now()
                session.commit()
                session.refresh(row)
                return _event_dict(row)
            detail_table, detail_model = _event_detail_table(index_row.algorithm_type)
            detail = session.get(detail_model, event_id)
            now = datetime.now()
            index_row.ended_at = ended_at
            index_row.status = "closed"
            index_row.updated_at = now
            if detail is not None:
                detail.ended_at = ended_at
                detail.status = "closed"
                detail.updated_at = now
            legacy = session.get(ElectronicSealEventMeta, event_id)
            if legacy is not None:
                legacy.ended_at = ended_at
                legacy.duration_seconds = float(duration_seconds)
                legacy.status = "closed"
                legacy.updated_at = now
            session.commit()
            session.refresh(index_row)
            return _indexed_event_dict(index_row, detail)

    def get_event(self, event_id: str) -> dict | None:
        with self.session_factory() as session:
            index_row = session.query(ElectronicSealEventIndexMeta).filter(ElectronicSealEventIndexMeta.id == event_id).one_or_none()
            if index_row is not None:
                _, detail_model = _event_detail_table(index_row.algorithm_type)
                return _indexed_event_dict(index_row, session.get(detail_model, event_id))
            row = session.query(ElectronicSealEventMeta).filter(ElectronicSealEventMeta.id == event_id).one_or_none()
            return _event_dict(row) if row else None

    def update_event(self, event_id: str, payload: dict) -> dict | None:
        with self.session_factory() as session:
            index_row = session.query(ElectronicSealEventIndexMeta).filter(ElectronicSealEventIndexMeta.id == event_id).one_or_none()
            if index_row is None:
                row = session.query(ElectronicSealEventMeta).filter(ElectronicSealEventMeta.id == event_id).one_or_none()
                if row is None:
                    return None
                for key in (
                    "ended_at",
                    "duration_seconds",
                    "score",
                    "metrics_json",
                    "evidence_snapshot_path",
                    "evidence_video_path",
                    "handling_status",
                    "status",
                ):
                    if key in payload:
                        setattr(row, key, deepcopy(payload[key]))
                row.updated_at = datetime.now()
                session.commit()
                session.refresh(row)
                return _event_dict(row)

            _, detail_model = _event_detail_table(index_row.algorithm_type)
            detail = session.get(detail_model, event_id)
            now = datetime.now()
            if "ended_at" in payload:
                index_row.ended_at = deepcopy(payload["ended_at"])
            if "score" in payload:
                index_row.score = deepcopy(payload["score"])
            if "handling_status" in payload:
                index_row.handling_status = int(payload["handling_status"])
            if "status" in payload:
                index_row.status = str(payload["status"])
            if detail is not None:
                for key in (
                    "ended_at",
                    "score",
                    "metrics_json",
                    "evidence_snapshot_path",
                    "evidence_video_path",
                    "handling_status",
                    "status",
                ):
                    if key in payload and hasattr(detail, key):
                        setattr(detail, key, deepcopy(payload[key]))
                detail.updated_at = now
            legacy = session.get(ElectronicSealEventMeta, event_id)
            if legacy is not None:
                for key in (
                    "ended_at",
                    "duration_seconds",
                    "score",
                    "metrics_json",
                    "evidence_snapshot_path",
                    "evidence_video_path",
                    "handling_status",
                    "status",
                ):
                    if key in payload:
                        setattr(legacy, key, deepcopy(payload[key]))
                legacy.updated_at = now
            if "evidence_snapshot_path" in payload or "evidence_video_path" in payload:
                job_rows = (
                    session.query(ElectronicSealUploadJobMeta)
                    .filter(ElectronicSealUploadJobMeta.event_id == event_id)
                    .all()
                )
                evidence_paths = [
                    value
                    for value in (
                        getattr(detail, "evidence_snapshot_path", None),
                        getattr(detail, "evidence_video_path", None),
                    )
                    if value
                ]
                for job in job_rows:
                    if job.status in {"pending", "retry"}:
                        _merge_upload_payload(job, {"evidence_paths": evidence_paths})
                        job.updated_at = now
            index_row.updated_at = now
            session.commit()
            session.refresh(index_row)
            return _indexed_event_dict(index_row, detail)

    def list_events(
        self,
        channel_id: str | None = None,
        algorithm_type: str | None = None,
        page: int = 1,
        page_size: int = 10,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        cursor: str | None = None,
    ) -> dict:
        safe_page = max(int(page or 1), 1)
        safe_page_size = min(max(int(page_size or 10), 1), 100)
        with self.session_factory() as session:
            query = session.query(ElectronicSealEventIndexMeta)
            if channel_id:
                query = query.filter(ElectronicSealEventIndexMeta.channel_id == channel_id)
            if algorithm_type:
                query = query.filter(ElectronicSealEventIndexMeta.algorithm_type == algorithm_type)
            if start_time is not None:
                query = query.filter(ElectronicSealEventIndexMeta.created_at >= start_time)
            if end_time is not None:
                query = query.filter(ElectronicSealEventIndexMeta.created_at <= end_time)
            if cursor:
                try:
                    cursor_time_text, cursor_id = str(cursor).split("|", 1)
                    cursor_time = datetime.fromisoformat(cursor_time_text)
                    query = query.filter(
                        or_(
                            ElectronicSealEventIndexMeta.created_at < cursor_time,
                            (
                                ElectronicSealEventIndexMeta.created_at == cursor_time
                            )
                            & (ElectronicSealEventIndexMeta.id < cursor_id),
                        )
                    )
                except (TypeError, ValueError):
                    raise ValueError("cursor must be created_at|event_id") from None
            total = query.count()
            rows = (
                query.order_by(ElectronicSealEventIndexMeta.created_at.desc(), ElectronicSealEventIndexMeta.id.desc())
                .offset((safe_page - 1) * safe_page_size)
                .limit(safe_page_size)
                .all()
            )
            if not rows and total == 0:
                legacy_query = session.query(ElectronicSealEventMeta)
                if channel_id:
                    legacy_query = legacy_query.filter(ElectronicSealEventMeta.channel_id == channel_id)
                if algorithm_type:
                    legacy_query = legacy_query.filter(ElectronicSealEventMeta.algorithm_type == algorithm_type)
                total = legacy_query.count()
                legacy_rows = (
                    legacy_query.order_by(ElectronicSealEventMeta.created_at.desc(), ElectronicSealEventMeta.id.desc())
                    .offset((safe_page - 1) * safe_page_size)
                    .limit(safe_page_size)
                    .all()
                )
                return {
                    "items": [_event_dict(row) for row in legacy_rows],
                    "total": total,
                    "page": safe_page,
                    "page_size": safe_page_size,
                }
            next_cursor = None
            if rows:
                last = rows[-1]
                next_cursor = f"{last.created_at.isoformat()}|{last.id}"
            return {
                "items": [
                    _indexed_event_dict(
                        row,
                        session.get(_event_detail_table(row.algorithm_type)[1], row.id),
                    )
                    for row in rows
                ],
                "total": total,
                "page": safe_page,
                "page_size": safe_page_size,
                "next_cursor": next_cursor,
            }

    def list_upload_jobs(
        self,
        statuses: set[str] | None = None,
        limit: int = 100,
    ) -> list[dict]:
        safe_limit = min(max(int(limit or 100), 1), 1000)
        with self.session_factory() as session:
            query = session.query(ElectronicSealUploadJobMeta)
            if statuses:
                query = query.filter(ElectronicSealUploadJobMeta.status.in_(set(statuses)))
            rows = (
                query.order_by(ElectronicSealUploadJobMeta.created_at.asc())
                .limit(safe_limit)
                .all()
            )
            return [_upload_job_dict(row) for row in rows]

    def requeue_upload_job(self, job_id: str) -> dict | None:
        with self.session_factory() as session:
            row = (
                session.query(ElectronicSealUploadJobMeta)
                .filter(ElectronicSealUploadJobMeta.id == job_id)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                return None
            if row.status == "succeeded":
                return _upload_job_dict(row)
            row.status = "pending"
            row.next_retry_at = None
            row.claimed_at = None
            row.lease_expires_at = None
            row.claim_token = None
            row.last_error = None
            row.updated_at = datetime.now()
            index_row = session.query(ElectronicSealEventIndexMeta).filter(
                ElectronicSealEventIndexMeta.id == row.event_id
            ).one_or_none()
            if index_row is not None:
                index_row.upload_status = "pending"
                index_row.updated_at = row.updated_at
            session.commit()
            session.refresh(row)
            return _upload_job_dict(row)

    def create_upload_job(self, event_id: str, target: str, payload: dict | None = None) -> dict:
        payload = deepcopy(payload or {})
        now = datetime.now()
        idempotency_key = f"{event_id}:{target}"
        with self.session_factory() as session:
            event = session.query(ElectronicSealEventMeta).filter(ElectronicSealEventMeta.id == event_id).one()
            with _channel_lock(event.channel_id):
                _lock_channel_row(session, event.channel_id)
                row = (
                    session.query(ElectronicSealUploadJobMeta)
                    .filter(
                        ElectronicSealUploadJobMeta.event_id == event_id,
                        ElectronicSealUploadJobMeta.target == target,
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if row is None:
                    row = ElectronicSealUploadJobMeta(
                        id=generate_id("esu"),
                        event_id=event_id,
                        target=target,
                        status="pending",
                        attempts=0,
                        payload_json=deepcopy(payload) if payload else None,
                        next_retry_at=payload.get("next_retry_at"),
                        remote_id=payload.get("remote_id"),
                        last_error=payload.get("last_error"),
                        idempotency_key=idempotency_key,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(row)
                    try:
                        session.commit()
                    except IntegrityError:
                        session.rollback()
                        row = (
                            session.query(ElectronicSealUploadJobMeta)
                            .filter(
                                ElectronicSealUploadJobMeta.event_id == event_id,
                                ElectronicSealUploadJobMeta.target == target,
                            )
                            .with_for_update()
                            .one_or_none()
                        )
                        if row is None:
                            raise
                        if row.status in {"pending", "retry"}:
                            _merge_upload_payload(row, payload)
                            row.updated_at = datetime.now()
                            session.commit()
                        else:
                            session.rollback()
                    else:
                        session.refresh(row)
                elif payload and row.status in {"pending", "retry"}:
                    _merge_upload_payload(row, payload)
                    row.updated_at = now
                    session.commit()
                    session.refresh(row)
                index_row = session.query(ElectronicSealEventIndexMeta).filter(
                    ElectronicSealEventIndexMeta.id == event_id
                ).one_or_none()
                if index_row is not None and row.status in {"pending", "retry", "uploading"}:
                    index_row.upload_status = "pending"
                    index_row.updated_at = datetime.now()
                    session.commit()
            return _upload_job_dict(row)

    def claim_upload_job(
        self,
        job_id: str,
        now: datetime | None = None,
        lease_seconds: int = 300,
        max_attempts: int = 5,
    ) -> dict | None:
        now = now or datetime.now()
        max_attempts = int(max_attempts)
        with _UPLOAD_JOB_LOCK:
            with self.session_factory() as session:
                row = (
                    session.query(ElectronicSealUploadJobMeta)
                    .filter(ElectronicSealUploadJobMeta.id == job_id)
                    .with_for_update()
                    .one_or_none()
                )
                if row is None:
                    return None
                attempts = int(row.attempts or 0)
                if row.status in {"pending", "retry"}:
                    if attempts >= max_attempts:
                        row.status = "failed"
                        row.next_retry_at = None
                        row.lease_expires_at = None
                        row.claim_token = None
                        row.last_error = "max attempts reached"
                        row.updated_at = now
                        session.commit()
                        return None
                    if row.next_retry_at is not None and row.next_retry_at > now:
                        return None
                elif row.status == "uploading":
                    if row.lease_expires_at is None or row.lease_expires_at > now:
                        return None
                    if attempts >= max_attempts:
                        row.status = "failed"
                        row.next_retry_at = None
                        row.lease_expires_at = None
                        row.claim_token = None
                        row.last_error = "max attempts reached"
                        row.updated_at = now
                        session.commit()
                        return None
                else:
                    return None

                row.status = "uploading"
                row.attempts = attempts + 1
                row.next_retry_at = None
                row.claimed_at = now
                row.lease_expires_at = now + timedelta(seconds=lease_seconds)
                row.claim_token = token_urlsafe(32)
                row.updated_at = now
                session.commit()
                session.refresh(row)
                return _upload_job_dict(row)

    def finish_upload_job(
        self,
        job_id: str,
        *,
        claim_token: str,
        success: bool,
        now: datetime | None = None,
        remote_id: str | None = None,
        error: str | None = None,
        next_retry_at: datetime | None = None,
        max_attempts: int = 5,
    ) -> dict | None:
        now = now if now is not None else datetime.now()
        with _UPLOAD_JOB_LOCK:
            with self.session_factory() as session:
                row = (
                    session.query(ElectronicSealUploadJobMeta)
                    .filter(ElectronicSealUploadJobMeta.id == job_id)
                    .with_for_update()
                    .one_or_none()
                )
                if row is None or row.status != "uploading" or row.claim_token != claim_token:
                    return None
                if row.lease_expires_at is None or row.lease_expires_at <= now:
                    return None
                if success:
                    row.status = "succeeded"
                    row.remote_id = remote_id
                    row.last_error = None
                    row.next_retry_at = None
                else:
                    row.last_error = error
                    row.next_retry_at = next_retry_at
                    row.status = "failed" if int(row.attempts or 0) >= int(max_attempts) else "retry"
                row.lease_expires_at = None
                row.claim_token = None
                row.updated_at = now
                index_row = session.query(ElectronicSealEventIndexMeta).filter(
                    ElectronicSealEventIndexMeta.id == row.event_id
                ).one_or_none()
                if index_row is not None:
                    index_row.upload_status = "succeeded" if success else row.status
                    index_row.uploaded_at = now if success else index_row.uploaded_at
                    index_row.updated_at = now
                session.commit()
                session.refresh(row)
                return _upload_job_dict(row)

    def get_truck_count(self, channel_id: str) -> dict | None:
        with self.session_factory() as session:
            if session.query(ChannelMeta.id).filter(ChannelMeta.id == str(channel_id)).one_or_none() is None:
                return None
            row = session.query(ElectronicSealTruckCountMeta).filter(
                ElectronicSealTruckCountMeta.channel_id == str(channel_id)
            ).one_or_none()
            if row is None:
                return {
                    "channel_id": str(channel_id),
                    "out_count": 0,
                    "last_event_id": None,
                    "last_event_at": None,
                    "updated_at": None,
                }
            return {
                "channel_id": row.channel_id,
                "out_count": int(row.out_count or 0),
                "last_event_id": row.last_event_id,
                "last_event_at": _format_electronic_seal_datetime(row.last_event_at),
                "updated_at": _format_electronic_seal_datetime(row.updated_at),
            }

    def list_truck_departures(
        self,
        channel_id: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> dict | None:
        safe_page = max(int(page or 1), 1)
        safe_page_size = min(max(int(page_size or 100), 1), 1000)
        with self.session_factory() as session:
            if session.query(ChannelMeta.id).filter(ChannelMeta.id == str(channel_id)).one_or_none() is None:
                return None
            query = session.query(ElectronicSealTruckDepartureEventMeta).filter(
                ElectronicSealTruckDepartureEventMeta.channel_id == str(channel_id),
                ElectronicSealTruckDepartureEventMeta.direction == "inside_to_outside",
            )
            if start_time is not None:
                query = query.filter(ElectronicSealTruckDepartureEventMeta.crossing_timestamp >= start_time)
            if end_time is not None:
                query = query.filter(ElectronicSealTruckDepartureEventMeta.crossing_timestamp <= end_time)
            total = query.count()
            rows = (
                query.order_by(
                    ElectronicSealTruckDepartureEventMeta.crossing_timestamp.desc(),
                    ElectronicSealTruckDepartureEventMeta.id.desc(),
                )
                .offset((safe_page - 1) * safe_page_size)
                .limit(safe_page_size)
                .all()
            )
            return {
                "items": [
                    {
                        "id": row.id,
                        "channel_id": row.channel_id,
                        "event_type": row.event_type,
                        "direction": row.direction,
                        "track_id": row.track_id,
                        "vehicle_label": row.vehicle_label,
                        "confidence": row.confidence,
                        "line_id": row.line_id,
                        "crossing_timestamp": _format_electronic_seal_datetime(row.crossing_timestamp),
                        "bbox": deepcopy(row.bbox_json or []),
                        "metrics_json": deepcopy(row.metrics_json or {}),
                    }
                    for row in rows
                ],
                "total": total,
                "page": safe_page,
                "page_size": safe_page_size,
            }


__all__ = ["ElectronicSealStore", "ELECTRONIC_SEAL_TABLES", "ensure_electronic_seal_schema"]
