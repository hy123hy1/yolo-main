from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from storage.models import (
    Base,
    CameraHealthEvent,
    CameraHealthState,
    Channel,
    PersonCountRecord,
)


def _now() -> datetime:
    return datetime.now()


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value))
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    raise TypeError(f"unsupported datetime value: {type(value).__name__}")


def _json_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_value(value: str | None) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _next_channel_id(session: Session) -> str:
    max_value = 0
    for value in session.scalars(select(Channel.id)).all():
        text = str(value or "").strip()
        if text.isdigit():
            max_value = max(max_value, int(text))
    return f"{max_value + 1:04d}"


def _channel_dict(row: Channel) -> dict[str, Any]:
    return {
        "id": row.id,
        "gb_id": row.gb_id,
        "device_id": row.device_id,
        "name": row.name,
        "type": row.type,
        "source_url": row.source_url,
        "flv": row.flv,
        "stream": row.stream,
        "is_online": bool(row.is_online),
        "camera_code": row.camera_code,
        "person_count_enabled": bool(row.person_count_enabled),
        "snapshot_path": row.snapshot_path,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _health_state_dict(row: CameraHealthState) -> dict[str, Any]:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "algorithm_type": row.algorithm_type,
        "status": row.status,
        "current_score": row.current_score,
        "last_reason": row.last_reason,
        "last_metrics_json": _json_value(row.last_metrics_json),
        "bad_started_at": row.bad_started_at,
        "recovery_started_at": row.recovery_started_at,
        "updated_at": row.updated_at,
    }


def _health_event_dict(row: CameraHealthEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "algorithm_type": row.algorithm_type,
        "analysis_case": row.analysis_case,
        "status": row.status,
        "metrics_json": _json_value(row.metrics_json),
        "event_path": row.event_path,
        "started_at": row.started_at,
        "ended_at": row.ended_at,
        "duration_seconds": row.duration_seconds,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _person_count_dict(row: PersonCountRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "analysis_type": row.analysis_type,
        "analysis_case": row.analysis_case,
        "snapshot_path": row.snapshot_path,
        "created_at": row.created_at,
    }


class CameraHealthRepository:
    def __init__(self, database_url: str = "sqlite:///./camera_health.sqlite3"):
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(
            database_url,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
        self.session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )
        Base.metadata.create_all(self.engine)

    def upsert_channel(self, payload: dict[str, Any]) -> dict[str, Any]:
        gb_id = str(payload.get("gb_id") or "").strip()
        if not gb_id:
            raise ValueError("gb_id is required")

        with self.session_factory() as session:
            row = session.scalar(select(Channel).where(Channel.gb_id == gb_id))
            now = _now()
            if row is None:
                row = Channel(
                    id=_next_channel_id(session),
                    gb_id=gb_id,
                    name=str(payload.get("name") or gb_id),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                is_new = True
            else:
                is_new = False

            row.device_id = payload.get("device_id") or row.device_id
            row.name = str(payload.get("name") or row.name or gb_id)
            row.type = str(payload.get("type") or row.type or "wvp")
            row.is_online = (
                bool(payload["is_online"])
                if "is_online" in payload
                else bool(row.is_online)
            )
            if is_new and payload.get("camera_code") is not None:
                row.camera_code = self._validate_camera_code(payload["camera_code"])
            if is_new and "person_count_enabled" in payload:
                row.person_count_enabled = bool(payload["person_count_enabled"])
            if not row.source_url and payload.get("source_url"):
                row.source_url = str(payload["source_url"]).strip()
            if not row.flv and payload.get("flv"):
                row.flv = str(payload["flv"]).strip()
            if payload.get("stream"):
                row.stream = str(payload["stream"]).strip()
            row.updated_at = now
            session.commit()
            result = _channel_dict(row)
            result["is_new"] = is_new
            result["baseline_required"] = is_new or not row.snapshot_path
            return result

    def set_camera_code(
        self,
        channel_id: str,
        camera_code: str | None,
    ) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.get(Channel, str(channel_id).strip())
            if row is None:
                return None
            row.camera_code = self._validate_camera_code(camera_code)
            row.updated_at = _now()
            session.commit()
            return _channel_dict(row)

    def update_person_count_config(
        self,
        channel_id: str,
        *,
        enabled: bool,
    ) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.get(Channel, str(channel_id).strip())
            if row is None:
                return None
            row.person_count_enabled = bool(enabled)
            row.updated_at = _now()
            session.commit()
            return _channel_dict(row)

    def create_person_count_record(self, payload: dict[str, Any]) -> dict[str, Any]:
        channel_id = str(payload.get("channel_id") or "").strip()
        if not channel_id:
            raise ValueError("channel_id is required")
        analysis_type = str(payload.get("analysis_type") or "01").strip()
        if analysis_type != "01":
            raise ValueError('analysis_type must be "01"')
        analysis_case = str(payload.get("analysis_case") or "").strip()
        if not re.fullmatch(r"\d{4}", analysis_case) or analysis_case == "0000":
            raise ValueError("analysis_case must be a non-zero four-digit code")
        with self.session_factory() as session:
            if session.get(Channel, channel_id) is None:
                raise ValueError("channel not found")
            now = _now()
            row = PersonCountRecord(
                id=str(payload.get("id") or uuid4().hex),
                channel_id=channel_id,
                analysis_type=analysis_type,
                analysis_case=analysis_case,
                snapshot_path=(
                    str(payload["snapshot_path"]).strip()
                    if payload.get("snapshot_path")
                    else None
                ),
                created_at=_coerce_datetime(payload.get("created_at")) or now,
            )
            session.add(row)
            session.commit()
            return _person_count_dict(row)

    def get_person_count_record(self, record_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.get(PersonCountRecord, str(record_id).strip())
            return _person_count_dict(row) if row else None

    def list_person_count_records(
        self,
        channel_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
        start_time: Any = None,
        end_time: Any = None,
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            statement = select(PersonCountRecord).where(
                PersonCountRecord.channel_id == str(channel_id).strip()
            )
            start = _coerce_datetime(start_time)
            end = _coerce_datetime(end_time)
            if start is not None:
                statement = statement.where(PersonCountRecord.created_at >= start)
            if end is not None:
                statement = statement.where(PersonCountRecord.created_at <= end)
            total = len(session.scalars(statement).all())
            offset = max(int(page) - 1, 0) * max(int(page_size), 1)
            rows = session.scalars(
                statement.order_by(PersonCountRecord.created_at.desc()).offset(offset).limit(
                    max(int(page_size), 1)
                )
            ).all()
            return {
                "page": max(int(page), 1),
                "page_size": max(int(page_size), 1),
                "total": total,
                "items": [_person_count_dict(row) for row in rows],
            }

    @staticmethod
    def _validate_camera_code(camera_code: str | None) -> str | None:
        if camera_code is None or not str(camera_code).strip():
            return None
        value = str(camera_code).strip()
        if not re.fullmatch(r"\d{20}", value):
            raise ValueError("camera_code must contain exactly 20 digits")
        return value

    def update_playback(
        self,
        channel_id: str,
        *,
        stream: str | None = None,
        source_url: str | None = None,
        flv: str | None = None,
        is_online: bool | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.get(Channel, str(channel_id).strip())
            if row is None:
                return None
            previous_stream = row.stream
            previous_source = row.source_url
            if stream:
                row.stream = str(stream).strip()
            if not row.source_url and source_url:
                row.source_url = str(source_url).strip()
            if not row.flv and flv:
                row.flv = str(flv).strip()
            if is_online is not None:
                row.is_online = bool(is_online)
            row.updated_at = _now()
            session.commit()
            result = _channel_dict(row)
            result["baseline_required"] = (
                bool(previous_stream and row.stream and previous_stream != row.stream)
                or bool(previous_source is None and row.source_url)
                or not row.snapshot_path
            )
            return result

    def update_channel(self, channel_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.get(Channel, str(channel_id).strip())
            if row is None:
                return None
            source_changed = (
                "source_url" in payload
                and str(payload.get("source_url") or "").strip() != str(row.source_url or "").strip()
            )
            stream_changed = (
                "stream" in payload
                and str(payload.get("stream") or "").strip() != str(row.stream or "").strip()
            )
            for field in ("name", "device_id", "type", "source_url", "flv", "stream", "is_online"):
                if field in payload:
                    setattr(row, field, payload[field])
            row.updated_at = _now()
            session.commit()
            result = _channel_dict(row)
            result["baseline_required"] = source_changed or stream_changed
            return result

    def update_snapshot_path(self, channel_id: str, snapshot_path: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.get(Channel, str(channel_id).strip())
            if row is None:
                return None
            row.snapshot_path = str(snapshot_path)
            row.updated_at = _now()
            session.commit()
            return _channel_dict(row)

    def get_channel(self, channel_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.get(Channel, str(channel_id).strip())
            return _channel_dict(row) if row else None

    def get_channel_by_gb_id(self, gb_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(Channel).where(Channel.gb_id == str(gb_id).strip())
            )
            return _channel_dict(row) if row else None

    def list_channels(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(Channel).order_by(Channel.created_at.desc(), Channel.id.desc())
            ).all()
            return [_channel_dict(row) for row in rows]

    def mark_missing_offline(self, active_gb_ids: set[str]) -> int:
        normalized_ids = {str(value).strip() for value in active_gb_ids if str(value).strip()}
        with self.session_factory() as session:
            rows = session.scalars(select(Channel)).all()
            changed = 0
            for row in rows:
                if row.gb_id not in normalized_ids and row.is_online:
                    row.is_online = False
                    row.updated_at = _now()
                    changed += 1
            session.commit()
            return changed

    def save_health_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        channel_id = str(payload.get("channel_id") or "").strip()
        algorithm_type = str(payload.get("algorithm_type") or "").strip()
        if not channel_id or not algorithm_type:
            raise ValueError("channel_id and algorithm_type are required")

        with self.session_factory() as session:
            row = session.scalar(
                select(CameraHealthState).where(
                    CameraHealthState.channel_id == channel_id,
                    CameraHealthState.algorithm_type == algorithm_type,
                )
            )
            now = _now()
            if row is None:
                row = CameraHealthState(
                    id=str(payload.get("id") or uuid4().hex),
                    channel_id=channel_id,
                    algorithm_type=algorithm_type,
                    status="normal",
                    updated_at=now,
                )
                session.add(row)

            row.status = str(payload.get("status") or row.status or "normal")
            if "current_score" in payload:
                row.current_score = payload["current_score"]
            if "last_reason" in payload:
                row.last_reason = payload["last_reason"]
            metrics = payload.get("last_metrics_json", payload.get("metrics"))
            if metrics is not None:
                row.last_metrics_json = _json_text(metrics)
            if "bad_started_at" in payload:
                row.bad_started_at = _coerce_datetime(payload["bad_started_at"])
            if "recovery_started_at" in payload:
                row.recovery_started_at = _coerce_datetime(
                    payload["recovery_started_at"]
                )
            row.updated_at = now
            session.commit()
            return _health_state_dict(row)

    def get_health_state(
        self,
        channel_id: str,
        algorithm_type: str,
    ) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(CameraHealthState).where(
                    CameraHealthState.channel_id == str(channel_id).strip(),
                    CameraHealthState.algorithm_type == str(algorithm_type).strip(),
                )
            )
            return _health_state_dict(row) if row else None

    def list_health_states(self, channel_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(CameraHealthState)
                .where(CameraHealthState.channel_id == str(channel_id).strip())
                .order_by(CameraHealthState.algorithm_type.asc())
            ).all()
            return [_health_state_dict(row) for row in rows]

    def create_health_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        channel_id = str(payload.get("channel_id") or "").strip()
        algorithm_type = str(payload.get("algorithm_type") or "").strip()
        if not channel_id or not algorithm_type:
            raise ValueError("channel_id and algorithm_type are required")
        analysis_case = str(payload.get("analysis_case") or "").strip()
        if len(analysis_case) != 4:
            raise ValueError("analysis_case must be a four-character code")
        now = _now()
        with self.session_factory() as session:
            row = CameraHealthEvent(
                id=str(payload.get("id") or uuid4().hex),
                channel_id=channel_id,
                algorithm_type=algorithm_type,
                analysis_case=analysis_case,
                status=str(payload.get("status") or "new"),
                metrics_json=_json_text(
                    payload.get("metrics_json", payload.get("metrics"))
                ),
                event_path=(
                    str(payload["event_path"]).strip()
                    if payload.get("event_path")
                    else None
                ),
                started_at=_coerce_datetime(payload.get("started_at")) or now,
                ended_at=_coerce_datetime(payload.get("ended_at")),
                duration_seconds=payload.get("duration_seconds"),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            return _health_event_dict(row)

    def get_health_event(self, event_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.get(CameraHealthEvent, str(event_id).strip())
            return _health_event_dict(row) if row else None

    def get_active_health_event(
        self,
        channel_id: str,
        algorithm_type: str,
    ) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(CameraHealthEvent)
                .where(
                    CameraHealthEvent.channel_id == str(channel_id).strip(),
                    CameraHealthEvent.algorithm_type
                    == str(algorithm_type).strip(),
                    CameraHealthEvent.status != "closed",
                )
                .order_by(CameraHealthEvent.created_at.desc())
            )
            return _health_event_dict(row) if row else None

    def close_health_event(
        self,
        event_id: str,
        *,
        ended_at: Any = None,
        duration_seconds: float | None = None,
    ) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.get(CameraHealthEvent, str(event_id).strip())
            if row is None:
                return None
            ended = _coerce_datetime(ended_at) or _now()
            row.status = "closed"
            row.ended_at = ended
            if duration_seconds is None:
                duration_seconds = max(
                    (ended - row.started_at).total_seconds(),
                    0.0,
                )
            row.duration_seconds = float(duration_seconds)
            row.updated_at = _now()
            session.commit()
            return _health_event_dict(row)

    def list_health_events(
        self,
        channel_id: str,
        *,
        algorithm_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            statement = select(CameraHealthEvent).where(
                CameraHealthEvent.channel_id == str(channel_id).strip()
            )
            if algorithm_type:
                statement = statement.where(
                    CameraHealthEvent.algorithm_type == str(algorithm_type).strip()
                )
            if status:
                statement = statement.where(CameraHealthEvent.status == status)
            rows = session.scalars(
                statement.order_by(CameraHealthEvent.created_at.desc()).limit(
                    max(int(limit), 1)
                )
            ).all()
            return [_health_event_dict(row) for row in rows]
