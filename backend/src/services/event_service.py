import asyncio
from pathlib import Path

from src.core.settings import settings
from src.services.metadata_db_sync import delete_json_document
from src.services.platform_storage import generate_id, list_json_items, load_json, now_iso, save_json


class EventService:
    def __init__(self):
        self.events_dir: Path = settings.EVENTS_DIR

    def _get_path(self, event_id: str) -> Path:
        return self.events_dir / f"{event_id}.json"

    async def list_events(
        self,
        channel_id: str | None = None,
        class_name: str | None = None,
        page: int = 1,
        page_size: int = 10,
    ):
        def _list():
            items = list_json_items(self.events_dir)
            if channel_id:
                items[:] = [item for item in items if item.get("channel_id") == channel_id]
            if class_name:
                items[:] = [item for item in items if item.get("class_name") == class_name]
            items = sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)
            total = len(items)
            safe_page = max(page, 1)
            safe_page_size = min(max(page_size, 1), 100)
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
        path = self._get_path(event_id)
        if not await asyncio.to_thread(path.exists):
            return None
        return await asyncio.to_thread(load_json, path)

    async def create_event(self, payload: dict):
        def _create():
            event_id = generate_id("evt")
            item = {
                "id": event_id,
                "channel_id": payload.get("channel_id"),
                "model_id": payload.get("model_id"),
                "event_type": payload.get("event_type", "detection"),
                "class_name": payload.get("class_name", "unknown"),
                "conf": payload.get("conf", 0),
                "snapshot_path": payload.get("snapshot_path"),
                "video_path": payload.get("video_path"),
                "remark": payload.get("remark"),
                "status": payload.get("status", "new"),
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
            save_json(self._get_path(event_id), item)
            return item

        return await asyncio.to_thread(_create)

    async def update_event(self, event_id: str, request):
        path = self._get_path(event_id)
        if not await asyncio.to_thread(path.exists):
            return None

        def _update():
            item = load_json(path)
            for field in ["event_type", "class_name", "conf", "snapshot_path", "video_path", "remark", "status"]:
                value = getattr(request, field, None)
                if value is not None:
                    item[field] = value
            item["updated_at"] = now_iso()
            save_json(path, item)
            return item

        return await asyncio.to_thread(_update)

    async def delete_event(self, event_id: str):
        path = self._get_path(event_id)
        if not await asyncio.to_thread(path.exists):
            return None

        def _delete():
            item = load_json(path)
            path.unlink()
            delete_json_document(path, item.get("id"))
            return item

        return await asyncio.to_thread(_delete)
