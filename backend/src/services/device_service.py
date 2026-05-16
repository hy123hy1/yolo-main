import asyncio
from pathlib import Path

from src.core.settings import settings
from src.services.metadata_db_sync import delete_json_document
from src.services.platform_storage import generate_id, list_json_items, load_json, now_iso, save_json


class DeviceService:
    def __init__(self):
        self.devices_dir: Path = settings.DEVICES_DIR

    def _get_path(self, device_id: str) -> Path:
        return self.devices_dir / f"{device_id}.json"

    async def list_devices(self, key: str | None = None, type_filter: str | None = None):
        def _list():
            items = list_json_items(self.devices_dir)
            if key:
                items[:] = [item for item in items if key.lower() in str(item).lower()]
            if type_filter:
                items[:] = [item for item in items if item.get("type") == type_filter]
            return {"items": sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)}

        return await asyncio.to_thread(_list)

    async def get_device(self, device_id: str):
        path = self._get_path(device_id)
        if not await asyncio.to_thread(path.exists):
            return None
        return await asyncio.to_thread(load_json, path)

    async def create_device(self, request):
        def _create():
            device_id = generate_id("dev")
            item = {
                "id": device_id,
                "name": request.name,
                "type": request.type,
                "host": request.host,
                "username": request.username,
                "password": request.password,
                "remark": request.remark,
                "status": "offline",
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
            save_json(self._get_path(device_id), item)
            return item

        return await asyncio.to_thread(_create)

    async def update_device(self, device_id: str, request):
        path = self._get_path(device_id)
        if not await asyncio.to_thread(path.exists):
            return None

        def _update():
            item = load_json(path)
            for field in ["name", "type", "host", "username", "password", "remark", "status"]:
                value = getattr(request, field, None)
                if value is not None:
                    item[field] = value
            item["updated_at"] = now_iso()
            save_json(path, item)
            return item

        return await asyncio.to_thread(_update)

    async def delete_device(self, device_id: str):
        path = self._get_path(device_id)
        if not await asyncio.to_thread(path.exists):
            return None

        def _delete():
            item = load_json(path)
            path.unlink()
            delete_json_document(path, item.get("id"))
            return item

        return await asyncio.to_thread(_delete)
