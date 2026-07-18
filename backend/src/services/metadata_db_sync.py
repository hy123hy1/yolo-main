from __future__ import annotations

import copy
import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from src.core.settings import settings
from src.db.database import SessionLocal, engine
from src.db.models import (
    AlgorithmRegionMeta,
    Base,
    ChannelMeta,
    ChannelZoneMeta,
    DeviceMeta,
    EventMeta,
    MediaNodeMeta,
    ModelMetaRecord,
    RecordingMeta,
    TaskAlgorithmMetricHistoryMeta,
    TaskAlgorithmMetricMeta,
    TaskAlgorithmMeta,
    TaskAlgorithmRegionMeta,
    TaskMeta,
    TaskRegionMeta,
)

logger = logging.getLogger(__name__)


def _table_columns(session: Session, table_name: str) -> set[str]:
    inspector = inspect(session.bind)
    try:
        columns = inspector.get_columns(table_name)
    except Exception:
        return set()
    return {str(column.get("name")) for column in columns}


def _add_column_if_missing(session: Session, table_name: str, column_name: str, ddl: str) -> None:
    columns = _table_columns(session, table_name)
    if column_name in columns:
        return
    session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {ddl}"))


def _table_exists(session: Session, table_name: str) -> bool:
    inspector = inspect(session.bind)
    try:
        return bool(inspector.has_table(table_name))
    except Exception:
        return False


def _table_column_map(session: Session, table_name: str) -> dict[str, dict]:
    inspector = inspect(session.bind)
    try:
        columns = inspector.get_columns(table_name)
    except Exception:
        return {}
    return {
        str(column.get("name")): column
        for column in columns
        if column.get("name")
    }


def _is_integer_like_column(column: dict | None) -> bool:
    if not column:
        return False
    column_type = column.get("type")
    try:
        if column_type.python_type is int:
            return True
    except Exception:
        pass
    type_name = str(column_type or "").lower()
    return "int" in type_name and "char" not in type_name and "text" not in type_name


def _normalize_metric_scope_key(region_id: str | None, scope_key: str | None = None) -> str:
    normalized_scope = str(scope_key or "").strip()
    if normalized_scope:
        return normalized_scope
    normalized_region = str(region_id or "").strip()
    return normalized_region or "global"


def _normalize_json_value(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return copy.deepcopy(value)
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _normalize_datetime_value(value):
    if isinstance(value, datetime) or value is None:
        return value
    if isinstance(value, str):
        return _parse_dt(value)
    return value


def _sanitize_channel_payload(payload: dict) -> dict:
    sanitized = copy.deepcopy(payload)
    sanitized.pop("ai_enabled", None)
    ext = sanitized.get("ext")
    if isinstance(ext, dict):
        ext.pop("ai_config", None)
    return sanitized


def _rename_table(session: Session, source_table: str, target_table: str, dialect_name: str) -> None:
    if dialect_name == "mysql":
        session.execute(text(f"RENAME TABLE {source_table} TO {target_table}"))
        return
    session.execute(text(f"ALTER TABLE {source_table} RENAME TO {target_table}"))


def _drop_table_if_exists(session: Session, table_name: str) -> None:
    if not _table_exists(session, table_name):
        return
    session.execute(text(f"DROP TABLE {table_name}"))


def _table_row_count(session: Session, table_name: str) -> int:
    if not _table_exists(session, table_name):
        return 0
    result = session.execute(text(f"SELECT COUNT(1) FROM {table_name}")).scalar()
    return int(result or 0)


def _restore_table_from_backup_if_needed(
    session: Session,
    *,
    table_name: str,
    backup_table: str,
    dialect_name: str,
) -> None:
    if not _table_exists(session, backup_table):
        return

    if _table_exists(session, table_name):
        if _table_row_count(session, table_name) == 0:
            _drop_table_if_exists(session, table_name)
            session.commit()
            _rename_table(session, backup_table, table_name, dialect_name)
            session.commit()
            return
        _drop_table_if_exists(session, backup_table)
        session.commit()
        return

    _rename_table(session, backup_table, table_name, dialect_name)
    session.commit()


def _load_realtime_metric_rows(session: Session) -> list[dict]:
    if not _table_exists(session, "task_algorithm_metrics"):
        return []
    rows = session.execute(text("SELECT * FROM task_algorithm_metrics")).mappings().all()
    deduped: dict[tuple[str | None, str | None, str | None, str], dict] = {}
    for row in sorted(
        rows,
        key=lambda item: (
            str(item.get("updated_at") or ""),
            str(item.get("id") or ""),
        ),
    ):
        payload = dict(row)
        payload.pop("id", None)
        payload["scope_key"] = _normalize_metric_scope_key(
            payload.get("region_id"),
            payload.get("scope_key"),
        )
        payload["updated_at"] = _normalize_datetime_value(payload.get("updated_at"))
        payload["raw_json"] = _normalize_json_value(payload.get("raw_json"))
        business_key = (
            payload.get("task_id"),
            payload.get("algorithm_id"),
            payload.get("metric_type"),
            payload["scope_key"],
        )
        deduped[business_key] = payload
    return list(deduped.values())


def _load_metric_history_rows(session: Session) -> list[dict]:
    if not _table_exists(session, "task_algorithm_metric_history"):
        return []
    rows = session.execute(text("SELECT * FROM task_algorithm_metric_history")).mappings().all()
    normalized_rows: list[dict] = []
    for row in rows:
        payload = dict(row)
        payload.pop("id", None)
        payload["sampled_at"] = _normalize_datetime_value(payload.get("sampled_at"))
        payload["created_at"] = _normalize_datetime_value(payload.get("created_at"))
        payload["raw_json"] = _normalize_json_value(payload.get("raw_json"))
        normalized_rows.append(payload)
    return normalized_rows


def _rebuild_realtime_metric_table(session: Session, dialect_name: str) -> None:
    if not _table_exists(session, "task_algorithm_metrics"):
        TaskAlgorithmMetricMeta.__table__.create(bind=session.bind, checkfirst=True)
        return

    rows = _load_realtime_metric_rows(session)
    backup_table = "task_algorithm_metrics__bak"

    _drop_table_if_exists(session, backup_table)
    session.commit()

    _rename_table(session, "task_algorithm_metrics", backup_table, dialect_name)
    session.commit()

    TaskAlgorithmMetricMeta.__table__.create(bind=session.bind, checkfirst=False)
    session.commit()

    if rows:
        session.execute(TaskAlgorithmMetricMeta.__table__.insert(), rows)
        session.commit()

    _drop_table_if_exists(session, backup_table)
    session.commit()


def _rebuild_metric_history_table(session: Session, dialect_name: str) -> None:
    if not _table_exists(session, "task_algorithm_metric_history"):
        TaskAlgorithmMetricHistoryMeta.__table__.create(bind=session.bind, checkfirst=True)
        return

    rows = _load_metric_history_rows(session)
    backup_table = "task_algorithm_metric_history__bak"

    _drop_table_if_exists(session, backup_table)
    session.commit()

    _rename_table(session, "task_algorithm_metric_history", backup_table, dialect_name)
    session.commit()

    TaskAlgorithmMetricHistoryMeta.__table__.create(bind=session.bind, checkfirst=False)
    session.commit()

    if rows:
        session.execute(TaskAlgorithmMetricHistoryMeta.__table__.insert(), rows)
        session.commit()

    _drop_table_if_exists(session, backup_table)
    session.commit()


def _ensure_metric_tables_v2(session: Session, dialect_name: str) -> None:
    _restore_table_from_backup_if_needed(
        session,
        table_name="task_algorithm_metrics",
        backup_table="task_algorithm_metrics__bak",
        dialect_name=dialect_name,
    )
    _restore_table_from_backup_if_needed(
        session,
        table_name="task_algorithm_metric_history",
        backup_table="task_algorithm_metric_history__bak",
        dialect_name=dialect_name,
    )

    realtime_columns = _table_column_map(session, "task_algorithm_metrics")
    history_columns = _table_column_map(session, "task_algorithm_metric_history")

    realtime_needs_rebuild = bool(realtime_columns) and (
        "scope_key" not in realtime_columns
        or not _is_integer_like_column(realtime_columns.get("id"))
    )
    history_needs_rebuild = bool(history_columns) and not _is_integer_like_column(history_columns.get("id"))

    if realtime_needs_rebuild:
        _rebuild_realtime_metric_table(session, dialect_name)
    if history_needs_rebuild:
        _rebuild_metric_history_table(session, dialect_name)


def _cleanup_orphan_tasks(session: Session) -> None:
    orphan_task_ids = [
        str(task_id)
        for task_id in session.execute(
            text(
                """
                SELECT t.id
                FROM tasks t
                LEFT JOIN channels c ON c.id = t.channel_id
                WHERE t.channel_id IS NOT NULL
                  AND t.channel_id != ''
                  AND c.id IS NULL
                """
            )
        ).scalars().all()
    ]
    if not orphan_task_ids:
        return

    for task_id in orphan_task_ids:
        session.query(TaskAlgorithmMetricHistoryMeta).filter(TaskAlgorithmMetricHistoryMeta.task_id == task_id).delete()
        session.query(TaskAlgorithmMetricMeta).filter(TaskAlgorithmMetricMeta.task_id == task_id).delete()
        session.query(AlgorithmRegionMeta).filter(AlgorithmRegionMeta.task_id == task_id).delete()
        session.query(TaskAlgorithmRegionMeta).filter(TaskAlgorithmRegionMeta.task_id == task_id).delete()
        session.query(TaskAlgorithmMeta).filter(TaskAlgorithmMeta.task_id == task_id).delete()
        session.query(TaskRegionMeta).filter(TaskRegionMeta.task_id == task_id).delete()
        session.query(TaskMeta).filter(TaskMeta.id == task_id).delete()


def _mysql_foreign_key_exists(session: Session, table_name: str, column_name: str, referenced_table: str) -> bool:
    database_name = session.execute(text("SELECT DATABASE()")).scalar()
    if not database_name:
        return False

    result = session.execute(
        text(
            """
            SELECT COUNT(1)
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = :database_name
              AND TABLE_NAME = :table_name
              AND COLUMN_NAME = :column_name
              AND REFERENCED_TABLE_NAME = :referenced_table
            """
        ),
        {
            "database_name": database_name,
            "table_name": table_name,
            "column_name": column_name,
            "referenced_table": referenced_table,
        },
    ).scalar()
    return bool(result)


def _ensure_mysql_task_channel_fk(session: Session) -> None:
    _cleanup_orphan_tasks(session)
    if _mysql_foreign_key_exists(session, "tasks", "channel_id", "channels"):
        return

    session.execute(text("ALTER TABLE tasks ADD CONSTRAINT fk_tasks_channel_id FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE"))


def _cleanup_orphan_task_algorithm_models(session: Session) -> None:
    orphan_algorithm_ids = [
        str(algorithm_id)
        for algorithm_id in session.execute(
            text(
                """
                SELECT ta.id
                FROM task_algorithms ta
                LEFT JOIN models m ON m.id = ta.model_id
                WHERE ta.model_id IS NOT NULL
                  AND ta.model_id != ''
                  AND m.id IS NULL
                """
            )
        ).scalars().all()
    ]
    if not orphan_algorithm_ids:
        return

    for algorithm_id in orphan_algorithm_ids:
        session.query(TaskAlgorithmMetricMeta).filter(TaskAlgorithmMetricMeta.algorithm_id == algorithm_id).delete()
        session.query(AlgorithmRegionMeta).filter(AlgorithmRegionMeta.algorithm_id == algorithm_id).delete()
        session.query(TaskAlgorithmRegionMeta).filter(TaskAlgorithmRegionMeta.algorithm_id == algorithm_id).delete()
        session.query(TaskAlgorithmMeta).filter(TaskAlgorithmMeta.id == algorithm_id).delete()


def _ensure_mysql_task_algorithm_model_fk(session: Session) -> None:
    _cleanup_orphan_task_algorithm_models(session)
    if _mysql_foreign_key_exists(session, "task_algorithms", "model_id", "models"):
        return

    session.execute(
        text(
            "ALTER TABLE task_algorithms "
            "ADD CONSTRAINT fk_task_algorithms_model_id "
            "FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE RESTRICT"
        )
    )


def migrate_metadata_database_schema() -> None:
    if not settings.METADATA_DB_ENABLED:
        return

    with SessionLocal() as session:
        Base.metadata.create_all(bind=engine, tables=[
            TaskAlgorithmMetricMeta.__table__,
            TaskAlgorithmMetricHistoryMeta.__table__,
        ])
        dialect_name = (session.bind.dialect.name or "").lower()
        _ensure_metric_tables_v2(session, dialect_name)

        if dialect_name == "sqlite":
            _add_column_if_missing(session, "channels", "webrtc_api", "webrtc_api TEXT")
            _add_column_if_missing(session, "channels", "hls", "hls TEXT")
            _add_column_if_missing(session, "channels", "snapshot_path", "snapshot_path TEXT")
            _add_column_if_missing(session, "models", "version", "version VARCHAR(64)")
            _add_column_if_missing(session, "models", "confidence", "confidence FLOAT")
            _add_column_if_missing(session, "models", "alert_interval", "alert_interval INTEGER")
            _add_column_if_missing(session, "tasks", "channel_name", "channel_name VARCHAR(255)")
            _add_column_if_missing(session, "tasks", "region_count", "region_count INTEGER")
            _add_column_if_missing(session, "tasks", "is_running", "is_running BOOLEAN")
            _add_column_if_missing(session, "tasks", "started_at", "started_at DATETIME")
            _add_column_if_missing(session, "tasks", "stopped_at", "stopped_at DATETIME")
            _add_column_if_missing(session, "tasks", "last_analysis_at", "last_analysis_at DATETIME")
            _add_column_if_missing(session, "tasks", "last_event_at", "last_event_at DATETIME")
            _add_column_if_missing(session, "tasks", "last_error", "last_error TEXT")
            _add_column_if_missing(session, "task_algorithms", "scene_key", "scene_key VARCHAR(128)")
            _add_column_if_missing(session, "task_algorithms", "scene_name", "scene_name VARCHAR(255)")
            _add_column_if_missing(session, "task_algorithms", "rule_type", "rule_type VARCHAR(64)")
            _add_column_if_missing(session, "task_algorithms", "detect_all_classes", "detect_all_classes BOOLEAN")
            _add_column_if_missing(session, "task_algorithms", "label_source", "label_source VARCHAR(32)")
            _add_column_if_missing(session, "task_algorithms", "requires_region", "requires_region BOOLEAN")
            _add_column_if_missing(session, "task_algorithms", "postprocess_config_json", "postprocess_config_json JSON")
            _add_column_if_missing(session, "events", "task_id", "task_id VARCHAR(64)")
            _add_column_if_missing(session, "events", "algorithm_id", "algorithm_id VARCHAR(64)")
            _add_column_if_missing(session, "events", "model_id", "model_id VARCHAR(64)")
            _add_column_if_missing(session, "events", "video_path", "video_path TEXT")
            _add_column_if_missing(session, "events", "handling_status", "handling_status INTEGER")
            _add_column_if_missing(session, "recordings", "task_id", "task_id VARCHAR(64)")
            _add_column_if_missing(session, "recordings", "algorithm_id", "algorithm_id VARCHAR(64)")
        else:
            _add_column_if_missing(session, "channels", "webrtc_api", "webrtc_api TEXT")
            _add_column_if_missing(session, "channels", "hls", "hls TEXT")
            _add_column_if_missing(session, "channels", "snapshot_path", "snapshot_path TEXT")
            _add_column_if_missing(session, "models", "version", "version VARCHAR(64)")
            _add_column_if_missing(session, "models", "confidence", "confidence FLOAT")
            _add_column_if_missing(session, "models", "alert_interval", "alert_interval INTEGER")
            _add_column_if_missing(session, "tasks", "channel_name", "channel_name VARCHAR(255)")
            _add_column_if_missing(session, "tasks", "region_count", "region_count INTEGER")
            _add_column_if_missing(session, "tasks", "is_running", "is_running BOOLEAN")
            _add_column_if_missing(session, "tasks", "started_at", "started_at DATETIME")
            _add_column_if_missing(session, "tasks", "stopped_at", "stopped_at DATETIME")
            _add_column_if_missing(session, "tasks", "last_analysis_at", "last_analysis_at DATETIME")
            _add_column_if_missing(session, "tasks", "last_event_at", "last_event_at DATETIME")
            _add_column_if_missing(session, "tasks", "last_error", "last_error TEXT")
            _add_column_if_missing(session, "task_algorithms", "scene_key", "scene_key VARCHAR(128)")
            _add_column_if_missing(session, "task_algorithms", "scene_name", "scene_name VARCHAR(255)")
            _add_column_if_missing(session, "task_algorithms", "rule_type", "rule_type VARCHAR(64)")
            _add_column_if_missing(session, "task_algorithms", "detect_all_classes", "detect_all_classes BOOLEAN")
            _add_column_if_missing(session, "task_algorithms", "label_source", "label_source VARCHAR(32)")
            _add_column_if_missing(session, "task_algorithms", "requires_region", "requires_region BOOLEAN")
            _add_column_if_missing(session, "task_algorithms", "postprocess_config_json", "postprocess_config_json JSON")
            _add_column_if_missing(session, "events", "task_id", "task_id VARCHAR(64)")
            _add_column_if_missing(session, "events", "algorithm_id", "algorithm_id VARCHAR(64)")
            _add_column_if_missing(session, "events", "model_id", "model_id VARCHAR(64)")
            _add_column_if_missing(session, "events", "video_path", "video_path TEXT")
            _add_column_if_missing(session, "events", "handling_status", "handling_status INTEGER")
            _add_column_if_missing(session, "recordings", "task_id", "task_id VARCHAR(64)")
            _add_column_if_missing(session, "recordings", "algorithm_id", "algorithm_id VARCHAR(64)")
            if dialect_name == "mysql":
                _ensure_mysql_task_channel_fk(session)
                _ensure_mysql_task_algorithm_model_fk(session)

        session.execute(
            text(
                """
                UPDATE models
                SET confidence = COALESCE(confidence, :default_confidence),
                    alert_interval = COALESCE(alert_interval, :default_alert_interval)
                """
            ),
            {
                "default_confidence": 0.25,
                "default_alert_interval": 60,
            },
        )
        session.execute(
            text(
                """
                UPDATE tasks
                SET channel_name = COALESCE(
                    NULLIF(channel_name, ''),
                    (SELECT channels.name FROM channels WHERE channels.id = tasks.channel_id)
                )
                WHERE channel_id IS NOT NULL
                  AND (channel_name IS NULL OR channel_name = '')
                """
            )
        )
        session.execute(
            text(
                """
                UPDATE task_algorithms
                SET video_before_seconds = :before_seconds,
                    video_after_seconds = :after_seconds
                WHERE COALESCE(need_video_clip, 0) = 1
                  AND (
                    COALESCE(video_before_seconds, -1) <> :before_seconds
                    OR COALESCE(video_after_seconds, -1) <> :after_seconds
                  )
                """
            ),
            {
                "before_seconds": max(int(settings.ALARM_VIDEO_BEFORE_SECONDS or 0), 0),
                "after_seconds": max(int(settings.ALARM_VIDEO_AFTER_SECONDS or 0), 0),
            },
        )
        session.commit()


def _parse_dt(value: str | None):
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _upsert(session: Session, model) -> None:
    session.merge(model)


def _sync_device(session: Session, payload: dict) -> None:
    _upsert(
        session,
        DeviceMeta(
            id=payload["id"],
            name=payload.get("name"),
            type=payload.get("type"),
            host=payload.get("host"),
            username=payload.get("username"),
            password=payload.get("password"),
            remark=payload.get("remark"),
            status=payload.get("status"),
            created_at=_parse_dt(payload.get("created_at")),
            updated_at=_parse_dt(payload.get("updated_at")),
            raw_json=copy.deepcopy(payload),
        ),
    )


def _sync_media_node(session: Session, payload: dict) -> None:
    _upsert(
        session,
        MediaNodeMeta(
            id=payload["id"],
            name=payload.get("name"),
            type=payload.get("type"),
            api_base=payload.get("api_base"),
            play_base=payload.get("play_base"),
            webrtc_base=payload.get("webrtc_base"),
            transcode_rtmp_host=payload.get("transcode_rtmp_host"),
            public_rtsp_host=payload.get("public_rtsp_host"),
            public_rtmp_host=payload.get("public_rtmp_host"),
            public_http_host=payload.get("public_http_host"),
            rtsp_port=payload.get("rtsp_port"),
            rtmp_port=payload.get("rtmp_port"),
            http_port=payload.get("http_port"),
            https_port=payload.get("https_port"),
            hls_file=payload.get("hls_file"),
            httpflv_suffix=payload.get("httpflv_suffix"),
            webrtc_path=payload.get("webrtc_path"),
            secret=payload.get("secret"),
            is_default=payload.get("is_default"),
            status=payload.get("status"),
            created_at=_parse_dt(payload.get("created_at")),
            updated_at=_parse_dt(payload.get("updated_at")),
            raw_json=copy.deepcopy(payload),
        ),
    )


def _sync_channel(session: Session, payload: dict) -> None:
    payload = _sanitize_channel_payload(payload)
    _upsert(
        session,
        ChannelMeta(
            id=payload["id"],
            device_id=payload.get("device_id"),
            media_node_id=payload.get("media_node_id"),
            name=payload.get("name"),
            type=payload.get("type"),
            source_url=payload.get("source_url"),
            app=payload.get("app"),
            stream=payload.get("stream"),
            is_online=payload.get("is_online"),
            is_playing=payload.get("is_playing"),
            record_mode=payload.get("record_mode"),
            snapshot_url=payload.get("snapshot_url"),
            snapshot_path=payload.get("snapshot_path"),
            webrtc_api=payload.get("webrtc_api"),
            hls=payload.get("hls"),
            created_at=_parse_dt(payload.get("created_at")),
            updated_at=_parse_dt(payload.get("updated_at")),
            raw_json=copy.deepcopy(payload),
        ),
    )
    session.flush()

    session.query(ChannelZoneMeta).filter(ChannelZoneMeta.channel_id == payload["id"]).delete()
    for zone in payload.get("ext", {}).get("zones", []) or []:
        if not zone.get("id"):
            continue
        session.add(
            ChannelZoneMeta(
                id=zone["id"],
                channel_id=payload["id"],
                name=zone.get("name"),
                color=zone.get("color"),
                labels_json=copy.deepcopy(zone.get("labels") or []),
                coordinates_json=copy.deepcopy(zone.get("coordinates") or []),
                raw_json=copy.deepcopy(zone),
            )
        )

def _sync_event(session: Session, payload: dict) -> None:
    _upsert(
        session,
        EventMeta(
            id=payload["id"],
            channel_id=payload.get("channel_id"),
            task_id=payload.get("task_id"),
            algorithm_id=payload.get("algorithm_id"),
            model_id=payload.get("model_id"),
            event_type=payload.get("event_type"),
            class_name=payload.get("class_name"),
            conf=payload.get("conf"),
            snapshot_path=payload.get("snapshot_path"),
            video_path=payload.get("video_path"),
            handling_status=payload.get("handling_status"),
            remark=payload.get("remark"),
            status=payload.get("status"),
            created_at=_parse_dt(payload.get("created_at")),
            updated_at=_parse_dt(payload.get("updated_at")),
            raw_json=copy.deepcopy(payload),
        ),
    )


def _sync_recording(session: Session, payload: dict) -> None:
    _upsert(
        session,
        RecordingMeta(
            id=payload["id"],
            channel_id=payload.get("channel_id"),
            task_id=payload.get("task_id") or (payload.get("raw_json") or {}).get("task_id"),
            algorithm_id=payload.get("algorithm_id") or (payload.get("raw_json") or {}).get("algorithm_id"),
            started_at=_parse_dt(payload.get("started_at")),
            ended_at=_parse_dt(payload.get("ended_at")),
            duration=payload.get("duration"),
            file_path=payload.get("file_path") or payload.get("path"),
            status=payload.get("status"),
            created_at=_parse_dt(payload.get("created_at")),
            updated_at=_parse_dt(payload.get("updated_at")),
            raw_json=copy.deepcopy(payload),
        ),
    )


def _sync_model(session: Session, payload: dict) -> None:
    model_id = payload.get("model_id") or payload.get("id")
    if not model_id:
        return
    _upsert(
        session,
        ModelMetaRecord(
            id=model_id,
            name=payload.get("name"),
            version=payload.get("version"),
            source=payload.get("source"),
            description=payload.get("description"),
            tags_json=copy.deepcopy(payload.get("tags") or []),
            classes_json=copy.deepcopy(payload.get("classes") or []),
            weights_path=payload.get("weights_path"),
            file_size=payload.get("file_size"),
            file_size_mb=payload.get("file_size_mb"),
            job_id=payload.get("job_id"),
            confidence=payload.get("confidence"),
            alert_interval=payload.get("alert_interval"),
            created_at=_parse_dt(payload.get("created_at")),
            updated_at=_parse_dt(payload.get("updated_at")),
            raw_json=copy.deepcopy(payload),
        ),
    )


def _sync_task(session: Session, payload: dict) -> None:
    runtime = payload.get("runtime") or {}
    algorithms = list(payload.get("algorithms") or [])
    regions_by_id: dict[str, dict] = {}
    for algorithm in algorithms:
        if not isinstance(algorithm, dict):
            continue
        for region in list(algorithm.get("regions") or []):
            if not isinstance(region, dict):
                continue
            region_id = region.get("id")
            if not region_id or region_id in regions_by_id:
                continue
            regions_by_id[region_id] = copy.deepcopy(region)

    # Compatibility projection only: task-level regions are derived from algorithm-owned regions.
    regions = list(regions_by_id.values())

    _upsert(
        session,
        TaskMeta(
            id=payload["id"],
            name=payload.get("name"),
            source_url=payload.get("source_url"),
            source_type=payload.get("source_type"),
            channel_id=payload.get("channel_id"),
            channel_name=payload.get("channel_name"),
            frame_interval=payload.get("frame_interval"),
            status=payload.get("status"),
            algorithm_count=len(algorithms),
            region_count=len(regions),
            is_running=runtime.get("is_running"),
            started_at=_parse_dt(runtime.get("started_at")),
            stopped_at=_parse_dt(runtime.get("stopped_at")),
            last_analysis_at=_parse_dt(runtime.get("last_analysis_at")),
            last_event_at=_parse_dt(runtime.get("last_event_at")),
            last_error=runtime.get("last_error"),
            created_at=_parse_dt(payload.get("created_at")),
            updated_at=_parse_dt(payload.get("updated_at")),
            raw_json=copy.deepcopy(payload),
        ),
    )
    session.flush()

    session.query(AlgorithmRegionMeta).filter(AlgorithmRegionMeta.task_id == payload["id"]).delete()
    session.query(TaskAlgorithmRegionMeta).filter(TaskAlgorithmRegionMeta.task_id == payload["id"]).delete()
    session.query(TaskAlgorithmMeta).filter(TaskAlgorithmMeta.task_id == payload["id"]).delete()
    session.query(TaskRegionMeta).filter(TaskRegionMeta.task_id == payload["id"]).delete()

    for index, algorithm in enumerate(algorithms):
        algorithm_id = algorithm.get("id")
        if not algorithm_id:
            continue
        session.add(
            TaskAlgorithmMeta(
                id=algorithm_id,
                task_id=payload["id"],
                algorithm_type=algorithm.get("algorithm_type"),
                scene_key=algorithm.get("scene_key"),
                scene_name=algorithm.get("scene_name"),
                rule_type=algorithm.get("rule_type"),
                model_id=algorithm.get("model_id"),
                enabled=algorithm.get("enabled"),
                labels_json=copy.deepcopy(algorithm.get("labels") or []),
                detect_all_classes=algorithm.get("detect_all_classes"),
                label_source=algorithm.get("label_source"),
                confidence=algorithm.get("confidence"),
                alert_interval=algorithm.get("alert_interval"),
                need_snapshot=algorithm.get("need_snapshot"),
                need_video_clip=algorithm.get("need_video_clip"),
                video_before_seconds=algorithm.get("video_before_seconds"),
                video_after_seconds=algorithm.get("video_after_seconds"),
                need_overlay=algorithm.get("need_overlay"),
                requires_region=algorithm.get("requires_region"),
                overlay_color=algorithm.get("overlay_color"),
                postprocess_config_json=copy.deepcopy(algorithm.get("postprocess_config") or {}),
                sort_order=index,
                created_at=_parse_dt(algorithm.get("created_at")) or _parse_dt(payload.get("created_at")),
                updated_at=_parse_dt(payload.get("updated_at")),
                raw_json=copy.deepcopy(algorithm),
            )
        )

    # Flush parent algorithm rows first so child region inserts satisfy FK constraints.
    session.flush()

    for algorithm in algorithms:
        algorithm_id = algorithm.get("id")
        if not algorithm_id:
            continue

        for region in list(algorithm.get("regions") or []):
            region_id = region.get("id")
            if not region_id:
                continue
            session.add(
                AlgorithmRegionMeta(
                    id=region_id,
                    task_id=payload["id"],
                    algorithm_id=algorithm_id,
                    name=region.get("name"),
                    type=region.get("type"),
                    color=region.get("color"),
                    coordinates_json=copy.deepcopy(region.get("coordinates") or []),
                    enabled=region.get("enabled"),
                    created_at=_parse_dt(region.get("created_at")) or _parse_dt(payload.get("created_at")),
                    updated_at=_parse_dt(payload.get("updated_at")),
                    raw_json=copy.deepcopy(region),
                )
            )

    for region in regions:
        region_id = region.get("id")
        if not region_id:
            continue
        session.add(
            TaskRegionMeta(
                id=region_id,
                task_id=payload["id"],
                name=region.get("name"),
                type=region.get("type"),
                color=region.get("color"),
                coordinates_json=copy.deepcopy(region.get("coordinates") or []),
                enabled=region.get("enabled"),
                created_at=_parse_dt(region.get("created_at")) or _parse_dt(payload.get("created_at")),
                updated_at=_parse_dt(payload.get("updated_at")),
                raw_json=copy.deepcopy(region),
            )
        )
    session.flush()

    for algorithm in algorithms:
        algorithm_id = algorithm.get("id")
        if not algorithm_id:
            continue
        algorithm_region_ids = [
            region.get("id")
            for region in list(algorithm.get("regions") or [])
            if isinstance(region, dict) and region.get("id")
        ]
        for link_index, region_id in enumerate(algorithm_region_ids, start=1):
            session.add(
                TaskAlgorithmRegionMeta(
                    id=f"{payload['id']}_{algorithm_id}_{region_id}_{link_index}",
                    task_id=payload["id"],
                    algorithm_id=algorithm_id,
                    region_id=region_id,
                    created_at=_parse_dt(payload.get("updated_at")) or _parse_dt(payload.get("created_at")),
                )
            )


def _sync_by_path(session: Session, path: Path, payload: dict, *, allow_task_sync: bool = False) -> None:
    if path.parent == settings.DEVICES_DIR:
        _sync_device(session, payload)
    elif path.parent == settings.MEDIA_NODES_DIR:
        _sync_media_node(session, payload)
    elif path.parent == settings.CHANNELS_DIR:
        _sync_channel(session, payload)
    elif path.parent == settings.EVENTS_DIR:
        _sync_event(session, payload)
    elif path.parent == settings.RECORDINGS_DIR:
        _sync_recording(session, payload)
    elif path.parent == settings.TASKS_DIR:
        if allow_task_sync:
            _sync_task(session, payload)
    elif path.name == "model.json" and settings.REGISTRY_DIR in path.parents:
        _sync_model(session, payload)


def sync_json_document(path: Path, payload: dict) -> None:
    if not settings.METADATA_DB_ENABLED:
        return

    with SessionLocal() as session:
        _sync_by_path(session, path, payload, allow_task_sync=False)
        session.commit()


def sync_single_metadata_file(path: Path) -> bool:
    if not settings.METADATA_DB_ENABLED or not path.exists():
        return False
    if path.parent == settings.TASKS_DIR:
        logger.info("Skip task JSON -> metadata sync in DB-first mode: %s", path)
        return False

    from src.services.platform_storage import load_json

    with SessionLocal() as session:
        _sync_by_path(session, path, load_json(path), allow_task_sync=False)
        session.commit()
    return True


def delete_json_document(path: Path, item_id: str | None) -> None:
    if not settings.METADATA_DB_ENABLED or not item_id:
        return

    with SessionLocal() as session:
        if path.parent == settings.CHANNELS_DIR:
            session.query(ChannelZoneMeta).filter(ChannelZoneMeta.channel_id == item_id).delete()
            session.query(ChannelMeta).filter(ChannelMeta.id == item_id).delete()
        elif path.parent == settings.DEVICES_DIR:
            session.query(DeviceMeta).filter(DeviceMeta.id == item_id).delete()
        elif path.parent == settings.MEDIA_NODES_DIR:
            session.query(MediaNodeMeta).filter(MediaNodeMeta.id == item_id).delete()
        elif path.parent == settings.EVENTS_DIR:
            session.query(EventMeta).filter(EventMeta.id == item_id).delete()
        elif path.parent == settings.RECORDINGS_DIR:
            session.query(RecordingMeta).filter(RecordingMeta.id == item_id).delete()
        elif path.parent == settings.TASKS_DIR:
            session.query(TaskAlgorithmMetricHistoryMeta).filter(TaskAlgorithmMetricHistoryMeta.task_id == item_id).delete()
            session.query(TaskAlgorithmMetricMeta).filter(TaskAlgorithmMetricMeta.task_id == item_id).delete()
            session.query(AlgorithmRegionMeta).filter(AlgorithmRegionMeta.task_id == item_id).delete()
            session.query(TaskAlgorithmRegionMeta).filter(TaskAlgorithmRegionMeta.task_id == item_id).delete()
            session.query(TaskAlgorithmMeta).filter(TaskAlgorithmMeta.task_id == item_id).delete()
            session.query(TaskRegionMeta).filter(TaskRegionMeta.task_id == item_id).delete()
            session.query(TaskMeta).filter(TaskMeta.id == item_id).delete()
        elif path.name == "model.json":
            session.query(ModelMetaRecord).filter(ModelMetaRecord.id == item_id).delete()
        session.commit()


def sync_all_metadata_from_files() -> None:
    from src.services.platform_storage import load_json

    with SessionLocal() as session:
        should_bootstrap_channels = False
        if settings.CHANNELS_DIR.exists():
            channel_file_exists = any(settings.CHANNELS_DIR.glob("*.json"))
            if channel_file_exists:
                existing_channel_count = session.query(ChannelMeta.id).limit(1).count()
                should_bootstrap_channels = existing_channel_count == 0

        should_bootstrap_tasks = False
        if settings.TASKS_DIR.exists():
            task_file_exists = any(settings.TASKS_DIR.glob("*.json"))
            if task_file_exists:
                existing_task_count = session.query(TaskMeta.id).limit(1).count()
                should_bootstrap_tasks = existing_task_count == 0

        for directory in [
            settings.DEVICES_DIR,
            settings.MEDIA_NODES_DIR,
            settings.CHANNELS_DIR,
            settings.TASKS_DIR,
            settings.EVENTS_DIR,
            settings.RECORDINGS_DIR,
        ]:
            if not directory.exists():
                continue
            for path in directory.glob("*.json"):
                try:
                    if directory == settings.CHANNELS_DIR and not should_bootstrap_channels:
                        continue
                    _sync_by_path(
                        session,
                        path,
                        load_json(path),
                        allow_task_sync=(directory == settings.TASKS_DIR and should_bootstrap_tasks),
                    )
                except Exception:
                    session.rollback()
                    logger.exception("Failed to sync metadata file: %s", path)

        if settings.REGISTRY_DIR.exists():
            for model_dir in settings.REGISTRY_DIR.iterdir():
                model_file = model_dir / "model.json"
                if not model_file.exists():
                    continue
                try:
                    _sync_by_path(session, model_file, load_json(model_file))
                except Exception:
                    session.rollback()
                    logger.exception("Failed to sync model metadata: %s", model_file)

        session.commit()


def initialize_metadata_database(sync_files: bool = True) -> None:
    if not settings.METADATA_DB_ENABLED:
        logger.info("Metadata DB disabled, skip metadata database initialization.")
        return

    try:
        Base.metadata.create_all(bind=engine)
        migrate_metadata_database_schema()
        if sync_files:
            sync_all_metadata_from_files()
            logger.info("Metadata database initialized and synced.")
        else:
            logger.info("Metadata database initialized without file sync.")
    except Exception as exc:
        logger.exception("Failed to initialize metadata database: %s", exc)
        if settings.METADATA_DB_STRICT:
            raise
