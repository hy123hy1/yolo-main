import asyncio
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src.core.settings import settings
from src.db.database import SessionLocal
from src.db.models import RecordingMeta
from src.services.platform_storage import format_datetime, generate_id, list_json_items, load_json, now_iso, save_json


class RecordingService:
    def __init__(self):
        self.recordings_dir: Path = settings.RECORDINGS_DIR

    def _get_path(self, recording_id: str) -> Path:
        return self.recordings_dir / f"{recording_id}.json"

    def _recording_from_meta(self, row: RecordingMeta) -> dict:
        payload = row.raw_json or {}
        raw_json = payload.get("raw_json") if isinstance(payload.get("raw_json"), dict) else payload
        return {
            "id": row.id,
            "channel_id": row.channel_id,
            "task_id": row.task_id,
            "algorithm_id": row.algorithm_id,
            "started_at": format_datetime(row.started_at),
            "ended_at": format_datetime(row.ended_at),
            "duration": row.duration,
            "file_path": row.file_path,
            "status": row.status,
            "created_at": format_datetime(row.created_at),
            "updated_at": format_datetime(row.updated_at),
            "raw_json": raw_json,
        }

    def _resolve_static_url(self, file_path: str | None) -> str | None:
        if not file_path:
            return None

        normalized = str(file_path).replace("\\", "/")
        if normalized.startswith("/static/"):
            return normalized

        candidates = [
            settings.DATA_DIR / normalized.lstrip("/"),
            settings.DATA_DIR / "recordings" / Path(normalized).name,
            settings.DATA_DIR / "snapshots" / Path(normalized).name,
        ]

        if normalized.startswith("/app/data/"):
            relative = normalized.replace("/app/data/", "", 1)
            candidates.insert(0, settings.DATA_DIR / relative)

        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                if resolved.exists():
                    relative = resolved.relative_to(settings.DATA_DIR.resolve()).as_posix()
                    return f"/static/{relative}"
            except Exception:
                continue
        return None

    def _decorate_item(self, item: dict) -> dict:
        next_item = dict(item)
        file_path = next_item.get("file_path")
        next_item["video_url"] = self._resolve_static_url(file_path)
        raw_json = next_item.get("raw_json") or {}
        next_item["event_id"] = raw_json.get("event_id")
        next_item["event_type"] = raw_json.get("event_type") or next_item.get("status")
        next_item["event_type_label"] = raw_json.get("event_type_label")
        next_item["class_name"] = raw_json.get("class_name")
        next_item["conf"] = raw_json.get("conf")
        snapshot_path = raw_json.get("snapshot_path")
        next_item["snapshot_url"] = self._resolve_static_url(snapshot_path)
        return next_item

    def _normalize_recording(self, payload: dict) -> dict:
        item = {
            "id": payload.get("id") or generate_id("rec"),
            "channel_id": payload.get("channel_id"),
            "task_id": payload.get("task_id"),
            "algorithm_id": payload.get("algorithm_id"),
            "started_at": payload.get("started_at"),
            "ended_at": payload.get("ended_at"),
            "duration": payload.get("duration"),
            "file_path": payload.get("file_path"),
            "status": payload.get("status", "ready"),
            "created_at": payload.get("created_at") or now_iso(),
            "updated_at": payload.get("updated_at") or now_iso(),
            "raw_json": payload.get("raw_json") or {},
        }
        if payload.get("event_id"):
            item["raw_json"] = {**item["raw_json"], "event_id": payload.get("event_id")}
        return item

    def create_recording_sync(self, payload: dict) -> dict:
        item = self._normalize_recording(payload)
        if settings.METADATA_DB_ENABLED:
            started_at = self._parse_dt(item.get("started_at"))
            ended_at = self._parse_dt(item.get("ended_at"))
            created_at = self._parse_dt(item.get("created_at")) or datetime.now()
            updated_at = self._parse_dt(item.get("updated_at")) or created_at
            with SessionLocal() as session:
                row = session.query(RecordingMeta).filter(RecordingMeta.id == item["id"]).one_or_none()
                if row is None:
                    row = RecordingMeta(id=item["id"])
                    session.add(row)
                row.channel_id = item.get("channel_id")
                row.task_id = item.get("task_id")
                row.algorithm_id = item.get("algorithm_id")
                row.started_at = started_at
                row.ended_at = ended_at
                row.duration = item.get("duration")
                row.file_path = item.get("file_path")
                row.status = item.get("status")
                row.created_at = created_at
                row.updated_at = updated_at
                row.raw_json = dict(item)
                session.commit()
        save_json(self._get_path(item["id"]), item)
        return item

    def update_recording_sync(self, recording_id: str, payload: dict) -> dict | None:
        path = self._get_path(recording_id)
        if not path.exists() and not settings.METADATA_DB_ENABLED:
            return None
        current = load_json(path) if path.exists() else {"id": recording_id}
        current.update(payload)
        current["updated_at"] = now_iso()
        if settings.METADATA_DB_ENABLED:
            started_at = self._parse_dt(current.get("started_at"))
            ended_at = self._parse_dt(current.get("ended_at"))
            created_at = self._parse_dt(current.get("created_at")) or datetime.now()
            updated_at = self._parse_dt(current.get("updated_at")) or datetime.now()
            with SessionLocal() as session:
                row = session.query(RecordingMeta).filter(RecordingMeta.id == recording_id).one_or_none()
                if row is None:
                    row = RecordingMeta(id=recording_id)
                    session.add(row)
                row.channel_id = current.get("channel_id")
                row.task_id = current.get("task_id")
                row.algorithm_id = current.get("algorithm_id")
                row.started_at = started_at
                row.ended_at = ended_at
                row.duration = current.get("duration")
                row.file_path = current.get("file_path")
                row.status = current.get("status")
                row.created_at = created_at
                row.updated_at = updated_at
                row.raw_json = dict(current)
                session.commit()
        save_json(path, current)
        return current

    @staticmethod
    def _parse_dt(value: str | None):
        if not value or not isinstance(value, str):
            return None
        candidate = value.strip().replace("Z", "+00:00")
        if not candidate:
            return None
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            return None

    async def create_recording(self, payload: dict):
        return await asyncio.to_thread(self.create_recording_sync, payload)

    async def update_recording(self, recording_id: str, payload: dict):
        return await asyncio.to_thread(self.update_recording_sync, recording_id, payload)

    async def list_recordings(
        self,
        channel_id: str | None = None,
        task_id: str | None = None,
        algorithm_id: str | None = None,
        page: int = 1,
        page_size: int = 10,
    ):
        safe_page = max(page, 1)
        safe_page_size = min(max(page_size, 1), 100)

        def _list_from_metadata():
            with SessionLocal() as session:
                query = session.query(RecordingMeta)
                if channel_id:
                    query = query.filter(RecordingMeta.channel_id == channel_id)
                if task_id:
                    query = query.filter(RecordingMeta.task_id == task_id)
                if algorithm_id:
                    query = query.filter(RecordingMeta.algorithm_id == algorithm_id)

                total = query.count()
                rows = (
                    query.order_by(RecordingMeta.started_at.desc(), RecordingMeta.id.desc())
                    .offset((safe_page - 1) * safe_page_size)
                    .limit(safe_page_size)
                    .all()
                )

            if total == 0:
                return None

            return {
                "items": [self._decorate_item(self._recording_from_meta(row)) for row in rows],
                "total": total,
                "page": safe_page,
                "page_size": safe_page_size,
            }

        metadata_result = await asyncio.to_thread(_list_from_metadata)
        if metadata_result is not None:
            return metadata_result

        def _list():
            items = list_json_items(self.recordings_dir)
            if channel_id:
                items[:] = [item for item in items if item.get("channel_id") == channel_id]
            if task_id:
                items[:] = [item for item in items if item.get("task_id") == task_id]
            if algorithm_id:
                items[:] = [item for item in items if item.get("algorithm_id") == algorithm_id]
            items = sorted(items, key=lambda x: x.get("started_at", ""), reverse=True)
            total = len(items)
            start = (safe_page - 1) * safe_page_size
            end = start + safe_page_size
            return {
                "items": [self._decorate_item(item) for item in items[start:end]],
                "total": total,
                "page": safe_page,
                "page_size": safe_page_size,
            }

        return await asyncio.to_thread(_list)

    async def get_recording(self, recording_id: str):
        if settings.METADATA_DB_ENABLED:
            def _get_from_metadata():
                with SessionLocal() as session:
                    row = session.query(RecordingMeta).filter(RecordingMeta.id == recording_id).one_or_none()
                if not row:
                    return None
                return self._decorate_item(self._recording_from_meta(row))

            return await asyncio.to_thread(_get_from_metadata)

        path = self._get_path(recording_id)
        if not await asyncio.to_thread(path.exists):
            return None
        return self._decorate_item(await asyncio.to_thread(load_json, path))

    async def timeline(self, channel_id: str | None = None):
        data = await self.list_recordings(channel_id=channel_id, page=1, page_size=10000)
        return {
            "items": [
                {
                    "id": item.get("id"),
                    "channel_id": item.get("channel_id"),
                    "started_at": item.get("started_at"),
                    "ended_at": item.get("ended_at"),
                    "duration": item.get("duration"),
                }
                for item in data["items"]
            ]
        }

    async def monthly(self, channel_id: str | None = None):
        data = await self.list_recordings(channel_id=channel_id, page=1, page_size=10000)

        def _monthly():
            stats: dict[str, int] = defaultdict(int)
            for item in data["items"]:
                started_at = item.get("started_at")
                if not started_at:
                    continue
                try:
                    month_key = datetime.fromisoformat(started_at).strftime("%Y-%m")
                except ValueError:
                    continue
                stats[month_key] += 1
            return {"items": [{"month": key, "count": value} for key, value in sorted(stats.items())]}

        return await asyncio.to_thread(_monthly)
