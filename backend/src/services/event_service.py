import asyncio
import copy
import logging
from datetime import datetime
from pathlib import Path

from src.core.settings import settings
from src.db.database import SessionLocal
from src.db.models import ChannelMeta, EventMeta, ModelMetaRecord, RecordingMeta
from src.services.metadata_db_sync import delete_json_document
from src.services.platform_storage import (
    format_datetime,
    generate_id,
    list_json_items,
    load_json,
    now_iso,
    save_json,
)

logger = logging.getLogger(__name__)


def _clean_text(value) -> str:
    return str(value or "").strip()


class EventService:
    def __init__(self):
        self.events_dir: Path = settings.EVENTS_DIR
        self.recordings_dir: Path = settings.RECORDINGS_DIR

    def _get_path(self, event_id: str) -> Path:
        return self.events_dir / f"{event_id}.json"

    def _get_recording_path(self, recording_id: str) -> Path:
        return self.recordings_dir / f"{recording_id}.json"

    @staticmethod
    def _algorithm_display_name(model_name: str | None, model_id: str | None) -> str:
        label = _clean_text(model_name) or _clean_text(model_id) or "未知算法"
        if "算法" in label:
            return label
        return f"{label}"

    @classmethod
    def _build_event_content(
        cls,
        item: dict,
        *,
        channel_name: str | None = None,
        model_name: str | None = None,
    ) -> str:
        explicit_content = _clean_text(item.get("content"))
        if explicit_content:
            return explicit_content

        created_at = _clean_text(item.get("created_at")) or format_datetime(datetime.now())
        channel_label = _clean_text(channel_name) or _clean_text(item.get("channel_id")) or "未知通道"
        algorithm_label = cls._algorithm_display_name(model_name, item.get("model_id"))
        return f"{created_at}，{channel_label}检测到{algorithm_label}，请核实处理"

    def _lookup_event_display_names(self, session, item: dict) -> tuple[str | None, str | None]:
        channel_name = None
        model_name = None
        channel_id = _clean_text(item.get("channel_id"))
        model_id = _clean_text(item.get("model_id"))

        try:
            if channel_id:
                row = session.query(ChannelMeta.name).filter(ChannelMeta.id == channel_id).one_or_none()
                if row:
                    channel_name = row[0]
            if model_id:
                row = session.query(ModelMetaRecord.name).filter(ModelMetaRecord.id == model_id).one_or_none()
                if row:
                    model_name = row[0]
        except Exception:
            logger.warning(
                "Failed to resolve event content names channel_id=%s model_id=%s",
                channel_id,
                model_id,
                exc_info=True,
            )

        return channel_name, model_name

    @staticmethod
    def _build_event_item(payload: dict, *, event_id: str | None = None, created_at: datetime | None = None) -> dict:
        now_dt = created_at or datetime.now()
        return {
            "id": event_id or generate_id("evt"),
            "channel_id": payload.get("channel_id"),
            "task_id": payload.get("task_id"),
            "algorithm_id": payload.get("algorithm_id"),
            "model_id": payload.get("model_id"),
            "event_type": payload.get("event_type", "detection"),
            "class_name": payload.get("class_name", "unknown"),
            "conf": payload.get("conf", 0),
            "snapshot_path": payload.get("snapshot_path"),
            "video_path": payload.get("video_path"),
            "content": payload.get("content"),
            "handling_status": payload.get("handling_status", 0),
            "remark": payload.get("remark"),
            "status": payload.get("status", "new"),
            "created_at": format_datetime(now_dt),
            "updated_at": format_datetime(now_dt),
        }

    def _event_from_meta(self, row: EventMeta) -> dict:
        return {
            "id": row.id,
            "channel_id": row.channel_id,
            "task_id": row.task_id,
            "algorithm_id": row.algorithm_id,
            "model_id": row.model_id,
            "event_type": row.event_type,
            "class_name": row.class_name,
            "conf": row.conf,
            "snapshot_path": row.snapshot_path,
            "video_path": row.video_path,
            "content": row.content,
            "handling_status": row.handling_status,
            "remark": row.remark,
            "status": row.status,
            "created_at": format_datetime(row.created_at),
            "updated_at": format_datetime(row.updated_at),
            "raw_json": row.raw_json,
        }

    def _event_payload_from_meta(self, row: EventMeta) -> dict:
        payload = copy.deepcopy(row.raw_json or {})
        payload.update({
            "id": row.id,
            "channel_id": row.channel_id,
            "task_id": row.task_id,
            "algorithm_id": row.algorithm_id,
            "model_id": row.model_id,
            "event_type": row.event_type,
            "class_name": row.class_name,
            "conf": row.conf,
            "snapshot_path": row.snapshot_path,
            "video_path": row.video_path,
            "content": row.content,
            "handling_status": row.handling_status,
            "remark": row.remark,
            "status": row.status,
            "created_at": format_datetime(row.created_at),
            "updated_at": format_datetime(row.updated_at),
        })
        return payload

    def _resolve_data_file(self, path_value: str | None) -> Path | None:
        if not path_value:
            return None

        normalized = str(path_value).replace("\\", "/")
        if normalized.startswith("/static/"):
            candidate = settings.DATA_DIR / normalized.replace("/static/", "", 1)
            return candidate if candidate.exists() else None

        candidates = [Path(normalized)]
        if normalized.startswith("/app/data/"):
            relative = normalized.replace("/app/data/", "", 1)
            candidates.insert(0, settings.DATA_DIR / relative)
        else:
            candidates.append(settings.DATA_DIR / normalized.lstrip("/"))
            candidates.append(settings.DATA_DIR / "snapshots" / Path(normalized).name)
            candidates.append(settings.DATA_DIR / "recordings" / Path(normalized).name)

        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                if resolved.exists():
                    return resolved
            except Exception:
                continue
        return None

    def _recording_exists(self, recording_id: str) -> bool:
        if settings.METADATA_DB_ENABLED:
            with SessionLocal() as session:
                row = session.query(RecordingMeta.id).filter(RecordingMeta.id == recording_id).one_or_none()
            return row is not None

        recording_path = self._get_recording_path(recording_id)
        if recording_path.exists():
            return True
        with SessionLocal() as session:
            row = session.query(RecordingMeta.id).filter(RecordingMeta.id == recording_id).one_or_none()
        return row is not None

    async def list_events(
        self,
        channel_id: str | None = None,
        task_id: str | None = None,
        algorithm_id: str | None = None,
        class_name: str | None = None,
        page: int = 1,
        page_size: int = 10,
    ):
        safe_page = max(page, 1)
        safe_page_size = min(max(page_size, 1), 100)

        def _list_from_metadata():
            with SessionLocal() as session:
                query = session.query(EventMeta)
                if channel_id:
                    query = query.filter(EventMeta.channel_id == channel_id)
                if task_id:
                    query = query.filter(EventMeta.task_id == task_id)
                if algorithm_id:
                    query = query.filter(EventMeta.algorithm_id == algorithm_id)
                if class_name:
                    query = query.filter(EventMeta.class_name == class_name)

                total = query.count()
                rows = (
                    query.order_by(EventMeta.created_at.desc(), EventMeta.id.desc())
                    .offset((safe_page - 1) * safe_page_size)
                    .limit(safe_page_size)
                    .all()
                )

            return {
                "items": [self._event_from_meta(row) for row in rows],
                "total": total,
                "page": safe_page,
                "page_size": safe_page_size,
            }

        metadata_result = await asyncio.to_thread(_list_from_metadata)
        if settings.METADATA_DB_ENABLED:
            return metadata_result

        def _list():
            items = list_json_items(self.events_dir)
            if channel_id:
                items[:] = [item for item in items if item.get("channel_id") == channel_id]
            if task_id:
                items[:] = [item for item in items if item.get("task_id") == task_id]
            if algorithm_id:
                items[:] = [item for item in items if item.get("algorithm_id") == algorithm_id]
            if class_name:
                items[:] = [item for item in items if item.get("class_name") == class_name]
            items = sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)
            total = len(items)
            start = (safe_page - 1) * safe_page_size
            end = start + safe_page_size
            return {
                "items": items[start:end],
                "total": total,
                "page": safe_page,
                "page_size": safe_page_size,
            }

        return await asyncio.to_thread(_list)

    async def get_event(self, event_id: str):
        def _get_from_metadata():
            with SessionLocal() as session:
                row = session.query(EventMeta).filter(EventMeta.id == event_id).one_or_none()
            if not row:
                return None
            return self._event_from_meta(row)

        metadata_item = await asyncio.to_thread(_get_from_metadata)
        if settings.METADATA_DB_ENABLED or metadata_item is not None:
            return metadata_item

        path = self._get_path(event_id)
        if not await asyncio.to_thread(path.exists):
            return None
        return await asyncio.to_thread(load_json, path)

    async def create_event(self, payload: dict):
        def _create_to_json():
            item = self._build_event_item(payload)
            item["content"] = self._build_event_content(item)
            save_json(self._get_path(item["id"]), item, sync_metadata=False)
            return item

        if settings.METADATA_DB_ENABLED:
            def _create_in_metadata():
                now_dt = datetime.now()
                item = self._build_event_item(
                    payload,
                    created_at=now_dt,
                )
                with SessionLocal() as session:
                    channel_name, model_name = self._lookup_event_display_names(session, item)
                    item["content"] = self._build_event_content(
                        item,
                        channel_name=channel_name,
                        model_name=model_name,
                    )
                    session.add(
                        EventMeta(
                            id=item["id"],
                            channel_id=item["channel_id"],
                            task_id=item["task_id"],
                            algorithm_id=item["algorithm_id"],
                            model_id=item["model_id"],
                            event_type=item["event_type"],
                            class_name=item["class_name"],
                            conf=item["conf"],
                            snapshot_path=item["snapshot_path"],
                            video_path=item["video_path"],
                            content=item["content"],
                            handling_status=item["handling_status"],
                            remark=item["remark"],
                            status=item["status"],
                            created_at=now_dt,
                            updated_at=now_dt,
                            raw_json=copy.deepcopy(item),
                        )
                    )
                    session.commit()
                return item

            try:
                return await asyncio.to_thread(_create_in_metadata)
            except Exception:
                if settings.METADATA_DB_STRICT:
                    raise
                logger.exception("Failed to create event in metadata DB, falling back to JSON cache")

        return await asyncio.to_thread(_create_to_json)

    def update_event_sync(self, event_id: str, request):
        if settings.METADATA_DB_ENABLED:
            try:
                with SessionLocal() as session:
                    row = session.query(EventMeta).filter(EventMeta.id == event_id).one_or_none()
                    if row:
                        for field in [
                            "event_type",
                            "class_name",
                            "conf",
                            "snapshot_path",
                            "video_path",
                            "handling_status",
                            "remark",
                            "status",
                        ]:
                            value = getattr(request, field, None)
                            if value is not None:
                                setattr(row, field, value)
                        row.updated_at = datetime.now()
                        row.raw_json = self._event_payload_from_meta(row)
                        session.commit()
                        session.refresh(row)
                        return self._event_from_meta(row)
            except Exception:
                if settings.METADATA_DB_STRICT:
                    raise
                logger.exception("Failed to update event in metadata DB, falling back to JSON cache: %s", event_id)

        path = self._get_path(event_id)
        if not path.exists():
            return None
        item = load_json(path)
        for field in [
            "event_type",
            "class_name",
            "conf",
            "snapshot_path",
            "video_path",
            "handling_status",
            "remark",
            "status",
        ]:
            value = getattr(request, field, None)
            if value is not None:
                item[field] = value
        item["updated_at"] = now_iso()
        save_json(path, item, sync_metadata=False)
        return item

    async def update_event(self, event_id: str, request):
        return await asyncio.to_thread(self.update_event_sync, event_id, request)

    async def delete_event(self, event_id: str):
        item = await self.get_event(event_id)
        if not item:
            return None

        if settings.METADATA_DB_ENABLED:
            def _delete_from_metadata():
                with SessionLocal() as session:
                    session.query(EventMeta).filter(EventMeta.id == event_id).delete()
                    session.commit()
                path = self._get_path(event_id)
                if path.exists():
                    path.unlink()
                return item

            return await asyncio.to_thread(_delete_from_metadata)

        def _delete():
            path = self._get_path(event_id)
            if path.exists():
                path.unlink()
            delete_json_document(path, event_id)
            return item

        return await asyncio.to_thread(_delete)

    async def delete_event_media(self, event_id: str) -> dict | None:
        event = await self.get_event(event_id)
        if not event:
            return None

        if settings.METADATA_DB_ENABLED:
            def _delete_media_from_metadata() -> dict:
                snapshot_file = self._resolve_data_file(event.get("snapshot_path"))
                video_file = self._resolve_data_file(event.get("video_path"))
                deleted_snapshot = False
                deleted_video = False

                if snapshot_file and snapshot_file.exists():
                    snapshot_file.unlink(missing_ok=True)
                    deleted_snapshot = True

                if video_file and video_file.exists():
                    video_file.unlink(missing_ok=True)
                    deleted_video = True

                deleted_recording = False
                with SessionLocal() as session:
                    recording_row = session.query(RecordingMeta).filter(RecordingMeta.id == event_id).one_or_none()
                    if recording_row:
                        session.delete(recording_row)
                        deleted_recording = True

                    row = session.query(EventMeta).filter(EventMeta.id == event_id).one_or_none()
                    if not row:
                        return None
                    row.snapshot_path = None
                    row.video_path = None
                    row.updated_at = datetime.now()
                    row.raw_json = self._event_payload_from_meta(row)
                    session.commit()
                    session.refresh(row)
                    updated_event = self._event_from_meta(row)

                recording_path = self._get_recording_path(event_id)
                if recording_path.exists():
                    recording_path.unlink(missing_ok=True)
                    deleted_recording = True

                event_path = self._get_path(event_id)
                if event_path.exists():
                    event_path.unlink(missing_ok=True)

                return {
                    "id": event_id,
                    "deleted_snapshot": deleted_snapshot,
                    "deleted_video": deleted_video,
                    "deleted_recording": deleted_recording,
                    "event": updated_event,
                }

            return await asyncio.to_thread(_delete_media_from_metadata)

        def _delete_media() -> dict:
            snapshot_file = self._resolve_data_file(event.get("snapshot_path"))
            video_file = self._resolve_data_file(event.get("video_path"))
            deleted_snapshot = False
            deleted_video = False

            if snapshot_file and snapshot_file.exists():
                snapshot_file.unlink(missing_ok=True)
                deleted_snapshot = True

            if video_file and video_file.exists():
                video_file.unlink(missing_ok=True)
                deleted_video = True

            deleted_recording = False
            if self._recording_exists(event_id):
                recording_path = self._get_recording_path(event_id)
                if recording_path.exists():
                    recording_path.unlink(missing_ok=True)
                delete_json_document(recording_path, event_id)
                deleted_recording = True

            updated_event = dict(event)
            updated_event["snapshot_path"] = None
            updated_event["video_path"] = None
            updated_event["updated_at"] = now_iso()
            save_json(self._get_path(event_id), updated_event)
            return {
                "id": event_id,
                "deleted_snapshot": deleted_snapshot,
                "deleted_video": deleted_video,
                "deleted_recording": deleted_recording,
                "event": updated_event,
            }

        return await asyncio.to_thread(_delete_media)
