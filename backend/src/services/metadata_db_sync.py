from __future__ import annotations

import copy
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from src.core.settings import settings
from src.db.database import SessionLocal, engine
from src.db.models import (
    Base,
    ChannelAIConfigMeta,
    ChannelMeta,
    ChannelZoneMeta,
    DeviceMeta,
    EventMeta,
    MediaNodeMeta,
    ModelMetaRecord,
    RecordingMeta,
)

logger = logging.getLogger(__name__)


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
            ai_enabled=payload.get("ai_enabled"),
            record_mode=payload.get("record_mode"),
            snapshot_url=payload.get("snapshot_url"),
            created_at=_parse_dt(payload.get("created_at")),
            updated_at=_parse_dt(payload.get("updated_at")),
            raw_json=copy.deepcopy(payload),
        ),
    )

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

    config = payload.get("ext", {}).get("ai_config") or {}
    _upsert(
        session,
        ChannelAIConfigMeta(
            channel_id=payload["id"],
            enabled=config.get("enabled"),
            model_id=config.get("model_id"),
            labels_json=copy.deepcopy(config.get("labels") or []),
            conf=config.get("conf"),
            frame_interval=config.get("frame_interval"),
            zone_ids_json=copy.deepcopy(config.get("zone_ids") or []),
            save_snapshot=config.get("save_snapshot"),
            raw_json=copy.deepcopy(config),
        ),
    )


def _sync_event(session: Session, payload: dict) -> None:
    _upsert(
        session,
        EventMeta(
            id=payload["id"],
            channel_id=payload.get("channel_id"),
            model_id=payload.get("model_id"),
            event_type=payload.get("event_type"),
            class_name=payload.get("class_name"),
            conf=payload.get("conf"),
            snapshot_path=payload.get("snapshot_path"),
            video_path=payload.get("video_path"),
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
            source=payload.get("source"),
            description=payload.get("description"),
            tags_json=copy.deepcopy(payload.get("tags") or []),
            classes_json=copy.deepcopy(payload.get("classes") or []),
            weights_path=payload.get("weights_path"),
            file_size=payload.get("file_size"),
            file_size_mb=payload.get("file_size_mb"),
            job_id=payload.get("job_id"),
            created_at=_parse_dt(payload.get("created_at")),
            updated_at=_parse_dt(payload.get("updated_at")),
            raw_json=copy.deepcopy(payload),
        ),
    )


def _sync_by_path(session: Session, path: Path, payload: dict) -> None:
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
    elif path.name == "model.json" and settings.REGISTRY_DIR in path.parents:
        _sync_model(session, payload)


def sync_json_document(path: Path, payload: dict) -> None:
    if not settings.METADATA_DB_ENABLED:
        return

    with SessionLocal() as session:
        _sync_by_path(session, path, payload)
        session.commit()


def delete_json_document(path: Path, item_id: str | None) -> None:
    if not settings.METADATA_DB_ENABLED or not item_id:
        return

    with SessionLocal() as session:
        if path.parent == settings.CHANNELS_DIR:
            session.query(ChannelAIConfigMeta).filter(ChannelAIConfigMeta.channel_id == item_id).delete()
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
        elif path.name == "model.json":
            session.query(ModelMetaRecord).filter(ModelMetaRecord.id == item_id).delete()
        session.commit()


def sync_all_metadata_from_files() -> None:
    from src.services.platform_storage import load_json

    with SessionLocal() as session:
        for directory in [
            settings.DEVICES_DIR,
            settings.MEDIA_NODES_DIR,
            settings.CHANNELS_DIR,
            settings.EVENTS_DIR,
            settings.RECORDINGS_DIR,
        ]:
            if not directory.exists():
                continue
            for path in directory.glob("*.json"):
                try:
                    _sync_by_path(session, path, load_json(path))
                except Exception:
                    logger.exception("Failed to sync metadata file: %s", path)

        if settings.REGISTRY_DIR.exists():
            for model_dir in settings.REGISTRY_DIR.iterdir():
                model_file = model_dir / "model.json"
                if not model_file.exists():
                    continue
                try:
                    _sync_by_path(session, model_file, load_json(model_file))
                except Exception:
                    logger.exception("Failed to sync model metadata: %s", model_file)

        session.commit()


def initialize_metadata_database() -> None:
    if not settings.METADATA_DB_ENABLED:
        logger.info("Metadata DB disabled, skip metadata database initialization.")
        return

    try:
        Base.metadata.create_all(bind=engine)
        sync_all_metadata_from_files()
        logger.info("Metadata database initialized and synced.")
    except Exception as exc:
        logger.exception("Failed to initialize metadata database: %s", exc)
        if settings.METADATA_DB_STRICT:
            raise
