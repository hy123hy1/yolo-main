from __future__ import annotations

from datetime import datetime, timedelta
import json
import math
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from storage.models import (
    AlgorithmDefinition,
    Base,
    CameraHealthEvent,
    CameraHealthState,
    Channel,
    ChannelAlgorithmConfig,
    PersonBehaviorEvent,
    PersonEntryEvent,
    PersonCountRecord,
)


PERSON_ANALYSIS_MODES = frozenset({"count", "entry", "count_and_entry"})
PERSON_ENTRY_DIRECTIONS = frozenset(
    {"outside_to_inside", "inside_to_outside", "toward_first_edge"}
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


def _validate_algorithm_code(value: Any) -> str:
    code = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", code):
        raise ValueError(
            "algorithm_code must contain 2-32 lowercase letters, digits, or underscores"
        )
    return code


def _validate_analysis_interval(value: Any) -> float | None:
    if value is None:
        return None
    try:
        interval = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("analysis_interval must be greater than 0") from exc
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("analysis_interval must be greater than 0")
    return interval


def _validate_algorithm_config(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("config must be an object")
    return value


def _validate_loitering_algorithm_config(value: dict[str, Any]) -> dict[str, Any]:
    forbidden = {"max_missing_seconds", "exit_confirm_seconds"}
    for field in forbidden:
        if field in value:
            raise ValueError(f"{field} is managed by the service")
    unexpected = set(value) - {"region_config", "dwell_seconds"}
    if unexpected:
        raise ValueError(
            f"unsupported loitering config field: {sorted(unexpected)[0]}"
        )
    if "region_config" in value and not isinstance(value["region_config"], dict):
        raise ValueError("region_config must be an object")
    if "dwell_seconds" in value:
        try:
            dwell_seconds = float(value["dwell_seconds"])
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("dwell_seconds must be greater than 0") from exc
        if not math.isfinite(dwell_seconds) or dwell_seconds <= 0:
            raise ValueError("dwell_seconds must be greater than 0")
    return value


def _validate_person_analysis_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode not in PERSON_ANALYSIS_MODES:
        raise ValueError(
            "person analysis mode must be count, entry, or count_and_entry"
        )
    return mode


def _validate_person_entry_direction(value: Any) -> str:
    direction = str(value or "outside_to_inside").strip().lower()
    if direction not in PERSON_ENTRY_DIRECTIONS:
        raise ValueError(
            "person entry direction must be outside_to_inside, "
            "inside_to_outside, or toward_first_edge"
        )
    return direction


def person_entry_analysis_type(direction: str) -> str:
    """Map a normalized crossing direction to the business analysis type."""
    if direction in {"inside_to_outside", "toward_first_edge"}:
        return "02"
    return "01"


def _mode_from_legacy_flags(count_enabled: bool, entry_enabled: bool) -> str:
    if count_enabled and entry_enabled:
        return "count_and_entry"
    if entry_enabled:
        return "entry"
    return "count"


def effective_person_analysis_config(channel: dict[str, Any]) -> dict[str, Any]:
    """Return a unified view while supporting channels created before migration."""
    raw_enabled = channel.get("person_analysis_enabled")
    if raw_enabled is None:
        count_enabled = bool(channel.get("person_count_enabled"))
        entry_enabled = bool(channel.get("person_entry_enabled"))
        enabled = count_enabled or entry_enabled
        mode = _mode_from_legacy_flags(count_enabled, entry_enabled)
    else:
        enabled = bool(raw_enabled)
        mode = _validate_person_analysis_mode(
            channel.get("person_analysis_mode") or "count_and_entry"
        )
    line_config = (
        channel.get("person_analysis_line_config")
        or channel.get("person_entry_line_config")
        or {}
    )
    region_config = (
        channel.get("person_analysis_region_config")
        or channel.get("person_entry_region_config")
        or {}
    )
    direction = _validate_person_entry_direction(
        channel.get("person_analysis_direction") or "outside_to_inside"
    )
    entry_requested = enabled and mode in {"entry", "count_and_entry"}
    # A four-point region generates its own directed crossing lines. Legacy
    # channels may still provide only a manually configured virtual line.
    region_points = region_config.get("points") if isinstance(region_config, dict) else None
    entry_configured = bool(line_config) or (
        isinstance(region_points, (list, tuple)) and len(region_points) == 4
    )
    return {
        "channel_id": str(channel.get("id") or "").strip(),
        "enabled": enabled,
        "mode": mode,
        "line_config": line_config,
        "region_config": region_config,
        "direction": direction,
        "count_enabled": enabled and mode in {"count", "count_and_entry"},
        "entry_enabled": entry_requested and entry_configured,
        "entry_configured": entry_configured,
        "entry_configuration_missing": entry_requested and not entry_configured,
    }


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
        "person_entry_enabled": bool(row.person_entry_enabled),
        "person_entry_line_config": _json_value(row.person_entry_line_config_json),
        "person_entry_region_config": _json_value(row.person_entry_region_config_json),
        "person_analysis_enabled": (
            None
            if row.person_analysis_enabled is None
            else bool(row.person_analysis_enabled)
        ),
        "person_analysis_mode": row.person_analysis_mode,
        "person_analysis_direction": row.person_analysis_direction,
        "person_analysis_line_config": _json_value(
            row.person_analysis_line_config_json
        ),
        "person_analysis_region_config": _json_value(
            row.person_analysis_region_config_json
        ),
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
        "analysis_type": row.analysis_type,
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


def _person_entry_event_dict(row: PersonEntryEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "analysis_type": row.analysis_type,
        "analysis_case": row.analysis_case,
        "track_id": row.track_id,
        "line_id": row.line_id,
        "direction": row.direction,
        "confidence": row.confidence,
        "bbox": _json_value(row.bbox_json),
        "snapshot_path": row.snapshot_path,
        "crossing_timestamp": row.crossing_timestamp,
        "created_at": row.created_at,
    }


def _person_behavior_event_dict(row: PersonBehaviorEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "algorithm_code": row.algorithm_code,
        "behavior_code": row.behavior_code,
        "track_id": row.track_id,
        "related_track_ids": _json_value(row.related_track_ids_json),
        "confidence": row.confidence,
        "bbox": _json_value(row.bbox_json),
        "metrics": _json_value(row.metrics_json),
        "snapshot_path": row.snapshot_path,
        "event_timestamp": row.event_timestamp,
        "created_at": row.created_at,
    }


def _algorithm_definition_dict(row: AlgorithmDefinition) -> dict[str, Any]:
    return {
        "id": row.id,
        "algorithm_code": row.algorithm_code,
        "algorithm_name": row.algorithm_name,
        "pipeline_code": row.pipeline_code,
        "default_interval": row.default_interval,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _channel_algorithm_config_dict(
    row: ChannelAlgorithmConfig,
    definition: AlgorithmDefinition,
) -> dict[str, Any]:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "algorithm_code": row.algorithm_code,
        "algorithm_name": definition.algorithm_name,
        "pipeline_code": definition.pipeline_code,
        "enabled": bool(row.enabled),
        "analysis_interval": (
            row.analysis_interval
            if row.analysis_interval is not None
            else definition.default_interval
        ),
        "configured_interval": row.analysis_interval,
        "config": _json_value(row.config_json),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
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
        self._ensure_builtin_algorithm_definitions()

    def _ensure_builtin_algorithm_definitions(self) -> None:
        now = _now()
        with self.session_factory() as session:
            definitions = {
                "person_count": ("人员计数及进出统计", 1.0),
                "tailgating": ("尾随闯入", 0.2),
                "loitering": ("井口滞留", 0.5),
            }
            for code, (name, interval) in definitions.items():
                row = session.scalar(
                    select(AlgorithmDefinition).where(
                        AlgorithmDefinition.algorithm_code == code
                    )
                )
                if row is None:
                    session.add(
                        AlgorithmDefinition(
                            algorithm_code=code,
                            algorithm_name=name,
                            pipeline_code="person_yolo11s_bytetrack",
                            default_interval=interval,
                            created_at=now,
                            updated_at=now,
                        )
                    )
            session.commit()

    def get_algorithm_definition(
        self,
        algorithm_code: str,
    ) -> dict[str, Any] | None:
        code = _validate_algorithm_code(algorithm_code)
        with self.session_factory() as session:
            row = session.scalar(
                select(AlgorithmDefinition).where(
                    AlgorithmDefinition.algorithm_code == code
                )
            )
            return _algorithm_definition_dict(row) if row else None

    def list_algorithm_definitions(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(AlgorithmDefinition).order_by(
                    AlgorithmDefinition.algorithm_code.asc()
                )
            ).all()
            return [_algorithm_definition_dict(row) for row in rows]

    def list_channel_algorithm_configs(
        self,
        channel_id: str,
    ) -> list[dict[str, Any]]:
        key = str(channel_id).strip()
        with self.session_factory() as session:
            rows = session.scalars(
                select(ChannelAlgorithmConfig)
                .where(ChannelAlgorithmConfig.channel_id == key)
                .order_by(ChannelAlgorithmConfig.algorithm_code.asc())
            ).all()
            definitions = {
                row.algorithm_code: row
                for row in session.scalars(select(AlgorithmDefinition)).all()
            }
            return [
                _channel_algorithm_config_dict(row, definitions[row.algorithm_code])
                for row in rows
                if row.algorithm_code in definitions
            ]

    def get_channel_algorithm_config(
        self,
        channel_id: str,
        algorithm_code: str,
    ) -> dict[str, Any] | None:
        key = str(channel_id).strip()
        code = _validate_algorithm_code(algorithm_code)
        with self.session_factory() as session:
            row = session.scalar(
                select(ChannelAlgorithmConfig).where(
                    ChannelAlgorithmConfig.channel_id == key,
                    ChannelAlgorithmConfig.algorithm_code == code,
                )
            )
            if row is None:
                return None
            definition = session.scalar(
                select(AlgorithmDefinition).where(
                    AlgorithmDefinition.algorithm_code == code
                )
            )
            if definition is None:
                return None
            return _channel_algorithm_config_dict(row, definition)

    def upsert_channel_algorithm_config(
        self,
        channel_id: str,
        algorithm_code: str,
        *,
        enabled: bool,
        analysis_interval: float | None,
        config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        key = str(channel_id).strip()
        if not key:
            raise ValueError("channel_id is required")
        code = _validate_algorithm_code(algorithm_code)
        configured_interval = _validate_analysis_interval(analysis_interval)
        normalized_config = _validate_algorithm_config(config)
        if code == "loitering":
            normalized_config = _validate_loitering_algorithm_config(
                normalized_config
            )
        now = _now()
        with self.session_factory() as session:
            if session.get(Channel, key) is None:
                raise ValueError("channel not found")
            definition = session.scalar(
                select(AlgorithmDefinition).where(
                    AlgorithmDefinition.algorithm_code == code
                )
            )
            if definition is None:
                raise ValueError("algorithm not found")
            row = session.scalar(
                select(ChannelAlgorithmConfig).where(
                    ChannelAlgorithmConfig.channel_id == key,
                    ChannelAlgorithmConfig.algorithm_code == code,
                )
            )
            if row is None:
                row = ChannelAlgorithmConfig(
                    channel_id=key,
                    algorithm_code=code,
                    enabled=bool(enabled),
                    analysis_interval=configured_interval,
                    config_json=_json_text(normalized_config),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.enabled = bool(enabled)
                row.analysis_interval = configured_interval
                row.config_json = _json_text(normalized_config)
                row.updated_at = now
            session.commit()
            return _channel_algorithm_config_dict(row, definition)

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
            if "person_entry_enabled" in payload:
                row.person_entry_enabled = bool(payload["person_entry_enabled"])
            if "person_entry_direction" in payload:
                row.person_analysis_direction = _validate_person_entry_direction(
                    payload["person_entry_direction"]
                )
            if "person_entry_line_config" in payload:
                row.person_entry_line_config_json = _json_text(
                    payload.get("person_entry_line_config")
                )
            if "person_entry_region_config" in payload:
                row.person_entry_region_config_json = _json_text(
                    payload.get("person_entry_region_config")
                )
            if "person_analysis_enabled" in payload:
                row.person_analysis_enabled = bool(payload["person_analysis_enabled"])
            elif is_new and (
                "person_count_enabled" in payload
                or "person_entry_enabled" in payload
            ):
                row.person_analysis_enabled = bool(
                    payload.get("person_count_enabled")
                    or payload.get("person_entry_enabled")
                )
            if "person_analysis_mode" in payload:
                row.person_analysis_mode = _validate_person_analysis_mode(
                    payload["person_analysis_mode"]
                )
            if "person_analysis_direction" in payload:
                row.person_analysis_direction = _validate_person_entry_direction(
                    payload["person_analysis_direction"]
                )
            elif is_new and (
                "person_count_enabled" in payload
                or "person_entry_enabled" in payload
            ):
                row.person_analysis_mode = _mode_from_legacy_flags(
                    bool(payload.get("person_count_enabled")),
                    bool(payload.get("person_entry_enabled")),
                )
            if "person_analysis_line_config" in payload:
                row.person_analysis_line_config_json = _json_text(
                    payload.get("person_analysis_line_config")
                )
            if "person_analysis_region_config" in payload:
                row.person_analysis_region_config_json = _json_text(
                    payload.get("person_analysis_region_config")
                )
            if (
                not is_new
                and "person_analysis_enabled" not in payload
                and (
                    "person_count_enabled" in payload
                    or "person_entry_enabled" in payload
                )
            ):
                row.person_analysis_enabled = bool(
                    row.person_count_enabled or row.person_entry_enabled
                )
                row.person_analysis_mode = _mode_from_legacy_flags(
                    bool(row.person_count_enabled),
                    bool(row.person_entry_enabled),
                )
                if "person_entry_line_config" in payload:
                    row.person_analysis_line_config_json = row.person_entry_line_config_json
                if "person_entry_region_config" in payload:
                    row.person_analysis_region_config_json = row.person_entry_region_config_json
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

    def update_person_analysis_config(
        self,
        channel_id: str,
        *,
        enabled: bool,
        mode: str = "count_and_entry",
        line_config: dict[str, Any] | None = None,
        region_config: dict[str, Any] | None = None,
        direction: str = "outside_to_inside",
    ) -> dict[str, Any] | None:
        normalized_mode = _validate_person_analysis_mode(mode)
        normalized_direction = _validate_person_entry_direction(direction)
        with self.session_factory() as session:
            row = session.get(Channel, str(channel_id).strip())
            if row is None:
                return None
            row.person_analysis_enabled = bool(enabled)
            row.person_analysis_mode = normalized_mode
            row.person_analysis_direction = normalized_direction
            if line_config is not None:
                row.person_analysis_line_config_json = _json_text(line_config)
                row.person_entry_line_config_json = _json_text(line_config)
            if region_config is not None:
                row.person_analysis_region_config_json = _json_text(region_config)
                row.person_entry_region_config_json = _json_text(region_config)
            row.person_count_enabled = bool(
                enabled and normalized_mode in {"count", "count_and_entry"}
            )
            effective_region = (
                region_config
                if region_config is not None
                else _json_value(row.person_analysis_region_config_json)
            )
            region_points = (
                effective_region.get("points")
                if isinstance(effective_region, dict)
                else None
            )
            entry_configured = bool(line_config) or (
                isinstance(region_points, (list, tuple)) and len(region_points) == 4
            )
            row.person_entry_enabled = bool(
                enabled
                and normalized_mode in {"entry", "count_and_entry"}
                and entry_configured
            )
            row.updated_at = _now()
            session.commit()
            return _channel_dict(row)

    def get_person_analysis_config(self, channel_id: str) -> dict[str, Any] | None:
        channel = self.get_channel(channel_id)
        if channel is None:
            return None
        return effective_person_analysis_config(channel)

    def create_person_entry_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        channel_id = str(payload.get("channel_id") or "").strip()
        if not channel_id:
            raise ValueError("channel_id is required")
        line_id = str(payload.get("line_id") or "").strip()
        if not line_id:
            raise ValueError("line_id is required")
        direction = _validate_person_entry_direction(
            payload.get("direction") or "outside_to_inside"
        )
        # The type is derived from direction so legacy callers that always
        # supplied "01" cannot misclassify outbound events.
        analysis_type = person_entry_analysis_type(direction)
        crossing_timestamp = _coerce_datetime(payload.get("crossing_timestamp"))
        if crossing_timestamp is None:
            raise ValueError("crossing_timestamp is required")
        with self.session_factory() as session:
            if session.get(Channel, channel_id) is None:
                raise ValueError("channel not found")
            raw_analysis_case = payload.get("analysis_case")
            if raw_analysis_case is not None:
                analysis_case = str(raw_analysis_case).strip()
                if not re.fullmatch(r"\d{4}", analysis_case):
                    raise ValueError("analysis_case must be a four-digit code")
            else:
                day_start = crossing_timestamp.replace(
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                day_end = day_start + timedelta(days=1)
                daily_total = session.scalar(
                    select(func.count(PersonEntryEvent.id)).where(
                        PersonEntryEvent.channel_id == channel_id,
                        PersonEntryEvent.direction == direction,
                        PersonEntryEvent.crossing_timestamp >= day_start,
                        PersonEntryEvent.crossing_timestamp < day_end,
                    )
                ) or 0
                next_total = int(daily_total) + 1
                if next_total > 9999:
                    raise ValueError("daily person entry count cannot exceed 9999")
                analysis_case = f"{next_total:04d}"
            now = _now()
            row = PersonEntryEvent(
                id=(int(payload["id"]) if payload.get("id") is not None else None),
                channel_id=channel_id,
                analysis_type=analysis_type,
                analysis_case=analysis_case,
                track_id=(
                    str(payload["track_id"]).strip()
                    if payload.get("track_id") is not None
                    else None
                ),
                line_id=line_id,
                direction=direction,
                confidence=payload.get("confidence"),
                bbox_json=_json_text(payload.get("bbox")),
                snapshot_path=(
                    str(payload["snapshot_path"]).strip()
                    if payload.get("snapshot_path")
                    else None
                ),
                crossing_timestamp=crossing_timestamp,
                created_at=_coerce_datetime(payload.get("created_at")) or now,
            )
            session.add(row)
            session.commit()
            return _person_entry_event_dict(row)

    def list_person_entry_events(
        self,
        channel_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
        start_time: Any = None,
        end_time: Any = None,
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            statement = select(PersonEntryEvent).where(
                PersonEntryEvent.channel_id == str(channel_id).strip()
            )
            start = _coerce_datetime(start_time)
            end = _coerce_datetime(end_time)
            if start is not None:
                statement = statement.where(PersonEntryEvent.crossing_timestamp >= start)
            if end is not None:
                statement = statement.where(PersonEntryEvent.crossing_timestamp <= end)
            total = len(session.scalars(statement).all())
            size = max(int(page_size), 1)
            number = max(int(page), 1)
            rows = session.scalars(
                statement.order_by(PersonEntryEvent.crossing_timestamp.desc())
                .offset((number - 1) * size)
                .limit(size)
            ).all()
            return {
                "page": number,
                "page_size": size,
                "total": total,
                "items": [_person_entry_event_dict(row) for row in rows],
            }

    def get_person_entry_event(self, event_id: int | str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            try:
                normalized_id = int(event_id)
            except (TypeError, ValueError):
                return None
            row = session.get(PersonEntryEvent, normalized_id)
            return _person_entry_event_dict(row) if row else None

    def create_person_behavior_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        channel_id = str(payload.get("channel_id") or "").strip()
        if not channel_id:
            raise ValueError("channel_id is required")
        algorithm_code = _validate_algorithm_code(payload.get("algorithm_code"))
        behavior_code = str(payload.get("behavior_code") or "").strip()
        if not behavior_code:
            raise ValueError("behavior_code is required")
        event_timestamp = _coerce_datetime(payload.get("event_timestamp"))
        if event_timestamp is None:
            raise ValueError("event_timestamp is required")
        with self.session_factory() as session:
            if session.get(Channel, channel_id) is None:
                raise ValueError("channel not found")
            now = _now()
            row = PersonBehaviorEvent(
                channel_id=channel_id,
                algorithm_code=algorithm_code,
                behavior_code=behavior_code,
                track_id=(
                    str(payload["track_id"]).strip()
                    if payload.get("track_id") is not None
                    else None
                ),
                related_track_ids_json=_json_text(payload.get("related_track_ids")),
                confidence=payload.get("confidence"),
                bbox_json=_json_text(payload.get("bbox")),
                metrics_json=_json_text(payload.get("metrics")),
                snapshot_path=(
                    str(payload["snapshot_path"]).strip()
                    if payload.get("snapshot_path")
                    else None
                ),
                event_timestamp=event_timestamp,
                created_at=_coerce_datetime(payload.get("created_at")) or now,
            )
            session.add(row)
            session.commit()
            return _person_behavior_event_dict(row)

    def list_person_behavior_events(
        self,
        channel_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
        start_time: Any = None,
        end_time: Any = None,
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            statement = select(PersonBehaviorEvent).where(
                PersonBehaviorEvent.channel_id == str(channel_id).strip()
            )
            start = _coerce_datetime(start_time)
            end = _coerce_datetime(end_time)
            if start is not None:
                statement = statement.where(PersonBehaviorEvent.event_timestamp >= start)
            if end is not None:
                statement = statement.where(PersonBehaviorEvent.event_timestamp <= end)
            total = int(session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
            size = max(int(page_size), 1)
            number = max(int(page), 1)
            rows = session.scalars(
                statement.order_by(PersonBehaviorEvent.event_timestamp.desc())
                .offset((number - 1) * size)
                .limit(size)
            ).all()
            return {
                "page": number,
                "page_size": size,
                "total": total,
                "items": [_person_behavior_event_dict(row) for row in rows],
            }

    def get_person_behavior_event(self, event_id: int | str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            try:
                normalized_id = int(event_id)
            except (TypeError, ValueError):
                return None
            row = session.get(PersonBehaviorEvent, normalized_id)
            return _person_behavior_event_dict(row) if row else None

    def purge_person_behavior_events_before(
        self,
        cutoff: Any,
        *,
        batch_size: int = 10_000,
    ) -> list[str]:
        boundary = _coerce_datetime(cutoff)
        if boundary is None:
            raise ValueError("cutoff is required")
        size = max(int(batch_size), 1)
        snapshot_paths: list[str] = []
        while True:
            with self.session_factory() as session:
                rows = session.execute(
                    select(PersonBehaviorEvent.id, PersonBehaviorEvent.snapshot_path)
                    .where(PersonBehaviorEvent.event_timestamp < boundary)
                    .order_by(PersonBehaviorEvent.event_timestamp.asc())
                    .limit(size)
                ).all()
                if not rows:
                    return snapshot_paths
                ids = [row[0] for row in rows]
                snapshot_paths.extend(str(row[1]).strip() for row in rows if row[1])
                session.execute(
                    delete(PersonBehaviorEvent).where(PersonBehaviorEvent.id.in_(ids))
                )
                session.commit()

    def count_person_entry_events(
        self,
        channel_id: str,
        *,
        start_time: Any = None,
        end_time: Any = None,
    ) -> int:
        with self.session_factory() as session:
            statement = select(func.count(PersonEntryEvent.id)).where(
                PersonEntryEvent.channel_id == str(channel_id).strip()
            )
            start = _coerce_datetime(start_time)
            end = _coerce_datetime(end_time)
            if start is not None:
                statement = statement.where(PersonEntryEvent.crossing_timestamp >= start)
            if end is not None:
                statement = statement.where(PersonEntryEvent.crossing_timestamp <= end)
            return int(session.scalar(statement) or 0)

    def purge_person_entry_events_before(
        self,
        cutoff: Any,
        *,
        batch_size: int = 10_000,
    ) -> list[str]:
        boundary = _coerce_datetime(cutoff)
        if boundary is None:
            raise ValueError("cutoff is required")
        size = max(int(batch_size), 1)
        snapshot_paths: list[str] = []
        while True:
            with self.session_factory() as session:
                rows = session.execute(
                    select(PersonEntryEvent.id, PersonEntryEvent.snapshot_path)
                    .where(PersonEntryEvent.crossing_timestamp < boundary)
                    .order_by(PersonEntryEvent.crossing_timestamp.asc())
                    .limit(size)
                ).all()
                if not rows:
                    return snapshot_paths
                ids = [row[0] for row in rows]
                snapshot_paths.extend(
                    str(row[1]).strip()
                    for row in rows
                    if row[1]
                )
                session.execute(
                    delete(PersonEntryEvent).where(PersonEntryEvent.id.in_(ids))
                )
                session.commit()

    def create_person_count_record(self, payload: dict[str, Any]) -> dict[str, Any]:
        channel_id = str(payload.get("channel_id") or "").strip()
        if not channel_id:
            raise ValueError("channel_id is required")
        analysis_type = str(payload.get("analysis_type") or "01").strip()
        if analysis_type not in {"01", "02"}:
            raise ValueError('analysis_type must be "01" or "02"')
        analysis_case = str(payload.get("analysis_case") or "").strip()
        if not re.fullmatch(r"\d{4}", analysis_case):
            raise ValueError("analysis_case must be a four-digit code")
        with self.session_factory() as session:
            if session.get(Channel, channel_id) is None:
                raise ValueError("channel not found")
            now = _now()
            row = PersonCountRecord(
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

    def get_person_count_record(self, record_id: int) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.get(PersonCountRecord, int(record_id))
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

    def purge_person_count_records_before(
        self,
        cutoff: Any,
        *,
        batch_size: int = 10_000,
    ) -> list[str]:
        """Delete old records and return their snapshot paths."""
        boundary = _coerce_datetime(cutoff)
        if boundary is None:
            raise ValueError("cutoff is required")
        size = max(int(batch_size), 1)
        snapshot_paths: list[str] = []
        while True:
            with self.session_factory() as session:
                rows = session.execute(
                    select(PersonCountRecord.id, PersonCountRecord.snapshot_path)
                    .where(PersonCountRecord.created_at < boundary)
                    .order_by(PersonCountRecord.id.asc())
                    .limit(size)
                ).all()
                if not rows:
                    return snapshot_paths
                ids = [row[0] for row in rows]
                snapshot_paths.extend(
                    str(row[1]).strip()
                    for row in rows
                    if row[1]
                )
                session.execute(
                    delete(PersonCountRecord).where(PersonCountRecord.id.in_(ids))
                )
                session.commit()

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
            if source_url:
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
                or bool(previous_source != row.source_url and row.source_url)
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

    def persist_health_transition(
        self,
        state_payload: dict[str, Any],
        *,
        event_payload: dict[str, Any] | None = None,
        close_event: bool = False,
        ended_at: Any = None,
    ) -> dict[str, Any]:
        """Persist one health state transition and its event side effect atomically."""
        channel_id = str(state_payload.get("channel_id") or "").strip()
        algorithm_type = str(state_payload.get("algorithm_type") or "").strip()
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
                    id=str(state_payload.get("id") or uuid4().hex),
                    channel_id=channel_id,
                    algorithm_type=algorithm_type,
                    status="normal",
                    updated_at=now,
                )
                session.add(row)
            self._apply_health_state_payload(row, state_payload, now)

            created_event: CameraHealthEvent | None = None
            closed_event: CameraHealthEvent | None = None
            if event_payload is not None:
                active = self._active_health_event_row(
                    session,
                    channel_id,
                    algorithm_type,
                )
                if active is None:
                    created_event = self._new_health_event_row(
                        channel_id,
                        algorithm_type,
                        event_payload,
                        now,
                    )
                    session.add(created_event)

            if close_event:
                active = self._active_health_event_row(
                    session,
                    channel_id,
                    algorithm_type,
                )
                if active is not None:
                    ended = _coerce_datetime(ended_at) or now
                    active.status = "closed"
                    active.ended_at = ended
                    active.duration_seconds = max(
                        (ended - active.started_at).total_seconds(),
                        0.0,
                    )
                    active.updated_at = now
                    closed_event = active

            session.commit()
            return {
                "state": _health_state_dict(row),
                "event": _health_event_dict(created_event) if created_event else None,
                "closed_event": _health_event_dict(closed_event)
                if closed_event
                else None,
            }

    def ensure_health_event_for_state(
        self,
        channel_id: str,
        algorithm_type: str,
        *,
        analysis_case: str,
    ) -> dict[str, Any] | None:
        """Repair an alarm state that has no corresponding active event."""
        key = str(channel_id).strip()
        name = str(algorithm_type).strip()
        code = str(analysis_case or "").strip()
        if len(code) != 4:
            raise ValueError("analysis_case must be a four-character code")
        with self.session_factory() as session:
            state = session.scalar(
                select(CameraHealthState).where(
                    CameraHealthState.channel_id == key,
                    CameraHealthState.algorithm_type == name,
                )
            )
            if state is None or state.status not in {"alarming", "offline"}:
                return None
            active = self._active_health_event_row(session, key, name)
            if active is not None:
                return _health_event_dict(active)
            now = _now()
            event = self._new_health_event_row(
                key,
                name,
                {
                    "analysis_case": code,
                    "metrics_json": _json_value(state.last_metrics_json),
                    "started_at": state.bad_started_at,
                },
                now,
            )
            session.add(event)
            session.commit()
            return _health_event_dict(event)

    @staticmethod
    def _apply_health_state_payload(
        row: CameraHealthState,
        payload: dict[str, Any],
        now: datetime,
    ) -> None:
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

    @staticmethod
    def _active_health_event_row(
        session: Session,
        channel_id: str,
        algorithm_type: str,
    ) -> CameraHealthEvent | None:
        return session.scalar(
            select(CameraHealthEvent)
            .where(
                CameraHealthEvent.channel_id == channel_id,
                CameraHealthEvent.algorithm_type == algorithm_type,
                CameraHealthEvent.status != "closed",
            )
            .order_by(CameraHealthEvent.created_at.desc())
        )

    @staticmethod
    def _new_health_event_row(
        channel_id: str,
        algorithm_type: str,
        payload: dict[str, Any],
        now: datetime,
    ) -> CameraHealthEvent:
        analysis_case = str(payload.get("analysis_case") or "").strip()
        if len(analysis_case) != 4:
            raise ValueError("analysis_case must be a four-character code")
        return CameraHealthEvent(
            id=str(payload.get("id") or uuid4().hex),
            channel_id=channel_id,
            algorithm_type=algorithm_type,
            analysis_type=str(payload.get("analysis_type") or "03").strip(),
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
            created_at=_coerce_datetime(payload.get("created_at")) or now,
            updated_at=now,
        )

    def save_health_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.persist_health_transition(payload)["state"]

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

    def get_health_context(self, channel_id: str) -> dict[str, dict[str, Any]]:
        """Load current states and unclosed events for a channel in one query pair."""
        key = str(channel_id).strip()
        with self.session_factory() as session:
            states = session.scalars(
                select(CameraHealthState).where(CameraHealthState.channel_id == key)
            ).all()
            events = session.scalars(
                select(CameraHealthEvent)
                .where(
                    CameraHealthEvent.channel_id == key,
                    CameraHealthEvent.status != "closed",
                )
                .order_by(CameraHealthEvent.created_at.desc())
            ).all()
            context: dict[str, dict[str, Any]] = {
                row.algorithm_type: {"state": _health_state_dict(row), "event": None}
                for row in states
            }
            for row in events:
                context.setdefault(
                    row.algorithm_type,
                    {"state": None, "event": None},
                )
                if context[row.algorithm_type]["event"] is None:
                    context[row.algorithm_type]["event"] = _health_event_dict(row)
            return context

    def create_health_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        channel_id = str(payload.get("channel_id") or "").strip()
        algorithm_type = str(payload.get("algorithm_type") or "").strip()
        if not channel_id or not algorithm_type:
            raise ValueError("channel_id and algorithm_type are required")
        now = _now()
        with self.session_factory() as session:
            row = self._new_health_event_row(
                channel_id, algorithm_type, payload, now
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
            row = self._active_health_event_row(
                session, str(channel_id).strip(), str(algorithm_type).strip()
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

    def purge_health_events_before(
        self,
        cutoff: Any,
        *,
        batch_size: int = 10_000,
    ) -> list[str]:
        """Delete closed health events older than cutoff and return image paths."""
        boundary = _coerce_datetime(cutoff)
        if boundary is None:
            raise ValueError("cutoff is required")
        size = max(int(batch_size), 1)
        event_paths: list[str] = []
        while True:
            with self.session_factory() as session:
                rows = session.execute(
                    select(CameraHealthEvent.id, CameraHealthEvent.event_path)
                    .where(
                        CameraHealthEvent.status == "closed",
                        CameraHealthEvent.created_at < boundary,
                    )
                    .order_by(CameraHealthEvent.created_at.asc())
                    .limit(size)
                ).all()
                if not rows:
                    return event_paths
                ids = [row[0] for row in rows]
                event_paths.extend(
                    str(row[1]).strip()
                    for row in rows
                    if row[1]
                )
                session.execute(
                    delete(CameraHealthEvent).where(CameraHealthEvent.id.in_(ids))
                )
                session.commit()
